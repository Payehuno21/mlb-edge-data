"""
MLB Edge — pipeline de datos diario
------------------------------------------------------------------
Inspirado en la arquitectura de:
  - gmalbert/baseball-predictions (ingesta MLB Stats API + features de equipo)
  - sebasclarkv/baseball-analyzer (probabilidades de bateo por jugador)

Este script corre en GitHub Actions (no en el navegador del usuario), así
que no tiene problemas de CORS: pega directo a la MLB Stats API, Open-Meteo
(clima) y The Odds API (momios).

Dos modos de corrida (--mode=full o --mode=refresh, default full):
  - full: trabajo completo — equipos, Elo, bateadores destacados, clima,
    abridores probables, momios. Corre una vez al día en la mañana.
  - refresh: solo vuelve a consultar abridor probable y momios (lo que más
    cambia durante el día — cambios de rotación, lesiones, movimiento de
    línea) reutilizando el resto de los datos pesados del data.json
    existente. Corre una segunda vez por la tarde, antes de los primeros
    juegos, para que el abridor y los momios no se queden desactualizados
    todo el día con el snapshot de la mañana.

Genera un único archivo data.json con calendario de hoy + mañana, equipos,
bateadores destacados, abridores con stats reales, clima por venue, momios
automáticos, y resultados finales de hoy/ayer para cotejo de bitácora.

La app web (React) consume este JSON directo desde GitHub raw,
sin necesidad de fetch en vivo ni proxies CORS.
"""

import json
import math
import os
import sys
import time
from datetime import date, timedelta, datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from model import find_best_bets, top_diverse_picks, edge_tier

STATS_BASE = "https://statsapi.mlb.com/api/v1"
WEATHER_BASE = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 12
MIN_PA_FOR_PROPS = 60  # mínimo de plate appearances para que la tasa de HR/AVG sea confiable

# Coordenadas de los 30 estadios de MLB (lat, lon) para consultar clima por venue.
# Necesario porque MLB Stats API no expone clima estructurado de forma confiable.
VENUE_COORDS = {
    "Chase Field": (33.4455, -112.0667),
    "Truist Park": (33.8908, -84.4678),
    "Oriole Park at Camden Yards": (39.2840, -76.6217),
    "Fenway Park": (42.3467, -71.0972),
    "Wrigley Field": (41.9484, -87.6553),
    "Guaranteed Rate Field": (41.8299, -87.6338),
    "Great American Ball Park": (39.0975, -84.5066),
    "Progressive Field": (41.4962, -81.6852),
    "Coors Field": (39.7559, -104.9942),
    "Comerica Park": (42.3390, -83.0485),
    "Minute Maid Park": (29.7572, -95.3551),
    "Kauffman Stadium": (39.0517, -94.4803),
    "Angel Stadium": (33.8003, -117.8827),
    "Dodger Stadium": (34.0739, -118.2400),
    "loanDepot park": (25.7781, -80.2196),
    "American Family Field": (43.0280, -87.9712),
    "Target Field": (44.9817, -93.2776),
    "Citi Field": (40.7571, -73.8458),
    "Yankee Stadium": (40.8296, -73.9262),
    "Oakland Coliseum": (37.7516, -122.2005),
    "Sutter Health Park": (38.5805, -121.5136),
    "Citizens Bank Park": (39.9061, -75.1665),
    "PNC Park": (40.4469, -80.0057),
    "Petco Park": (32.7073, -117.1566),
    "Oracle Park": (37.7786, -122.3893),
    "T-Mobile Park": (47.5914, -122.3325),
    "Busch Stadium": (38.6226, -90.1928),
    "Tropicana Field": (27.7683, -82.6534),
    "Globe Life Field": (32.7473, -97.0832),
    "Rogers Centre": (43.6414, -79.3894),
    "Nationals Park": (38.8730, -77.0074),
}


def get_json(url, retries=3, backoff=2.0):
    """GET con reintentos. Servidor de GitHub Actions sí tiene salida a internet libre."""
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "mlb-edge-pipeline/1.0"})
            with urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, TimeoutError) as e:
            last_err = e
            time.sleep(backoff * (attempt + 1))
    print(f"WARN: fallo definitivo en {url}: {last_err}", file=sys.stderr)
    return None


def debug_print_situation_codes():
    """DIAGNÓSTICO TEMPORAL — confirma los valores reales de sitCodes que usa
    MLB Stats API para splits vs. zurdo/derecho, antes de construir el
    cálculo de splits del abridor. Solo imprime al log, no afecta data.json.
    Quitar esta llamada una vez confirmado el código correcto.
    """
    data = get_json(f"{STATS_BASE}/situationCodes")
    if not data:
        print("DEBUG situationCodes: la consulta no devolvió nada (endpoint puede no existir en /v1 directo).")
        return
    codes = data.get("situationCodes", data) if isinstance(data, dict) else data
    print(f"DEBUG situationCodes: {len(codes)} código(s) totales encontrados.")
    keywords = ["hand", "left", "right", "vs.", "vs ", "lhp", "rhp", "vl", "vr"]
    relevant = [
        c for c in codes
        if any(kw in (c.get("description", "") + c.get("code", "")).lower() for kw in keywords)
    ]
    print(f"DEBUG situationCodes relacionados con zurdo/derecho: {json.dumps(relevant, ensure_ascii=False)}")


def debug_print_schedule_hydrations():
    """DIAGNÓSTICO TEMPORAL — lista las hidrataciones disponibles para el
    endpoint /schedule, para confirmar con certeza el nombre correcto de la
    hidratación de lineups confirmados (en vez de adivinar 'lineups').
    """
    data = get_json(f"{STATS_BASE}/schedule?sportId=1&hydrate=hydrations")
    if not data:
        print("DEBUG hydrations: la consulta no devolvió nada.")
        return
    print(f"DEBUG hydrations disponibles para /schedule: {json.dumps(data, ensure_ascii=False)[:3000]}")


def fetch_weather_for_venue(venue_name, game_date_iso):
    """Clima por hora del juego, vía Open-Meteo (gratis, sin API key).
    Devuelve None si el venue no está en el diccionario o falla la consulta —
    el modelo debe tratar la ausencia de clima como neutral, no como error fatal.
    """
    coords = VENUE_COORDS.get(venue_name)
    if not coords:
        return None
    lat, lon = coords
    try:
        game_dt = game_date_iso[:10]  # YYYY-MM-DD
    except Exception:
        return None

    url = (
        f"{WEATHER_BASE}?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,precipitation_probability,wind_speed_10m,wind_direction_10m"
        f"&start_date={game_dt}&end_date={game_dt}&timezone=auto&temperature_unit=fahrenheit"
        f"&wind_speed_unit=mph"
    )
    data = get_json(url, retries=2)
    if not data or "hourly" not in data:
        return None

    # Tomamos la hora más cercana a las 19:00 local (hora típica de primer pitch)
    # como aproximación razonable; no tenemos el horario exacto de cada juego aquí.
    try:
        times = data["hourly"]["time"]
        target_hour = f"{game_dt}T19:00"
        idx = times.index(target_hour) if target_hour in times else len(times) // 2
        return {
            "tempF": round(data["hourly"]["temperature_2m"][idx]),
            "precipProb": data["hourly"]["precipitation_probability"][idx],
            "windMph": round(data["hourly"]["wind_speed_10m"][idx], 1),
            "windDirDeg": data["hourly"]["wind_direction_10m"][idx],
        }
    except (KeyError, IndexError, ValueError):
        return None


def win_pct_to_elo(win_pct):
    if win_pct is None:
        return 1500
    return round(1500 + (win_pct - 0.5) * 600)


def fetch_standings():
    """Devuelve dict {teamId: {winPct, homeWinPct, awayWinPct, runsPerGame, staffEra}}."""
    data = get_json(f"{STATS_BASE}/standings?leagueId=103,104&season={date.today().year}")
    out = {}
    if not data:
        return out
    for record in data.get("records", []):
        for team_record in record.get("teamRecords", []):
            team_id = team_record["team"]["id"]
            wins = team_record.get("wins", 0)
            losses = team_record.get("losses", 0)
            games = wins + losses
            win_pct = wins / games if games else 0.5

            # splits home/away vienen en "records" -> "splitRecords"
            home_pct, away_pct = win_pct, win_pct
            for split in team_record.get("records", {}).get("splitRecords", []):
                if split.get("type") == "home" and split.get("wins") is not None:
                    g = split["wins"] + split["losses"]
                    home_pct = split["wins"] / g if g else win_pct
                if split.get("type") == "away" and split.get("wins") is not None:
                    g = split["wins"] + split["losses"]
                    away_pct = split["wins"] / g if g else win_pct

            # forma reciente (últimos 10)
            last10 = 5
            for split in team_record.get("records", {}).get("splitRecords", []):
                if split.get("type") == "lastTen" and split.get("wins") is not None:
                    last10 = split["wins"]

            out[team_id] = {
                "winPct": round(win_pct, 3),
                "homeWinPct": round(home_pct, 3),
                "awayWinPct": round(away_pct, 3),
                "last10": last10,
                "elo": win_pct_to_elo(win_pct),
            }
    return out


