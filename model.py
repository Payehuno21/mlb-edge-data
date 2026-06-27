"""
model.py — Modelo de edge portado desde MLBEdge.jsx (la app).
------------------------------------------------------------------
Este módulo replica EXACTAMENTE la lógica de cálculo de probabilidad y edge
que usa la app web, para que el correo de alertas reporte los mismos números
que vas a ver al abrir la app — no una aproximación distinta.

Si algún día se ajusta el modelo en MLBEdge.jsx (pesos del abridor, pendiente
del Run Line, etc.), este archivo debe actualizarse en espejo.
"""

import math
from datetime import datetime, timezone


def elo_win_prob(diff):
    return 1 / (1 + math.pow(10, -diff / 400))


def pitcher_score(starter):
    if not starter or starter.get("era") is None:
        return 0
    league_era, league_whip, league_k9 = 4.20, 1.30, 8.5
    era_adj = (league_era - starter["era"]) * 55
    whip_adj = (league_whip - (starter.get("whip") or league_whip)) * 70
    k9_adj = ((starter.get("k9") or league_k9) - league_k9) * 5
    return era_adj + whip_adj + k9_adj


def weather_run_factor(weather):
    if not weather:
        return 1.0
    factor = 1.0
    wind_mph = weather.get("windMph")
    wind_dir = weather.get("windDirDeg")
    if wind_mph is not None and wind_mph >= 8 and wind_dir is not None:
        if 200 <= wind_dir <= 340:
            factor += 0.05
        elif wind_dir <= 60 or wind_dir >= 300:
            factor -= 0.04
    temp_f = weather.get("tempF")
    if temp_f is not None:
        if temp_f > 85:
            factor += 0.025
        if temp_f < 50:
            factor -= 0.03
    precip = weather.get("precipProb")
    if precip is not None and precip > 50:
        factor -= 0.02
    return factor


def build_model(home, away, home_starter, away_starter, weather, park_factor=1.0):
    """home/away: dicts con elo, homeWinPct/awayWinPct (o winPct), last10,
    runsPerGame, staffEra. home_starter/away_starter: dicts con era/whip/k9.
    Devuelve el mismo conjunto de campos que buildModel() en JS.
    """
    diff = (home["elo"] + 24) - away["elo"]

    home_split = ((home.get("homeWinPct") or home.get("winPct") or 0.5) - 0.5) * 220
    away_split = ((away.get("awayWinPct") or away.get("winPct") or 0.5) - 0.5) * 220
    diff += home_split - away_split

    form_adj = (((home.get("last10") or 5) - (away.get("last10") or 5)) / 10) * 70
    diff += form_adj

    home_sp = pitcher_score(home_starter)
    away_sp = pitcher_score(away_starter)
    diff += home_sp - away_sp

    bullpen_adj = ((away.get("staffEra") or 4.0) - (home.get("staffEra") or 4.0)) * 18
    diff += bullpen_adj

    home_win_prob = elo_win_prob(diff)

    league_avg_total = 8.6
    offense_factor = ((home.get("runsPerGame") or 4.3) + (away.get("runsPerGame") or 4.3)) / 8.6
    pitching_factor = ((home_starter or {}).get("era", 4.2) or 4.2) + ((away_starter or {}).get("era", 4.2) or 4.2)
    pitching_factor /= 8.4
    projected_total = league_avg_total * (0.45 * offense_factor + 0.35 * pitching_factor + 0.20)
    w_factor = weather_run_factor(weather)
    projected_total *= w_factor
    projected_total *= park_factor

    expected_margin = (diff / 400) * 1.4
    home_is_favorite = home_win_prob >= 0.5
    fav_minus_1_5 = 1 / (1 + math.exp(-(abs(expected_margin) - 1.5) / 2.5))
    dog_plus_1_5 = 1 - fav_minus_1_5

    f5_diff = diff * 0.62
    f5_home_win_prob = elo_win_prob(f5_diff)
    f5_expected_margin = (f5_diff / 400) * 1.4
    f5_home_is_favorite = f5_home_win_prob >= 0.5
    f5_fav_minus_0_5 = 1 / (1 + math.exp(-(abs(f5_expected_margin) - 0.5) / 1.4))
    f5_dog_plus_0_5 = 1 - f5_fav_minus_0_5
    f5_projected_total = projected_total * 0.56

    return {
        "homeWinProb": home_win_prob,
        "awayWinProb": 1 - home_win_prob,
        "projectedTotal": round(projected_total, 1),
        "homeIsFavorite": home_is_favorite,
        "favMinus1_5": fav_minus_1_5,
        "dogPlus1_5": dog_plus_1_5,
        "weatherFactor": w_factor,
        "f5": {
            "homeWinProb": f5_home_win_prob,
            "awayWinProb": 1 - f5_home_win_prob,
            "projectedTotal": round(f5_projected_total, 1),
            "homeIsFavorite": f5_home_is_favorite,
            "favMinus0_5": f5_fav_minus_0_5,
            "dogPlus0_5": f5_dog_plus_0_5,
        },
    }


