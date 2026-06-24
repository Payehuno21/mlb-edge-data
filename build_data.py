"""
MLB Edge — pipeline de datos diario
------------------------------------------------------------------
Inspirado en la arquitectura de:
  - gmalbert/baseball-predictions (ingesta MLB Stats API + features de equipo)
  - sebasclarkv/baseball-analyzer (probabilidades de bateo por jugador)

Este script corre en GitHub Actions (no en el navegador del usuario), así
que no tiene problemas de CORS: pega directo a la MLB Stats API.

Genera un único archivo data.json con:
  - calendario de hoy (visitante/local, abridores probables)
  - Elo de equipo derivado de standings de temporada
  - splits home/away, runs por juego, ERA de staff (proxy de bullpen)
  - top bateador de poder (HR/PA) y top bateador de contacto (AVG) por equipo,
    con stats reales de temporada (no heurística inventada)
  - abridor probable con ERA/WHIP/K9 de temporada

La app web (React) consume este JSON directo desde GitHub Pages/raw,
sin necesidad de fetch en vivo ni proxies CORS.
"""

import json
import sys
import time
from datetime import date, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

STATS_BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 12
MIN_PA_FOR_PROPS = 60  # mínimo de plate appearances para que la tasa de HR/AVG sea confiable


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
        f"{STATS_BASE}/schedule?sportId=1&date={day_str}&hydrate=probablePitcher,team,linescore"
    )
    if not data or not data.get("dates"):
        return []
    return data["dates"][0].get("games", [])


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


def main():
    today = date.today()
    days = [today, today + timedelta(days=1)]
    day_strs = [d.isoformat() for d in days]
    print(f"Construyendo data.json para {day_strs}...")

    standings = fetch_standings()
    team_cache = {}
    games_out = []

    for day_str in day_strs:
        games_raw = fetch_schedule_with_matchups(day_str)
        print(f"  {day_str}: {len(games_raw)} juego(s) encontrados")

        for g in games_raw:
            home_team = g["teams"]["home"]["team"]
            away_team = g["teams"]["away"]["team"]

            for t in (home_team, away_team):
                if t["id"] not in team_cache:
                    print(f"  Procesando equipo: {t['name']}")
                    team_cache[t["id"]] = build_team_payload(
                        t["id"], t.get("abbreviation", ""), t["name"], standings
                    )

            home_pitcher = g["teams"]["home"].get("probablePitcher")
            away_pitcher = g["teams"]["away"].get("probablePitcher")
            home_pitcher_stats = fetch_pitcher_stats(home_pitcher["id"]) if home_pitcher else None
            away_pitcher_stats = fetch_pitcher_stats(away_pitcher["id"]) if away_pitcher else None

            games_out.append({
                "gamePk": g["gamePk"],
                "gameDate": g["gameDate"],
                "gameDateStr": day_str,
                "homeTeamId": home_team["id"],
                "awayTeamId": away_team["id"],
                "homeStarter": {
                    "name": home_pitcher["fullName"] if home_pitcher else None,
                    **(home_pitcher_stats or {}),
                } if home_pitcher else None,
                "awayStarter": {
                    "name": away_pitcher["fullName"] if away_pitcher else None,
                    **(away_pitcher_stats or {}),
                } if away_pitcher else None,
            })

    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "date": day_strs[0],
        "availableDates": day_strs,
        "teams": list(team_cache.values()),
        "games": games_out,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Listo: {len(games_out)} juegos totales, {len(team_cache)} equipos procesados.")


if __name__ == "__main__":
    main()