def fetch_team_run_and_staff_stats(team_id):
    data = get_json(f"{STATS_BASE}/teams/{team_id}/stats?stats=season&group=hitting,pitching")
    runs_per_game, staff_era = None, None
    if data:
        for block in data.get("stats", []):
            grp = block.get("group", {}).get("displayName")
            split = (block.get("splits") or [{}])[0].get("stat", {})
            if grp == "hitting":
                runs = split.get("runs")
                games = split.get("gamesPlayed")
                if runs and games:
                    runs_per_game = round(float(runs) / float(games), 2)
            if grp == "pitching":
                era = split.get("era")
                if era:
                    staff_era = round(float(era), 2)
    return runs_per_game, staff_era


BULLPEN_FATIGUE_LOOKBACK_DAYS = 3
BULLPEN_HEAVY_IP_THRESHOLD = 9.0  # innings de bullpen en 3 días que ya se consideran carga pesada
BULLPEN_FATIGUE_MAX_PENALTY = 0.30  # tope de aumento al ERA efectivo del bullpen (en puntos de ERA)


def fetch_boxscore(game_pk, boxscore_cache=None):
    if boxscore_cache is not None and game_pk in boxscore_cache:
        return boxscore_cache[game_pk]
    data = get_json(f"{STATS_BASE}/game/{game_pk}/boxscore")
    if boxscore_cache is not None:
        boxscore_cache[game_pk] = data
    return data


def innings_str_to_float(ip_str):
    """MLB representa innings parciales como '5.1' (= 5⅓) y '5.2' (= 5⅔),
    no como decimal real — hay que convertir el .1/.2 a fracción de tercio.
    """
    if not ip_str:
        return 0.0
    try:
        whole, _, frac = str(ip_str).partition(".")
        whole = int(whole)
        frac = int(frac) if frac else 0
        return whole + (frac / 3.0)
    except (ValueError, TypeError):
        return 0.0


def fetch_recent_bullpen_load(team_id, today, schedule_cache, boxscore_cache):
    """Suma innings lanzadas por el BULLPEN (no el abridor) del equipo en
    los últimos N días, recorriendo boxscores reales. Un bullpen que ya
    lanzó muchas entradas recientemente va a rendir peor hoy de lo que su
    ERA de temporada sugiere — esta señal no existe en runsPerGame/staffEra
    de temporada, que son promedios de todo el año.
    Devuelve (innings_recientes, penalty) donde penalty es el ajuste
    sugerido al ERA efectivo del bullpen para el cálculo del modelo.
    """
    total_bullpen_ip = 0.0
    for days_ago in range(1, BULLPEN_FATIGUE_LOOKBACK_DAYS + 1):
        day = (today - timedelta(days=days_ago)).isoformat()
        if day not in schedule_cache:
            schedule_cache[day] = fetch_schedule_with_matchups(day)
        games = schedule_cache[day]

        for g in games:
            home_id = g["teams"]["home"]["team"]["id"]
            away_id = g["teams"]["away"]["team"]["id"]
            if team_id not in (home_id, away_id):
                continue
            box = fetch_boxscore(g["gamePk"], boxscore_cache)
            if not box:
                continue
            side = "home" if team_id == home_id else "away"
            team_box = box.get("teams", {}).get(side, {})
            pitchers_ids = team_box.get("pitchers", [])
            players = team_box.get("players", {})
            if not pitchers_ids:
                continue
            # el primer id en "pitchers" es el abridor; el resto es bullpen
            for pid in pitchers_ids[1:]:
                p_stats = players.get(f"ID{pid}", {}).get("stats", {}).get("pitching", {})
                total_bullpen_ip += innings_str_to_float(p_stats.get("inningsPitched"))

    penalty = 0.0
    if total_bullpen_ip > BULLPEN_HEAVY_IP_THRESHOLD:
        excess = total_bullpen_ip - BULLPEN_HEAVY_IP_THRESHOLD
        penalty = min(excess * 0.04, BULLPEN_FATIGUE_MAX_PENALTY)
    return round(total_bullpen_ip, 1), round(penalty, 3)


def fetch_pitcher_stats(pitcher_id):
    data = get_json(f"{STATS_BASE}/people/{pitcher_id}/stats?stats=season&group=pitching")
    if not data:
        return None
    for block in data.get("stats", []):
        split = (block.get("splits") or [{}])[0].get("stat", {})
        if split:
            return {
                "era": round(float(split["era"]), 2) if split.get("era") else None,
                "whip": round(float(split["whip"]), 2) if split.get("whip") else None,
                "k9": round(float(split["strikeoutsPer9Inn"]), 1) if split.get("strikeoutsPer9Inn") else None,
            }
    return None


def fetch_team_hitters_stats(team_id):
    """Stats de bateo de temporada del roster activo completo (no solo el
    mejor de cada categoría). Devuelve dict {playerName: {avg, hrRate, pa}}
    para poder calcular probabilidad real de cualquier jugador que aparezca
    en una prop, no solo el destacado del equipo.
    """
    roster = get_json(f"{STATS_BASE}/teams/{team_id}/roster?rosterType=active")
    if not roster:
        return {}
    hitter_ids = [
        p["person"]["id"] for p in roster.get("roster", [])
        if p.get("position", {}).get("abbreviation") != "P"
    ]
    if not hitter_ids:
        return {}

    ids_param = ",".join(str(i) for i in hitter_ids)
    people = get_json(
        f"{STATS_BASE}/people?personIds={ids_param}&hydrate=stats(group=hitting,type=season)"
    )
    if not people:
        return {}

    by_name = {}
    for person in people.get("people", []):
        stats = person.get("stats", [])
        if not stats:
            continue
        splits = stats[0].get("splits", [])
        if not splits:
            continue
        stat = splits[0].get("stat", {})
        pa = int(stat.get("plateAppearances", 0) or 0)
        if pa < MIN_PA_FOR_PROPS:
            continue
        hr = int(stat.get("homeRuns", 0) or 0)
        hits = int(stat.get("hits", 0) or 0)
        avg = float(stat.get("avg", 0) or 0)
        by_name[person.get("fullName")] = {
            "avg": round(avg, 3),
            "hrRate": round(hr / pa, 4) if pa else 0,
            "hitRate": round(hits / pa, 4) if pa else 0,  # tasa de juegos con 1+ hit, aproximada por PA
            "pa": pa,
        }
    return by_name


MISSING_STARTER_PA_THRESHOLD = 250  # mínimo de PA en temporada para considerar a alguien "titular clave"
TOP_N_STARTERS_TO_CHECK = 5  # cuántos de los titulares con más PA se revisan por equipo


def detect_missing_starters(team_id, active_hitter_names):
    """Compara a los bateadores titulares de la TEMPORADA (sin filtrar por
    roster de hoy) contra el roster activo de hoy. Si alguno de los
    titulares con más plate appearances no aparece en el roster activo,
    es señal fuerte de ausencia (lesión, suspensión, etc.) que el promedio
    de temporada del equipo no refleja todavía.
    Devuelve una lista de nombres ausentes y una penalización sugerida
    (0.0 a 1.0, mayor = ausencia más significativa para la ofensiva).
    """
    # hydrate=person trae todos los jugadores con stats de bateo con este
    # equipo en la temporada, SIN filtrar por roster activo actual — esa es
    # la pieza clave que nos permite ver a alguien que ya no está activo.
    data = get_json(
        f"{STATS_BASE}/teams/{team_id}/roster?rosterType=40Man"
    )
    if not data:
        return [], 0.0
    full_org_ids = {p["person"]["id"]: p["person"]["fullName"] for p in data.get("roster", [])}
    if not full_org_ids:
        return [], 0.0

    ids_param = ",".join(str(i) for i in full_org_ids.keys())
    people = get_json(
        f"{STATS_BASE}/people?personIds={ids_param}&hydrate=stats(group=hitting,type=season)"
    )
    if not people:
        return [], 0.0

    season_starters = []  # [(name, pa)]
    for person in people.get("people", []):
        stats = person.get("stats", [])
        if not stats:
            continue
        splits = stats[0].get("splits", [])
        if not splits:
            continue
        pa = int(splits[0].get("stat", {}).get("plateAppearances", 0) or 0)
        if pa >= MISSING_STARTER_PA_THRESHOLD:
            season_starters.append((person.get("fullName"), pa))

    season_starters.sort(key=lambda x: x[1], reverse=True)
    top_starters = season_starters[:TOP_N_STARTERS_TO_CHECK]
    if not top_starters:
        return [], 0.0

    total_pa_top = sum(pa for _, pa in top_starters)
    missing = [(name, pa) for name, pa in top_starters if name not in active_hitter_names]

    if not missing:
        return [], 0.0

    missing_pa = sum(pa for _, pa in missing)
    # penalización proporcional: si falta el titular con más PA de los 5,
    # pesa más que si falta el quinto. Tope en 0.15 para no sobre-castigar
    # con una sola señal indirecta (esto es una aproximación, no certeza).
    penalty = min((missing_pa / total_pa_top) * 0.20, 0.15) if total_pa_top else 0.0
    return [name for name, _ in missing], round(penalty, 3)