def implied_prob_decimal(dec_odds):
    try:
        d = float(dec_odds)
    except (TypeError, ValueError):
        return None
    if not d or d <= 1:
        return None
    return 1 / d


def edge_pct(model_prob, dec_odds):
    imp = implied_prob_decimal(dec_odds)
    if imp is None or model_prob is None:
        return None
    return (model_prob - imp) * 100


def edge_tier(edge):
    if edge is None:
        return None
    if edge >= 6:
        return "BET"
    if edge >= 2.5:
        return "LEAN"
    if edge > -2.5:
        return "PASS"
    return "FADE"


def kelly_fraction(model_prob, dec_odds, fractional_multiplier=0.25):
    try:
        d = float(dec_odds)
    except (TypeError, ValueError):
        return None
    if not d or d <= 1 or model_prob is None:
        return None
    b = d - 1
    p = model_prob
    q = 1 - p
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0
    return full_kelly * fractional_multiplier


def is_sane_pregame_odds(dec_odds):
    """Un momio pre-partido razonable de MLB implica entre 8% y 92% de
    probabilidad — fuera de ese rango casi siempre es señal de un momio de
    mercado EN VIVO (el juego ya empezó) o un dato corrupto, no una
    oportunidad real. Se usa como filtro de cordura antes de confiar en
    cualquier candidato para la Apuesta Máxima.
    """
    imp = implied_prob_decimal(dec_odds)
    if imp is None:
        return False
    return 0.08 <= imp <= 0.92


def find_best_bets(games_out, teams_by_id):
    """Recorre los juegos del día y devuelve la lista de candidatos con
    edge calculado para ML y Run Line (juego completo), usando los momios
    automáticos ya presentes en cada juego (autoOdds). Solo incluye
    candidatos donde AMBOS lados del mercado tienen momio — igual que la
    app, para no sugerir nada basado en un solo lado.
    Devuelve lista ordenada por edge descendente.
    """
    candidates = []
    for g in games_out:
        home = teams_by_id.get(g["homeTeamId"])
        away = teams_by_id.get(g["awayTeamId"])
        if not home or not away:
            continue
        if g.get("liveState"):
            continue  # juego ya en curso o terminado, detectado por el pipeline
        game_date_str = g.get("gameDate")
        if game_date_str:
            try:
                game_dt = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
                if game_dt <= datetime.now(timezone.utc):
                    continue  # ya pasó la hora de inicio, aunque liveState no lo haya detectado todavía
            except ValueError:
                pass  # si el formato no se puede parsear, no bloqueamos por esto
        ao = g.get("autoOdds") or {}
        if not ao.get("mlHome") and not ao.get("mlAway"):
            continue  # sin momios, no hay nada que evaluar

        model = build_model(home, away, g.get("homeStarter"), g.get("awayStarter"), g.get("weather"))
        matchup_label = f"{away['abbr']} @ {home['abbr']}"

        home_is_fav = model["homeIsFavorite"]
        fav_abbr = home["abbr"] if home_is_fav else away["abbr"]
        dog_abbr = away["abbr"] if home_is_fav else home["abbr"]

        ml_home, ml_away = ao.get("mlHome"), ao.get("mlAway")
        rl_fav = ao.get("rlHomePrice") if home_is_fav else ao.get("rlAwayPrice")
        rl_dog = ao.get("rlAwayPrice") if home_is_fav else ao.get("rlHomePrice")

        rows = [
            (f"ML {home['abbr']}", model["homeWinProb"], ml_home, ml_away),
            (f"ML {away['abbr']}", model["awayWinProb"], ml_away, ml_home),
            (f"{fav_abbr} -1.5", model["favMinus1_5"], rl_fav, rl_dog),
            (f"{dog_abbr} +1.5", model["dogPlus1_5"], rl_dog, rl_fav),
        ]
        for label, prob, odd, other_odd in rows:
            if not odd or not other_odd:
                continue
            if not is_sane_pregame_odds(odd) or not is_sane_pregame_odds(other_odd):
                continue  # momio fuera de rango razonable — probablemente en vivo o corrupto
            e = edge_pct(prob, odd)
            if e is None:
                continue
            candidates.append({
                "label": label,
                "matchup": matchup_label,
                "odd": odd,
                "prob": prob,
                "edge": e,
                "tier": edge_tier(e),
            })

    candidates.sort(key=lambda c: c["edge"], reverse=True)
    return candidates
