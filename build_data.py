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
import os
import sys
import time
from datetime import date, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

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


def fetch_top_hitters(team_id):
    """Top bateador de poder (HR/PA) y top de contacto (AVG) con stats reales de temporada."""
    roster = get_json(f"{STATS_BASE}/teams/{team_id}/roster?rosterType=active")
    if not roster:
        return None, None
    hitter_ids = [
        p["person"]["id"] for p in roster.get("roster", [])
        if p.get("position", {}).get("abbreviation") != "P"
    ]
    if not hitter_ids:
        return None, None

    ids_param = ",".join(str(i) for i in hitter_ids)
    people = get_json(
        f"{STATS_BASE}/people?personIds={ids_param}&hydrate=stats(group=hitting,type=season)"
    )
    if not people:
        return None, None

    top_power, top_contact = None, None
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
        avg = float(stat.get("avg", 0) or 0)
        hr_rate = hr / pa if pa else 0

        if top_power is None or hr_rate > top_power["hrRate"]:
            top_power = {"name": person.get("fullName"), "hrRate": round(hr_rate, 4), "pa": pa}
        if top_contact is None or avg > top_contact["avg"]:
            top_contact = {"name": person.get("fullName"), "avg": round(avg, 3), "pa": pa}

    return top_power, top_contact


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


# ---------------------------------------------------------------------------
# MOMIOS AUTOMÁTICOS — The Odds API (https://the-odds-api.com).
# Requiere la variable de entorno ODDS_API_KEY (ver workflow de GitHub
# Actions, configurado como secret del repo). Si no está presente o la
# llamada falla (créditos agotados, etc.), el pipeline sigue funcionando
# sin momios automáticos — el usuario simplemente los mete a mano como antes.
# ---------------------------------------------------------------------------
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
ODDS_API_SPORT_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb"


def fetch_live_odds(api_key):
    if not api_key:
        print("INFO: ODDS_API_KEY no configurada — se omiten momios automáticos.")
        return []
    url = (
        f"{ODDS_API_BASE}?regions=us&markets=h2h,spreads,totals"
        f"&oddsFormat=decimal&apiKey={api_key}"
    )
    data = get_json(url, retries=2)
    if data is None:
        print("WARN: no se pudieron obtener momios de The Odds API (revisa créditos/clave).")
        return []
    if isinstance(data, dict) and data.get("message"):
        print(f"WARN: The Odds API respondió error: {data.get('message')}")
        return []
    return data if isinstance(data, list) else []


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


def match_odds_to_game(odds_events, home_team_name, away_team_name):
    """Empareja un juego del schedule de MLB Stats API con un evento de
    The Odds API por nombre de equipo (The Odds API usa nombres completos
    tipo 'New York Yankees', igual que MLB Stats API).
    """
    for ev in odds_events:
        if ev.get("home_team") == home_team_name and ev.get("away_team") == away_team_name:
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
    }


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


def extract_props_from_event(event_data):
    """Convierte la respuesta de props de un evento en una lista simple:
    [{player, type, decimalOdds, line}, ...]. Toma el mejor precio del lado
    'Over' disponible entre bookmakers para cada jugador+mercado.
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

    props = [
        {"player": p, "type": t, "decimalOdds": v["decimalOdds"], "line": v["line"]}
        for (p, t), v in best_by_key.items()
    ]
    # como máximo 1 prop de cada tipo por jugador; limitamos a 6 totales por
    # juego para no saturar el payload — priorizamos por momio más bajo
    # (más probable, generalmente el bateador/abridor más relevante del día).
    props.sort(key=lambda x: x["decimalOdds"])
    return props[:6]



def build_team_payload(team_id, abbr, name, standings):
    s = standings.get(team_id, {})
    runs_per_game, staff_era = fetch_team_run_and_staff_stats(team_id)
    top_power, top_contact = fetch_top_hitters(team_id)
    return {
        "id": team_id,
        "abbr": abbr,
        "name": name,
        "winPct": s.get("winPct", 0.5),
        "homeWinPct": s.get("homeWinPct", 0.5),
        "awayWinPct": s.get("awayWinPct", 0.5),
        "last10": s.get("last10", 5),
        "elo": s.get("elo", 1500),
        "runsPerGame": runs_per_game or 4.3,
        "staffEra": staff_era or 4.0,
        "topPowerHitter": top_power,
        "topContactHitter": top_contact,
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


def main():
    mode = parse_mode()
    today = date.today()
    yesterday = today - timedelta(days=1)
    days = [today, today + timedelta(days=1)]
    day_strs = [d.isoformat() for d in days]
    print(f"Construyendo data.json para {day_strs} (modo: {mode})...")

    existing_payload = load_existing_payload() if mode == "refresh" else None
    existing_teams_by_id = {}
    existing_games_by_pk = {}
    if existing_payload:
        existing_teams_by_id = {t["id"]: t for t in existing_payload.get("teams", [])}
        existing_games_by_pk = {g["gamePk"]: g for g in existing_payload.get("games", [])}
        print(f"  Modo refresh: reutilizando {len(existing_teams_by_id)} equipo(s) ya procesados (Elo/bateadores/clima no se recalculan).")

    standings = fetch_standings() if mode == "full" else {}
    team_cache = {}
    games_out = []

    # Momios automáticos (opcional, requiere ODDS_API_KEY como secret de GitHub).
    # Siempre se refrescan, en ambos modos — son justo lo que más cambia.
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    live_odds_events = fetch_live_odds(odds_api_key)
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
                        t["id"], t.get("abbreviation", ""), t["name"], standings
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

            # Props: solo para juegos de HOY (no mañana, las líneas de props
            # tardan en publicarse y consultarlas con mucha anticipación
            # desperdicia créditos) y solo en modo full (refresh se queda
            # con las del payload existente, igual que el clima).
            auto_props = []
            if mode == "full" and day_str == today.isoformat() and odds_event:
                event_id = odds_event.get("id")
                props_data = fetch_props_for_event(odds_api_key, event_id)
                auto_props = extract_props_from_event(props_data)
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
        "teams": list(team_cache.values()),
        "games": games_out,
        "results": results_out,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Listo: {len(games_out)} juegos totales, {len(team_cache)} equipos, {len(results_out)} resultados procesados.")


if __name__ == "__main__":
    main()