def fetch_schedule_with_matchups(day_str):
    data = get_json(
        f"{STATS_BASE}/schedule?sportId=1&date={day_str}&hydrate=probablePitcher,team,linescore,venue"
    )
    if not data or not data.get("dates"):
        return []
    return data["dates"][0].get("games", [])


def fetch_final_scores(day_str):
    """Resultados finales de juegos terminados en una fecha dada.
    Devuelve dict {gamePk: {homeScore, awayScore, status}}.
    """
    data = get_json(f"{STATS_BASE}/schedule?sportId=1&date={day_str}&hydrate=linescore")
    out = {}
    if not data or not data.get("dates"):
        return out
    for g in data["dates"][0].get("games", []):
        status = g.get("status", {}).get("abstractGameState")  # "Final", "Live", "Preview"
        linescore = g.get("linescore", {})
        home_score = linescore.get("teams", {}).get("home", {}).get("runs")
        away_score = linescore.get("teams", {}).get("away", {}).get("runs")
        out[g["gamePk"]] = {
            "status": status,
            "homeScore": home_score,
            "awayScore": away_score,
        }
    return out


def fetch_live_game_states(day_str):
    """Estado actual de cada juego del día al momento de esta corrida del
    pipeline: si ya empezó (Live) o terminó (Final), marcador actual, y
    entrada/parte. Esto NO es un dato en vivo dentro de la app — es un
    snapshot de cómo estaba el juego cuando corrió el pipeline (mañana o
    tarde), igual que el resto de los datos. Se usa para explicar con
    claridad por qué un momio pre-partido ya no aplica (juego en curso),
    en vez de solo decir 'fuera de rango' sin contexto.
    """
    data = get_json(f"{STATS_BASE}/schedule?sportId=1&date={day_str}&hydrate=linescore")
    out = {}
    if not data or not data.get("dates"):
        print(f"WARN liveState: get_json devolvió vacío/None para {day_str} — no hay estados de juego disponibles.")
        return out
    games_list = data["dates"][0].get("games", []) if data.get("dates") else []
    for g in games_list:
        status_state = g.get("status", {}).get("abstractGameState")  # "Preview", "Live", "Final"
        if status_state not in ("Live", "Final"):
            continue
        linescore = g.get("linescore", {})
        home_score = linescore.get("teams", {}).get("home", {}).get("runs")
        away_score = linescore.get("teams", {}).get("away", {}).get("runs")
        inning = linescore.get("currentInning")
        inning_half = linescore.get("inningHalf")  # "Top" / "Bottom"
        out[g["gamePk"]] = {
            "status": status_state,
            "homeScore": home_score,
            "awayScore": away_score,
            "inning": inning,
            "inningHalf": inning_half,
        }
    print(f"  Estados de juego (liveState): {len(out)} de {len(games_list)} juego(s) ya en curso/finalizados.")
    return out


# ---------------------------------------------------------------------------
# MOMIOS AUTOMÁTICOS — The Odds API (https://the-odds-api.com).
# Requiere la variable de entorno ODDS_API_KEY (ver workflow de GitHub
# Actions, configurado como secret del repo). Si no está presente o la
# llamada falla (créditos agotados, etc.), el pipeline sigue funcionando
# sin momios automáticos — el usuario simplemente los mete a mano como antes.
# ---------------------------------------------------------------------------
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
ODDS_API_SPORT_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb"


ODDS_CACHE_FILE = "odds_cache.json"


def load_odds_cache():
    try:
        with open(ODDS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_odds_cache(cache):
    try:
        with open(ODDS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        print(f"WARN: no se pudo guardar el caché de momios: {e}")


def is_sane_pregame_odds_value(dec_odds):
    """Un momio pre-partido razonable de MLB implica entre 8% y 92% de
    probabilidad — fuera de ese rango casi siempre es un resto de mercado
    EN VIVO (de un juego ya muy avanzado) que The Odds API puede seguir
    devolviendo por un rato después de que el juego cambia de cuotas, o un
    dato corrupto. Si se cachea ese valor, queda "congelado" como si fuera
    válido para corridas futuras del mismo día — por eso se filtra ANTES
    de guardar en caché, no solo al momento de calcular edge.
    """
    try:
        d = float(dec_odds)
    except (TypeError, ValueError):
        return False
    if not d or d <= 1:
        return False
    imp = 1 / d
    return 0.08 <= imp <= 0.92


def sanitize_odds_event(event):
    """Revisa los mercados h2h de un evento de The Odds API y descarta el
    evento completo si alguno de sus momios h2h está fuera de rango sano —
    eso es señal de que el juego ya está en vivo/avanzado y esos números
    no deben tratarse como pre-partido, ni cachearse como tales.
    """
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                if not is_sane_pregame_odds_value(outcome.get("price")):
                    return None
    return event


def fetch_live_odds(api_key, today_str, mode):
    """Cachea la respuesta de momios por (fecha, modo) en un archivo local
    versionado junto a data.json. Si ya corriste este mismo modo hoy (por
    ejemplo, probando varias veces 'full' el mismo día), reutiliza esa
    respuesta sin gastar créditos nuevos de The Odds API. El caché se limpia
    solo: cualquier entrada de un día distinto a hoy se descarta al guardar.
    """
    cache_key = f"{today_str}:{mode}"
    cache = load_odds_cache()

    # Limpieza: descarta entradas de días anteriores, conserva solo hoy.
    cache = {k: v for k, v in cache.items() if k.startswith(today_str)}

    if cache_key in cache:
        print(f"  Momios automáticos: usando caché de esta corrida de hoy ({len(cache[cache_key])} evento(s), sin gastar créditos nuevos)")
        return cache[cache_key]

    if not api_key:
        print("INFO: ODDS_API_KEY no configurada — se omiten momios automáticos.")
        return []
    # Incluimos múltiples bookmakers para detectar consenso y sharp movement.
    # DraftKings, FanDuel, BetMGM, Caesars y BetRivers son los más líquidos
    # del mercado US — cuando todas coinciden en una dirección, es señal fuerte.
    # No especificamos bookmakers= para obtener TODOS los disponibles en el plan
    # y luego analizar el spread entre ellos para detectar movimiento.
    url = (
        f"{ODDS_API_BASE}?regions=us&markets=h2h,spreads,totals"
        f"&oddsFormat=decimal&bookmakers=draftkings,fanduel,betmgm,caesars,betrivers"
        f"&apiKey={api_key}"
    )
    data = get_json(url, retries=2)
    if data is None:
        print("WARN: no se pudieron obtener momios de The Odds API (revisa créditos/clave).")
        return []
    if isinstance(data, dict) and data.get("message"):
        print(f"WARN: The Odds API respondió error: {data.get('message')}")
        return []
    result = data if isinstance(data, list) else []
    sane_result = []
    for ev in result:
        sane_ev = sanitize_odds_event(ev)
        if sane_ev is None:
            print(f"  WARN: descartado evento con momio fuera de rango sano: {ev.get('away_team')} @ {ev.get('home_team')} (probablemente en vivo/avanzado, no se cachea)")
            continue
        sane_result.append(sane_ev)
    result = sane_result

    cache[cache_key] = result
    save_odds_cache(cache)
    return result


def consensus_and_sharp_movement(bookmakers, outcome_filter):
    """Analiza el consenso entre múltiples casas de apuestas para detectar
    movimiento de línea significativo. Devuelve un dict con:
    - consensus_prob: probabilidad implícita promedio entre todas las casas
    - spread_pp: diferencia en pp entre la casa más alta y más baja (>3pp = movimiento)
    - books_count: número de casas que publicaron momios para este mercado
    Solo aplica para h2h (ML) por ahora — es donde el movimiento de línea
    entre casas es más informativo sobre dinero sharp.
    """
    results = {}
    for market_key in outcome_filter:
        if market_key != "h2h":
            continue
        by_outcome = {}
        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != market_key:
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    price = outcome.get("price")
                    if not name or not price or price <= 1:
                        continue
                    by_outcome.setdefault(name, []).append(1/price)  # prob implícita
        for name, probs in by_outcome.items():
            if len(probs) < 2:
                continue
            avg_prob = sum(probs) / len(probs)
            spread = (max(probs) - min(probs)) * 100  # en pp
            results[name] = {
                "consensusProb": round(avg_prob, 4),
                "spreadPp": round(spread, 2),
                "booksCount": len(probs),
            }
    return results


def best_price_per_outcome(bookmakers, outcome_filter):
    """De una lista de bookmakers para un mercado, devuelve el mejor momio
    decimal disponible por cada nombre de outcome (ej. equipo o Over/Under).
    Usamos el mejor precio entre libros como aproximación práctica al
    'mercado' cuando no se especifica una casa concreta.
    """
    best = {}
    for book in bookmakers:
        for market in book.get("markets", []):
            if market.get("key") not in outcome_filter:
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                key = (market["key"], name, point)
                if price is None:
                    continue
                if key not in best or price > best[key]["price"]:
                    best[key] = {"price": price, "point": point}
    return best


# Alias conocidos: nombre alterno -> nombre canónico usado por MLB Stats API.
# Athletics es el caso confirmado (mudanza reciente de Oakland) — algunas
# fuentes de momios pueden seguir usando "Oakland Athletics" mientras MLB
# Stats API ya reporta solo "Athletics".
TEAM_NAME_ALIASES = {
    "Oakland Athletics": "Athletics",
}


def match_odds_to_game(odds_events, home_team_name, away_team_name):
    """Empareja un juego del schedule de MLB Stats API con un evento de
    The Odds API por nombre de equipo. Intenta en este orden:
    1. Coincidencia exacta de ambos nombres (caso normal).
    2. Coincidencia usando una tabla de alias conocidos (equipos que
       cambiaron de nombre/ciudad recientemente, como Athletics, que MLB
       Stats API ya reporta como "Athletics" sin ciudad mientras algunas
       fuentes de momios todavía usan "Oakland Athletics").
    3. Coincidencia parcial (uno de los nombres contenido en el otro),
       como último respaldo para variaciones no anticipadas.
    """
    def normalize(name):
        if not name:
            return ""
        n = name
        for alias, canonical in TEAM_NAME_ALIASES.items():
            if n == alias:
                n = canonical
        return n

    norm_home = normalize(home_team_name)
    norm_away = normalize(away_team_name)

    # 1. Exacta
    for ev in odds_events:
        if ev.get("home_team") == home_team_name and ev.get("away_team") == away_team_name:
            return ev

    # 2. Vía alias conocidos (en ambas direcciones)
    for ev in odds_events:
        ev_home = normalize(ev.get("home_team"))
        ev_away = normalize(ev.get("away_team"))
        if ev_home == norm_home and ev_away == norm_away:
            return ev

    # 3. Coincidencia parcial como último respaldo
    for ev in odds_events:
        ev_home = ev.get("home_team") or ""
        ev_away = ev.get("away_team") or ""
        home_match = home_team_name in ev_home or ev_home in home_team_name
        away_match = away_team_name in ev_away or ev_away in away_team_name
        if home_match and away_match:
            print(f"  INFO: match de '{away_team_name} @ {home_team_name}' por coincidencia parcial con '{ev_away} @ {ev_home}'")
            return ev

    return None


def extract_market_odds(event):
    """Convierte el evento de The Odds API a un payload simple que la app
    pueda consumir directo: ml/rl/total con el mejor precio disponible.
    """
    if not event:
        return None
    bookmakers = event.get("bookmakers", [])
    best = best_price_per_outcome(bookmakers, {"h2h", "spreads", "totals"})

    home_name = event.get("home_team")
    away_name = event.get("away_team")

    ml_home = next((v["price"] for k, v in best.items() if k[0] == "h2h" and k[1] == home_name), None)
    ml_away = next((v["price"] for k, v in best.items() if k[0] == "h2h" and k[1] == away_name), None)

    rl_home = next(((k[2], v["price"]) for k, v in best.items() if k[0] == "spreads" and k[1] == home_name), (None, None))
    rl_away = next(((k[2], v["price"]) for k, v in best.items() if k[0] == "spreads" and k[1] == away_name), (None, None))

    total_over = next(((k[2], v["price"]) for k, v in best.items() if k[0] == "totals" and k[1] == "Over"), (None, None))
    total_under = next(((k[2], v["price"]) for k, v in best.items() if k[0] == "totals" and k[1] == "Under"), (None, None))

    return {
        "mlHome": ml_home,
        "mlAway": ml_away,
        "rlHomePoint": rl_home[0],
        "rlHomePrice": rl_home[1],
        "rlAwayPoint": rl_away[0],
        "rlAwayPrice": rl_away[1],
        "totalPoint": total_over[0] or total_under[0],
        "totalOverPrice": total_over[1],
        "totalUnderPrice": total_under[1],
        "lastUpdate": event.get("commence_time"),
        # Consenso entre múltiples casas — útil para detectar sharp movement.
        # Si el spread entre casas es >3pp en ML, hay dinero moviéndose.
        "consensus": consensus_and_sharp_movement(bookmakers, {"h2h"}),
    }


# ---------------------------------------------------------------------------
# MOVIMIENTO DE LÍNEA — compara los momios automáticos de esta corrida
# contra los de la corrida anterior (guardados en el data.json existente),
# sin gastar créditos extra de The Odds API. Un movimiento grande en un
# mercado es señal de que el dinero del mercado está reaccionando a algo
# (lesión de último momento, cambio de clima, etc.) que nuestro modelo
# puede no estar capturando todavía.
# ---------------------------------------------------------------------------
LINE_MOVE_SIGNIFICANT_PCT = 4.0  # cambio de probabilidad implícita (en puntos %) que se considera movimiento notable


def implied_prob_from_decimal(dec_odds):
    try:
        d = float(dec_odds)
    except (TypeError, ValueError):
        return None
    if not d or d <= 1:
        return None
    return 1 / d


def calc_line_movement(current_odds, previous_odds):
    """Compara ML home/away, RL fav/dog (por precio, no por punto) y Total
    over/under entre la corrida actual y la anterior. Devuelve una lista de
    movimientos significativos: [{market, side, fromProb, toProb, deltaPct}].
    """
    if not current_odds or not previous_odds:
        return []

    movements = []
    pairs = [
        ("ML", "home", current_odds.get("mlHome"), previous_odds.get("mlHome")),
        ("ML", "away", current_odds.get("mlAway"), previous_odds.get("mlAway")),
        ("RL", "home", current_odds.get("rlHomePrice"), previous_odds.get("rlHomePrice")),
        ("RL", "away", current_odds.get("rlAwayPrice"), previous_odds.get("rlAwayPrice")),
        ("Total Over", None, current_odds.get("totalOverPrice"), previous_odds.get("totalOverPrice")),
        ("Total Under", None, current_odds.get("totalUnderPrice"), previous_odds.get("totalUnderPrice")),
    ]
    for market, side, current_price, previous_price in pairs:
        current_prob = implied_prob_from_decimal(current_price)
        previous_prob = implied_prob_from_decimal(previous_price)
        if current_prob is None or previous_prob is None:
            continue
        delta_pct = round((current_prob - previous_prob) * 100, 1)
        if abs(delta_pct) >= LINE_MOVE_SIGNIFICANT_PCT:
            movements.append({
                "market": market,
                "side": side,
                "fromProb": round(previous_prob * 100, 1),
                "toProb": round(current_prob * 100, 1),
                "deltaPct": delta_pct,
            })
    return movements


# ---------------------------------------------------------------------------
# PLAYER PROPS — misma The Odds API que ya usamos para ML/RL/Total, pidiendo
# mercados adicionales (batter_home_runs, batter_hits, pitcher_strikeouts).
# A diferencia de ML/RL/Total, The Odds API requiere pedir props evento por
# evento (no en el listado general), así que esto consume más créditos —
# por eso se limita a los juegos de HOY únicamente, no a mañana también.
# Si no hay créditos suficientes o falla, el pipeline sigue funcionando y
# la app cae de vuelta a la heurística de temporada que ya teníamos.
# ---------------------------------------------------------------------------
PROP_MARKETS = "batter_home_runs,batter_hits,pitcher_strikeouts"
PROP_TYPE_BY_MARKET = {
    "batter_home_runs": "HR",
    "batter_hits": "1+ Hit",
    "pitcher_strikeouts": "Ponches (K)",
}


def fetch_props_for_event(api_key, event_id):
    """Trae props de un evento específico de The Odds API. Cada llamada aquí
    cuesta créditos extra (a diferencia del listado general de ML/RL/Total),
    así que se usa solo para los juegos de hoy.
    """
    if not api_key or not event_id:
        print(f"  WARN props: falta api_key o event_id (event_id={event_id})")
        return None
    url = (
        f"{ODDS_API_SPORT_BASE}/events/{event_id}/odds"
        f"?regions=us&markets={PROP_MARKETS}&oddsFormat=decimal&apiKey={api_key}"
    )
    data = get_json(url, retries=1)
    if data is None:
        print(f"  WARN props: get_json devolvió None para event_id={event_id}")
    elif isinstance(data, dict) and data.get("message"):
        print(f"  WARN props: The Odds API respondió error para event_id={event_id}: {data.get('message')}")
    elif isinstance(data, dict) and not data.get("bookmakers"):
        print(f"  WARN props: respuesta sin bookmakers para event_id={event_id} (puede no haber props publicadas aún para este juego)")
    return data


def poisson_prob_at_least(k_min, lam):
    """P(X >= k_min) para una distribución Poisson con media lam.
    k_min puede venir como entero o como línea tipo 2.5 (se redondea hacia
    arriba: 2.5+ significa 'al menos 3', igual que en las líneas reales
    de sportsbooks, donde X.5 es la línea estándar sin empates posibles).
    """
    k_min_int = math.ceil(k_min)
    if k_min_int <= 0:
        return 1.0
    cumulative = sum((lam ** k) * math.exp(-lam) / math.factorial(k) for k in range(k_min_int))
    return max(0.0, min(1.0, 1 - cumulative))


def calc_prop_model_prob(player_name, prop_type, line, hitters_by_name, opposing_starter):
    """Probabilidad de modelo para una prop específica, usando stats reales
    de temporada del jugador y ajustando por el abridor rival. Usa Poisson
    para modelar correctamente la línea real de la prop (ej. HR 2.5+ es
    'al menos 3 jonrones en el juego', muy distinto de HR 0.5+ que es
    'al menos 1' — tratar cualquier línea como 1+ infla artificialmente
    el edge en líneas altas, que es justo el bug que esto corrige).
    Devuelve None si no hay suficiente dato para calcular con confianza.
    """
    stat = hitters_by_name.get(player_name)
    league_era, league_whip = 4.20, 1.30
    expected_pa = 4.2  # turnos al bate esperados por juego, aproximación estándar

    if line is None:
        return None  # sin línea no podemos saber qué umbral evaluar

    if prop_type == "HR":
        if not stat or not stat.get("hrRate"):
            return None
        lam = stat["hrRate"] * expected_pa  # HR esperados en el juego
        if opposing_starter and opposing_starter.get("era") is not None:
            if opposing_starter["era"] > league_era + 0.3:
                lam *= 1.12
            elif opposing_starter["era"] < league_era - 0.7:
                lam *= 0.88
        return poisson_prob_at_least(line, lam)

    if prop_type == "1+ Hit":
        if not stat or not stat.get("avg"):
            return None
        # hits esperados en el juego ≈ AVG * turnos esperados (aproximación;
        # AVG es hits/at-bats, no hits/PA, pero es la mejor señal disponible
        # sin un endpoint de hits-por-PA separado).
        lam = stat["avg"] * expected_pa
        if opposing_starter and opposing_starter.get("whip") is not None:
            if opposing_starter["whip"] > league_whip + 0.10:
                lam *= 1.08
            elif opposing_starter["whip"] < league_whip - 0.20:
                lam *= 0.92
        return poisson_prob_at_least(line, lam)

    if prop_type == "Ponches (K)":
        # A diferencia de HR/1+ Hit, aquí el "stat" que importa es del
        # PROPIO pitcher (su K/9), no de un bateador rival — por eso esta
        # rama recibe directamente el dict del abridor (ver llamada).
        starter = hitters_by_name  # aquí es el dict del propio abridor, no bateadores
        if not starter or starter.get("k9") is None:
            return None
        # Innings esperadas: un abridor titular típico lanza ~5.5-6 innings.
        # No tenemos un dato directo de "innings esperadas hoy" (eso
        # dependería del bullpen/estrategia del día), así que se usa una
        # aproximación fija, ajustada levemente por WHIP propio (un pitcher
        # más errático tiende a salir antes del juego en promedio).
        expected_ip = 5.7
        whip = starter.get("whip")
        if whip is not None:
            if whip > league_whip + 0.15:
                expected_ip *= 0.92
            elif whip < league_whip - 0.20:
                expected_ip *= 1.05
        lam = (starter["k9"] / 9) * expected_ip
        return poisson_prob_at_least(line, lam)

    return None  # tipo de prop no reconocido — no se calcula edge


def extract_props_from_event(event_data, home_hitters, away_hitters, home_starter, away_starter):
    """Convierte la respuesta de props de un evento en una lista con edge
    calculado: [{player, type, decimalOdds, line, modelProb, edge}, ...].
    Toma el mejor precio del lado 'Over' disponible entre bookmakers para
    cada jugador+mercado, y le agrega probabilidad de modelo cuando hay
    suficiente dato real de temporada para calcularla con confianza.
    """
    if not event_data:
        return []
    best_by_key = {}  # (player, type) -> {decimalOdds, line}
    for book in event_data.get("bookmakers", []):
        for market in book.get("markets", []):
            prop_type = PROP_TYPE_BY_MARKET.get(market.get("key"))
            if not prop_type:
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name") != "Over":
                    continue  # solo nos interesa el lado "Over" de cada prop
                player = outcome.get("description")
                price = outcome.get("price")
                point = outcome.get("point")
                if not player or price is None:
                    continue
                key = (player, prop_type)
                if key not in best_by_key or price > best_by_key[key]["decimalOdds"]:
                    best_by_key[key] = {"decimalOdds": price, "line": point}

    # El jugador puede ser de cualquiera de los dos equipos — buscamos su
    # stat en ambos diccionarios de bateadores, y el abridor rival es el
    # del equipo CONTRARIO a donde encontremos al jugador.
    props = []
    debug_printed = False
    for (player, prop_type), v in best_by_key.items():
        if prop_type == "Ponches (K)":
            # Caso especial: el sujeto de esta prop es un PITCHER (el
            # abridor), no un bateador — se compara contra el nombre de
            # cada abridor en vez de buscar en los diccionarios de bateo.
            if home_starter and home_starter.get("name") == player:
                hitters_source = home_starter
            elif away_starter and away_starter.get("name") == player:
                hitters_source = away_starter
            else:
                hitters_source = {}
                if not debug_printed:
                    print(f"    DEBUG prop de Ponches sin match: '{player}' no coincide con ningún abridor ('{home_starter.get('name') if home_starter else None}' / '{away_starter.get('name') if away_starter else None}')")
                    debug_printed = True
            opposing_starter = None  # no se usa en la rama de Ponches
        elif player in home_hitters:
            opposing_starter = away_starter
            hitters_source = home_hitters
        elif player in away_hitters:
            opposing_starter = home_starter
            hitters_source = away_hitters
        else:
            opposing_starter = None
            hitters_source = {}
            if not debug_printed:
                print(f"    DEBUG prop sin match de nombre: '{player}' no está en home_hitters ni away_hitters")
                print(f"    DEBUG home_hitters disponibles: {list(home_hitters.keys())}")
                print(f"    DEBUG away_hitters disponibles: {list(away_hitters.keys())}")
                debug_printed = True

        model_prob = calc_prop_model_prob(player, prop_type, v["line"], hitters_source, opposing_starter)
        if model_prob is None and hitters_source and not debug_printed:
            print(f"    DEBUG '{player}' SÍ está en hitters pero modelProb salió None. stat={hitters_source.get(player)}")
            debug_printed = True
        edge = None
        if model_prob is not None:
            implied = 1 / v["decimalOdds"] if v["decimalOdds"] and v["decimalOdds"] > 1 else None
            if implied is not None:
                edge = round((model_prob - implied) * 100, 1)

        props.append({
            "player": player,
            "type": prop_type,
            "decimalOdds": v["decimalOdds"],
            "line": v["line"],
            "modelProb": round(model_prob, 4) if model_prob is not None else None,
            "edge": edge,
        })

    # Prioriza props CON edge calculado (más útiles) sobre las que no tienen
    # probabilidad de modelo; dentro de cada grupo, ordena por mejor edge o
    # menor momio. Limita a 6 para no saturar el payload.
    props.sort(key=lambda p: (p["edge"] is None, -(p["edge"] or 0), p["decimalOdds"]))
    return props[:6]



def build_team_payload(team_id, abbr, name, standings, today, schedule_cache, boxscore_cache, is_today=True):
    s = standings.get(team_id, {})
    runs_per_game, staff_era = fetch_team_run_and_staff_stats(team_id)
    all_hitters = fetch_team_hitters_stats(team_id)
    top_power, top_contact = None, None
    for hname, hs in all_hitters.items():
        if top_power is None or hs["hrRate"] > top_power["hrRate"]:
            top_power = {"name": hname, "hrRate": hs["hrRate"], "pa": hs["pa"]}
        if top_contact is None or hs["avg"] > top_contact["avg"]:
            top_contact = {"name": hname, "avg": hs["avg"], "pa": hs["pa"]}

    # Detección de titulares ausentes (lesión/suspensión/etc.) — el roster
    # activo de hoy ya excluye lesionados serios; comparamos contra los
    # líderes de PA de TODA la temporada (roster 40-man, no filtrado por
    # actividad de hoy) para detectar esa ausencia y penalizar levemente
    # la ofensiva esperada del equipo, ya que runsPerGame de temporada no
    # refleja todavía que ese titular no está jugando.
    missing_names, penalty = detect_missing_starters(team_id, set(all_hitters.keys()))
    adjusted_runs_per_game = (runs_per_game or 4.3) * (1 - penalty) if penalty else (runs_per_game or 4.3)
    if missing_names:
        print(f"    Ausencia(s) detectada(s) en {name}: {missing_names} (penalización ofensiva: -{penalty*100:.1f}%)")

    # Fatiga de bullpen reciente — ERA de temporada no distingue un bullpen
    # descansado de uno que lanzó muchas entradas en los últimos 3 días.
    # Solo se calcula para equipos que juegan HOY (no mañana, ya que para
    # entonces el bullpen va a tener un día más de descanso de cualquier
    # forma, y esto evita boxscores extra que no se van a usar todavía).
    if is_today:
        bullpen_recent_ip, bullpen_penalty = fetch_recent_bullpen_load(team_id, today, schedule_cache, boxscore_cache)
    else:
        bullpen_recent_ip, bullpen_penalty = 0.0, 0.0
    adjusted_staff_era = (staff_era or 4.0) + bullpen_penalty
    if bullpen_penalty > 0:
        print(f"    Bullpen cargado en {name}: {bullpen_recent_ip} IP en últimos {BULLPEN_FATIGUE_LOOKBACK_DAYS} días (ERA efectivo +{bullpen_penalty})")

    return {
        "id": team_id,
        "abbr": abbr,
        "name": name,
        "winPct": s.get("winPct", 0.5),
        "homeWinPct": s.get("homeWinPct", 0.5),
        "awayWinPct": s.get("awayWinPct", 0.5),
        "last10": s.get("last10", 5),
        "elo": round(s.get("elo", 1500) - penalty * 300),  # penalización también visible en Elo
        "runsPerGame": round(adjusted_runs_per_game, 2),
        "staffEra": round(adjusted_staff_era, 2),
        "bullpenRecentIp": bullpen_recent_ip,
        "topPowerHitter": top_power,
        "topContactHitter": top_contact,
        "missingStarters": missing_names,
        "_allHitters": all_hitters,  # uso interno para edge de props, no va al JSON final
    }


def parse_mode():
    """Lee --mode=full o --mode=refresh de los argumentos de línea de comandos.
    'full' (default) hace todo el trabajo pesado: equipos, bateadores, clima.
    'refresh' reutiliza esos datos pesados del data.json existente y solo
    vuelve a consultar lo que más cambia durante el día: abridor probable
    (puede cambiar por lesión/rotación) y momios (el mercado se mueve).
    """
    mode = "full"
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
    return mode if mode in ("full", "refresh") else "full"


def load_existing_payload():
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# ALERTA POR CORREO — Resend (https://resend.com), plan gratis (100/día).
# Manda un resumen de los picks con tier BET/LEAN del día, calculados con
# el mismo modelo que la app (ver model.py). Requiere RESEND_API_KEY y
# ALERT_EMAIL_TO como secrets de GitHub. Si no están configurados o el
# envío falla, el pipeline sigue funcionando normal — el correo es un
# extra, no algo de lo que dependa la app.
# ---------------------------------------------------------------------------
RESEND_API_URL = "https://api.resend.com/emails"


def build_alert_email_html(best_bets, games_out, teams_by_id, run_label, today_str):
    if not best_bets:
        body = "<p>Sin candidatos con momios automáticos suficientes hoy para calcular edge.</p>"
    else:
        top = best_bets[0]
        rest = [b for b in best_bets[1:] if b["tier"] in ("BET", "LEAN")][:8]

        top_html = f"""
        <div style="background:#0D0F14;border-radius:12px;padding:20px;margin-bottom:20px;">
          <p style="color:#00FFB2;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 8px;font-weight:700;">Apuesta máxima</p>
          <p style="color:#FFFFFF;font-size:22px;font-weight:700;margin:0 0 4px;">{top['label']}</p>
          <p style="color:#9CA3AF;font-size:13px;margin:0 0 10px;">{top['matchup']} · momio {top['odd']}</p>
          <p style="color:#00FFB2;font-size:28px;font-weight:700;margin:0;">+{top['edge']:.1f}%</p>
        </div>
        """ if top["tier"] == "BET" else """
        <div style="background:#0D0F14;border-radius:12px;padding:20px;margin-bottom:20px;">
          <p style="color:#9CA3AF;font-size:13px;margin:0;">Ningún candidato alcanzó hoy el umbral BET (≥6% edge). El mejor disponible fue:</p>
          <p style="color:#FFFFFF;font-size:16px;font-weight:700;margin:8px 0 0;">{label} ({matchup}) — {edge:.1f}%</p>
        </div>
        """.format(label=top["label"], matchup=top["matchup"], edge=top["edge"])

        top3 = top_diverse_picks(best_bets, n=3)
        top3_rows = "".join(f"""
          <tr style="border-bottom:1px solid #1F2329;">
            <td style="padding:8px 0;color:#6B7280;font-size:12px;width:24px;">#{i+1}</td>
            <td style="padding:8px 0;color:#FFFFFF;font-size:13px;font-weight:600;">{b['label']}</td>
            <td style="padding:8px 0;color:#9CA3AF;font-size:12px;">{b['matchup']}</td>
            <td style="padding:8px 0;color:{'#00FFB2' if b['tier']=='BET' else '#FFB200' if b['tier']=='LEAN' else '#9CA3AF'};font-size:13px;font-weight:700;text-align:right;">+{b['edge']:.1f}%</td>
          </tr>
        """ for i, b in enumerate(top3))
        top3_html = f"""
        <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
          <thead>
            <tr><td colspan="4" style="color:#6B7280;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;padding-bottom:8px;">Top {len(top3)} del día (un pick por juego)</td></tr>
          </thead>
          <tbody>{top3_rows}</tbody>
        </table>
        """ if len(top3) > 1 else ""

        rows_html = "".join(f"""
          <tr style="border-bottom:1px solid #1F2329;">
            <td style="padding:10px 0;color:#FFFFFF;font-size:13px;font-weight:600;">{b['label']}</td>
            <td style="padding:10px 0;color:#9CA3AF;font-size:12px;">{b['matchup']}</td>
            <td style="padding:10px 0;color:{'#00FFB2' if b['tier']=='BET' else '#FFB200'};font-size:13px;font-weight:700;text-align:right;">+{b['edge']:.1f}%</td>
          </tr>
        """ for b in rest)

        rest_html = f"""
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr><td colspan="3" style="color:#6B7280;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;padding-bottom:8px;">Otros picks BET/LEAN de hoy</td></tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
        """ if rows_html else ""

        # Reporte completo: TODO lo que tenga tier BET/LEAN hoy (ML, RL,
        # Total, F5 y props), organizado por partido — sin límite de 8 como
        # "rest" arriba, para no perder ningún candidato del día.
        bets_by_matchup = {}
        for b in best_bets:
            if b["tier"] not in ("BET", "LEAN"):
                continue
            bets_by_matchup.setdefault(b["matchup"], []).append(("market", b))

        for g in games_out:
            home = teams_by_id.get(g["homeTeamId"])
            away = teams_by_id.get(g["awayTeamId"])
            if not home or not away:
                continue
            matchup_label = f"{away['abbr']} @ {home['abbr']}"
            for p in g.get("autoProps", []):
                edge = p.get("edge")
                tier = edge_tier(edge)
                if tier not in ("BET", "LEAN"):
                    continue
                line_suffix = f" {p['line']}+" if p.get("line") else ""
                prop_label = f"{p['player']} · {p['type']}{line_suffix}"
                bets_by_matchup.setdefault(matchup_label, []).append(
                    ("prop", {"label": prop_label, "edge": edge, "tier": tier, "odd": p["decimalOdds"]})
                )

        full_report_sections = []
        for matchup_label, items in bets_by_matchup.items():
            items.sort(key=lambda kv: -kv[1]["edge"])
            rows = "".join(f"""
              <tr style="border-bottom:1px solid #1F2329;">
                <td style="padding:6px 0;color:#FFFFFF;font-size:12px;">{b['label']}</td>
                <td style="padding:6px 0;color:#9CA3AF;font-size:11px;text-align:right;">{b['odd']}</td>
                <td style="padding:6px 0;color:{'#00FFB2' if b['tier']=='BET' else '#FFB200'};font-size:12px;font-weight:700;text-align:right;width:80px;">+{b['edge']:.1f}%</td>
              </tr>
            """ for _, b in items)
            full_report_sections.append(f"""
            <div style="margin-bottom:14px;">
              <p style="color:#9CA3AF;font-size:12px;font-weight:700;margin:0 0 4px;">{matchup_label}</p>
              <table style="width:100%;border-collapse:collapse;">{rows}</table>
            </div>
            """)

        full_report_html = f"""
        <div style="margin-top:24px;padding-top:20px;border-top:1px solid #1F2329;">
          <p style="color:#6B7280;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin:0 0 14px;">Reporte completo — todo BET/LEAN encontrado hoy (ML, RL, Total, F5, props)</p>
          {"".join(full_report_sections)}
        </div>
        """ if full_report_sections else ""

        body = top_html + top3_html + rest_html + full_report_html

    return f"""
    <div style="background:#06070A;padding:24px;font-family:-apple-system,sans-serif;">
      <h1 style="color:#FFFFFF;font-size:20px;margin:0 0 4px;">MLB EDGE</h1>
      <p style="color:#6B7280;font-size:12px;margin:0 0 20px;">{run_label} · {today_str}</p>
      {body}
      <p style="color:#4B5563;font-size:11px;margin-top:24px;">Herramienta de análisis, no garantiza resultados. Apuesta con responsabilidad.</p>
    </div>
    """


def build_line_movement_email_html(movements_by_game, today_str):
    """Construye el HTML del correo de alerta de movimiento de línea.
    Solo se envía cuando hay movimientos significativos (>=4pp) entre
    la corrida de las 7am y la de las 9am — señal de que algo cambió
    (alineación, lesión, clima) que el modelo aún no sabe.
    """
    rows = ""
    for matchup, moves in movements_by_game.items():
        for m in moves:
            direction = "▲" if m["deltaPct"] > 0 else "▼"
            color = "#00FFB2" if m["deltaPct"] > 0 else "#FF3D71"
            side_label = f"{m['side'].upper()} " if m.get("side") else ""
            rows += f"""
            <tr style="border-bottom:1px solid #1F2329;">
              <td style="padding:8px 0;color:#FFFFFF;font-size:13px;">{matchup}</td>
              <td style="padding:8px 0;color:#9CA3AF;font-size:12px;">{m['market']} {side_label}</td>
              <td style="padding:8px 0;font-size:12px;color:#9CA3AF;">{m['fromProb']}% → {m['toProb']}%</td>
              <td style="padding:8px 0;color:{color};font-size:13px;font-weight:700;text-align:right;">{direction} {abs(m['deltaPct']):.1f}pp</td>
            </tr>"""

    return f"""
    <div style="background:#0D1117;padding:24px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;">
      <h1 style="color:#00FFB2;font-size:22px;margin:0 0 4px;">MLB EDGE</h1>
      <p style="color:#6B7280;font-size:12px;margin:0 0 20px;">Alerta de movimiento de línea · {today_str}</p>
      <p style="color:#9CA3AF;font-size:13px;margin:0 0 16px;">Se detectaron movimientos significativos (≥4pp) entre la corrida de las 7am y las 9am. Esto puede indicar una lesión, cambio de alineación o movimiento de dinero.</p>
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="border-bottom:1px solid #30363D;">
            <td style="color:#6B7280;font-size:11px;text-transform:uppercase;padding-bottom:8px;">Partido</td>
            <td style="color:#6B7280;font-size:11px;text-transform:uppercase;padding-bottom:8px;">Mercado</td>
            <td style="color:#6B7280;font-size:11px;text-transform:uppercase;padding-bottom:8px;">Movimiento</td>
            <td style="color:#6B7280;font-size:11px;text-transform:uppercase;padding-bottom:8px;text-align:right;">Δ</td>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#6B7280;font-size:11px;margin:20px 0 0;">Herramienta de análisis — no garantiza resultados. Apuesta con responsabilidad.</p>
    </div>"""


def save_line_history(games_out, today_str, mode):
    """Guarda el historial de líneas de apertura y cierre en line_history.json.
    La corrida de las 7am (full) guarda la línea de APERTURA.
    Las corridas posteriores actualizan el CIERRE.
    Esto permite analizar después qué tan bien predice el movimiento de línea
    los resultados reales — la señal más valiosa para calibrar el modelo.
    """
    history_file = "line_history.json"
    try:
        with open(history_file) as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {}

    date_key = today_str
    if date_key not in history:
        history[date_key] = {}

    for g in games_out:
        if g.get("gameDateStr") != today_str:
            continue
        matchup = f"{g.get('awayTeamId','?')}@{g.get('homeTeamId','?')}"
        ao = g.get("autoOdds") or {}
        if not ao.get("mlHome") and not ao.get("mlAway"):
            continue

        snap = {
            "mlHome": ao.get("mlHome"),
            "mlAway": ao.get("mlAway"),
            "totalOverPrice": ao.get("totalOverPrice"),
            "totalUnderPrice": ao.get("totalUnderPrice"),
            "totalPoint": ao.get("totalPoint"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if matchup not in history[date_key]:
            history[date_key][matchup] = {"open": snap, "snapshots": [snap]}
        else:
            history[date_key][matchup]["snapshots"].append(snap)
            history[date_key][matchup]["close"] = snap  # siempre el más reciente

    # Limpiar historial de más de 14 días para no crecer infinitamente
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=14)).isoformat()
    history = {k: v for k, v in history.items() if k >= cutoff}

    try:
        with open(history_file, "w") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"  Historial de líneas guardado: {len(history.get(date_key, {}))} juego(s) en {date_key}")
    except Exception as e:
        print(f"WARN: no se pudo guardar line_history.json: {e}")


def send_alert_email(api_key, to_email, html_body, subject):
    if not api_key or not to_email:
        print("INFO: RESEND_API_KEY o ALERT_EMAIL_TO no configurados — se omite correo de alerta.")
        return
    payload = json.dumps({
        "from": "MLB Edge <onboarding@resend.dev>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")
    req = Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "mlb-edge-pipeline/1.0",
        },
    )
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            print(f"  Correo de alerta enviado (status {resp.status}).")
    except HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            error_body = "(no se pudo leer el cuerpo del error)"
        print(f"WARN: no se pudo enviar el correo de alerta: HTTP {e.code} — {error_body}")
    except URLError as e:
        print(f"WARN: no se pudo enviar el correo de alerta: {e}")


def main():
    mode = parse_mode()
    today = date.today()
    yesterday = today - timedelta(days=1)
    days = [today]
    day_strs = [d.isoformat() for d in days]
    debug_print_situation_codes()  # DIAGNÓSTICO TEMPORAL — quitar tras confirmar sitCodes
    debug_print_schedule_hydrations()  # DIAGNÓSTICO TEMPORAL — quitar tras confirmar hidratación de lineups
    print(f"Construyendo data.json para {day_strs} (modo: {mode})...")

    # Se carga el data.json existente SIEMPRE (no solo en modo refresh) para
    # poder calcular el historial de movimiento de línea comparando momios
    # de esta corrida contra los de la corrida anterior. En modo refresh,
    # además se usa para reutilizar equipos/clima ya calculados.
    existing_payload = load_existing_payload()
    existing_teams_by_id = {}
    existing_games_by_pk = {}
    schedule_cache = {}  # compartido entre equipos, evita repetir fetch_schedule por día
    boxscore_cache = {}  # compartido entre equipos, evita repetir boxscore del mismo gamePk
    if existing_payload:
        existing_games_by_pk = {g["gamePk"]: g for g in existing_payload.get("games", [])}
        if mode == "refresh":
            existing_teams_by_id = {t["id"]: t for t in existing_payload.get("teams", [])}
            print(f"  Modo refresh: reutilizando {len(existing_teams_by_id)} equipo(s) ya procesados (Elo/bateadores/clima no se recalculan).")

    standings = fetch_standings() if mode == "full" else {}
    team_cache = {}
    games_out = []

    # Momios automáticos (opcional, requiere ODDS_API_KEY como secret de GitHub).
    # Siempre se refrescan, en ambos modos — son justo lo que más cambia.
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    live_odds_events = fetch_live_odds(odds_api_key, today.isoformat(), mode)
    # Se busca en AYER y HOY (no solo hoy) porque el servidor de GitHub
    # Actions corre en UTC, y para horas de la tarde/noche en Puerto
    # Vallarta (UTC-6) la fecha UTC ya cambió al día siguiente — sin esto,
    # los juegos de la noche aparecían como "ayer" para el servidor y
    # nunca se detectaban como en curso/finalizados.
    live_game_states = {**fetch_live_game_states(yesterday.isoformat()), **fetch_live_game_states(today.isoformat())}
    print(f"  Momios automáticos: {len(live_odds_events)} evento(s) de The Odds API")

    for day_str in day_strs:
        games_raw = fetch_schedule_with_matchups(day_str)
        print(f"  {day_str}: {len(games_raw)} juego(s) encontrados")

        for g in games_raw:
            home_team = g["teams"]["home"]["team"]
            away_team = g["teams"]["away"]["team"]

            for t in (home_team, away_team):
                if t["id"] in team_cache:
                    continue
                if mode == "refresh" and t["id"] in existing_teams_by_id:
                    team_cache[t["id"]] = existing_teams_by_id[t["id"]]
                else:
                    print(f"  Procesando equipo: {t['name']}")
                    team_cache[t["id"]] = build_team_payload(
                        t["id"], t.get("abbreviation", ""), t["name"], standings,
                        today, schedule_cache, boxscore_cache,
                        is_today=(day_str == today.isoformat())
                    )

            # Abridor probable y momios: SIEMPRE se refrescan, en ambos modos.
            # Es justo el dato que puede cambiar entre la corrida de la mañana
            # y la de la tarde (lesión, cambio de rotación, movimiento de línea).
            home_pitcher = g["teams"]["home"].get("probablePitcher")
            away_pitcher = g["teams"]["away"].get("probablePitcher")
            home_pitcher_stats = fetch_pitcher_stats(home_pitcher["id"]) if home_pitcher else None
            away_pitcher_stats = fetch_pitcher_stats(away_pitcher["id"]) if away_pitcher else None

            venue_name = g.get("venue", {}).get("name")
            if mode == "full":
                weather = fetch_weather_for_venue(venue_name, g["gameDate"]) if venue_name else None
            else:
                existing_game = existing_games_by_pk.get(g["gamePk"])
                weather = existing_game.get("weather") if existing_game else None

            odds_event = match_odds_to_game(live_odds_events, home_team["name"], away_team["name"])
            auto_odds = extract_market_odds(odds_event)

            # Movimiento de línea: compara contra los momios de la corrida
            # anterior (mismo gamePk), sin gastar créditos extra de la API.
            previous_game = existing_games_by_pk.get(g["gamePk"])
            previous_odds = previous_game.get("autoOdds") if previous_game else None
            line_movement = calc_line_movement(auto_odds, previous_odds)
            if line_movement:
                print(f"  Movimiento de línea {home_team['name']} vs {away_team['name']}: {line_movement}")

            # Props: solo para juegos de HOY (no mañana, las líneas de props
            # tardan en publicarse y consultarlas con mucha anticipación
            # desperdicia créditos) y solo en modo full (refresh se queda
            # con las del payload existente, igual que el clima).
            auto_props = []
            if mode == "full" and day_str == today.isoformat() and odds_event:
                event_id = odds_event.get("id")
                props_data = fetch_props_for_event(odds_api_key, event_id)
                home_hitters = team_cache.get(home_team["id"], {}).get("_allHitters", {})
                away_hitters = team_cache.get(away_team["id"], {}).get("_allHitters", {})
                auto_props = extract_props_from_event(
                    props_data, home_hitters, away_hitters, home_pitcher_stats, away_pitcher_stats
                )
                print(f"  Props {home_team['name']} vs {away_team['name']}: {len(auto_props)} encontrada(s)")
            elif mode == "full" and day_str == today.isoformat() and not odds_event:
                print(f"  Props {home_team['name']} vs {away_team['name']}: sin match de evento en The Odds API (odds_event=None)")
            elif mode == "refresh":
                existing_game = existing_games_by_pk.get(g["gamePk"])
                auto_props = existing_game.get("autoProps", []) if existing_game else []

            games_out.append({
                "gamePk": g["gamePk"],
                "gameDate": g["gameDate"],
                "gameDateStr": day_str,
                "venue": venue_name,
                "weather": weather,
                "homeTeamId": home_team["id"],
                "awayTeamId": away_team["id"],
                "homeTeamName": home_team["name"],
                "awayTeamName": away_team["name"],
                "autoOdds": auto_odds,
                "lineMovement": line_movement,
                "liveState": live_game_states.get(g["gamePk"]),
                "autoProps": auto_props,
                "homeStarter": {
                    "name": home_pitcher["fullName"] if home_pitcher else None,
                    **(home_pitcher_stats or {}),
                } if home_pitcher else None,
                "awayStarter": {
                    "name": away_pitcher["fullName"] if away_pitcher else None,
                    **(away_pitcher_stats or {}),
                } if away_pitcher else None,
            })

    # Resultados finales de ayer y de HOY (los juegos de hoy que ya terminaron),
    # para que la app coteje la bitácora sola sin esperar al día siguiente.
    def collect_final_results(day):
        day_str = day.isoformat()
        final_scores_raw = fetch_final_scores(day_str)
        day_games = fetch_schedule_with_matchups(day_str)
        out = []
        for g in day_games:
            score = final_scores_raw.get(g["gamePk"])
            if not score or score.get("status") != "Final":
                continue
            out.append({
                "gamePk": g["gamePk"],
                "dateStr": day_str,
                "homeTeamId": g["teams"]["home"]["team"]["id"],
                "awayTeamId": g["teams"]["away"]["team"]["id"],
                "homeScore": score["homeScore"],
                "awayScore": score["awayScore"],
            })
        return out

    print(f"  Resultados finales de {yesterday.isoformat()} y {today.isoformat()}...")
    results_out = collect_final_results(yesterday) + collect_final_results(today)
    print(f"  {len(results_out)} resultado(s) final(es) agregado(s)")

    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lastMode": mode,
        "date": day_strs[0],
        "availableDates": day_strs,
        "teams": [{k: v for k, v in t.items() if k != "_allHitters"} for t in team_cache.values()],
        "games": games_out,
        "results": results_out,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Listo: {len(games_out)} juegos totales, {len(team_cache)} equipos, {len(results_out)} resultados procesados.")

    # Correo de alerta con la Apuesta Máxima del día y demás picks BET/LEAN.
    # Solo se calcula sobre los juegos de HOY (no mañana, ya que mañana
    # normalmente no tiene momios automáticos publicados todavía).
    todays_games = [g for g in games_out if g["gameDateStr"] == today.isoformat()]
    teams_by_id = team_cache
    best_bets = find_best_bets(todays_games, teams_by_id)

    # Etiqueta de corrida según la hora UTC del servidor
    hour_utc = datetime.now(timezone.utc).hour
    if mode == "full" and hour_utc == 13:
        run_label = "Corrida completa (mañana)"
    elif mode == "full" and hour_utc == 15:
        run_label = "Corrida anticipada (9am)"
    elif hour_utc == 0:
        run_label = "Cierre de líneas (noche)"
    else:
        run_label = "Actualización (tarde)"

    # Guardar historial de líneas para análisis de apertura/cierre
    save_line_history(todays_games, today.isoformat(), mode)

    # Correo principal de picks del día
    html_body = build_alert_email_html(best_bets, todays_games, teams_by_id, run_label, today.isoformat())
    resend_key = os.environ.get("RESEND_API_KEY", "")
    alert_to = os.environ.get("ALERT_EMAIL_TO", "")
    subject = f"MLB Edge — {today.isoformat()} ({run_label})"
    send_alert_email(resend_key, alert_to, html_body, subject)

    # Alerta especial de movimiento de línea — solo en la corrida de las 9am
    # (15:00 UTC), comparando contra la de las 7am (13:00 UTC).
    # Si hay movimientos significativos (>=4pp), manda un correo separado
    # para que puedas revisar antes de apostar.
    if hour_utc == 15 and mode == "full":
        movements_by_game = {}
        for g in todays_games:
            lm = g.get("lineMovement") or []
            if lm:
                home = teams_by_id.get(g["homeTeamId"])
                away = teams_by_id.get(g["awayTeamId"])
                if home and away:
                    matchup_label = f"{away['abbr']} @ {home['abbr']}"
                    movements_by_game[matchup_label] = lm

        if movements_by_game:
            total_moves = sum(len(v) for v in movements_by_game.values())
            print(f"  Movimientos de línea detectados: {total_moves} en {len(movements_by_game)} partido(s) — enviando alerta.")
            move_html = build_line_movement_email_html(movements_by_game, today.isoformat())
            send_alert_email(
                resend_key, alert_to, move_html,
                f"⚠️ MLB Edge — Movimiento de línea detectado ({today.isoformat()})"
            )
        else:
            print("  Sin movimientos significativos de línea en la corrida de las 9am.")

    # Agregar line_history.json al commit de GitHub Actions
    import subprocess
    try:
        subprocess.run(["git", "add", "line_history.json"], check=False, capture_output=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
