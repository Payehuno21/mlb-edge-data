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


# CALIBRACIÓN — el modelo, al combinar varios factores (abridor + ausencias
# + bullpen + splits + forma) en la misma dirección, puede acumular más
# confianza de la que la realidad respalda. Datos reales de la bitácora
# (210 apuestas liquidadas, días 1-3) mostraron: cuando el modelo dice
# 70-80%, la realidad fue 53.7% (-21pp); cuando dice 80%+, fue 50.0% (-40pp).
# SHRINKAGE_FACTOR comprime la probabilidad hacia 50% proporcionalmente a su
# distancia del centro — corrige justo ese patrón de sobreconfianza en los
# extremos sin tener que tocar cada peso individual del modelo. Revisar este
# valor conforme crezca la muestra: si la calibración mejora, se puede subir
# (acercar a 1.0); si sigue sobreconfiado, bajarlo más.
SHRINKAGE_FACTOR = 0.65


def shrink_prob(p, factor=SHRINKAGE_FACTOR):
    return 0.5 + (p - 0.5) * factor


# Penalización adicional para picks donde el modelo favorece a un desvalido
# (prob_modelo > 50% pero el mercado lo cotiza con odds > 2.0).
# Los datos reales muestran que en estos casos el win rate real es solo 37.5%
# — muy por debajo del 50%+ que el modelo predice. Esta corrección reduce
# el "exceso de confianza contra el mercado" en un 90%, dejando solo el 10%
# del diferencial entre la probabilidad del modelo y la del mercado.
UNDERDOG_PENALTY = 0.9

# Descuento adicional específico para Run Line (±1.5).
# Datos reales (n=50): RL desvalido (+1.5) con odds 2.0-3.0 gana solo 36.4%,
# y los picks de mayor edge perdidos (HOU, AZ, DET, NYY +1.5 con edge 20-25%)
# todos tenían odds ~2.60-2.70. El mercado de RL es más eficiente que ML
# porque ya descuenta la dificultad de ganar POR MÁS de 1.5 carreras.
# Este factor reduce el exceso residual después de UNDERDOG_PENALTY, llevando
# picks como HOU +1.5 (edge 5% tras penalización) a PASS en vez de LEAN.
RL_EXTRA_DISCOUNT = 0.70

# Techo de edge creíble — datos reales (n=26) muestran que picks con edge
# calculado >20% tienen solo 34.6% de win rate, peor que un volado. El modelo
# está tan seguro de sí mismo en esos casos que el mercado lo está corrigiendo
# brutalmente. Cualquier edge calculado por encima de este techo se comprime
# a 20% para evitar que picks sobreconfiados aparezcan como Apuesta Máxima
# o dominen la lista de picks del día.
MAX_CREDIBLE_EDGE = 20.0


def apply_underdog_penalty(prob, dec_odds, is_rl=False):
    """Aplica una penalización a la probabilidad del modelo cuando contradice
    agresivamente al mercado (modelo dice >50% pero odds > 2.0).
    Para Run Line (is_rl=True), aplica un descuento adicional porque el
    mercado de RL es más eficiente — los datos muestran 36% WR real en RL
    desvalido vs el 50%+ que el modelo predice.
    No modifica favoritos ni casos donde el mercado coincide con el modelo.
    """
    if prob <= 0.5 or dec_odds is None or dec_odds < 2.0:
        return prob
    prob_mercado = 1.0 / dec_odds
    exceso = prob - prob_mercado
    if exceso <= 0:
        return prob
    prob_adj = prob - exceso * UNDERDOG_PENALTY
    if is_rl:
        exceso_restante = max(prob_adj - prob_mercado, 0)
        prob_adj = prob_adj - exceso_restante * RL_EXTRA_DISCOUNT
    return prob_adj


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

    home_win_prob = shrink_prob(elo_win_prob(diff))

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
    f5_home_win_prob = shrink_prob(elo_win_prob(f5_diff))
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
    raw = (model_prob - imp) * 100
    # Comprimir edges excesivamente altos — el modelo sobreestima su propia
    # certeza en estos casos, y los datos reales lo confirman.
    return min(raw, MAX_CREDIBLE_EDGE)


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


def is_sane_runline_odds(dec_odds):
    """Para Run Line (+-1.5), los rangos razonables son mas estrictos que ML.
    Un RL -1.5 a mas de 5.00 (20% implícita) es casi siempre un dato
    corrupto o un mercado en vivo.
    """
    imp = implied_prob_decimal(dec_odds)
    if imp is None:
        return False
    return 0.20 <= imp <= 0.92


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
            continue
        game_date_str = g.get("gameDate")
        if game_date_str:
            try:
                game_dt = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
                if game_dt <= datetime.now(timezone.utc):
                    continue
            except ValueError:
                pass
        ao = g.get("autoOdds") or {}
        if not ao.get("mlHome") and not ao.get("mlAway"):
            continue

        model = build_model(home, away, g.get("homeStarter"), g.get("awayStarter"), g.get("weather"))
        matchup_label = f"{away['abbr']} @ {home['abbr']}"

        home_is_fav = model["homeIsFavorite"]
        fav_abbr = home["abbr"] if home_is_fav else away["abbr"]
        dog_abbr = away["abbr"] if home_is_fav else home["abbr"]

        ml_home, ml_away = ao.get("mlHome"), ao.get("mlAway")
        rl_fav = ao.get("rlHomePrice") if home_is_fav else ao.get("rlAwayPrice")
        rl_dog = ao.get("rlAwayPrice") if home_is_fav else ao.get("rlHomePrice")

        rows = [
            (f"ML {home['abbr']}", model["homeWinProb"], ml_home, ml_away, False),
            (f"ML {away['abbr']}", model["awayWinProb"], ml_away, ml_home, False),
            (f"{fav_abbr} -1.5", model["favMinus1_5"], rl_fav, rl_dog, True),
            (f"{dog_abbr} +1.5", model["dogPlus1_5"], rl_dog, rl_fav, True),
        ]
        for label, prob, odd, other_odd, is_rl in rows:
            if not odd or not other_odd:
                continue
            sane_fn = is_sane_runline_odds if is_rl else is_sane_pregame_odds
            if not sane_fn(odd) or not sane_fn(other_odd):
                continue
            prob_adj = apply_underdog_penalty(prob, odd, is_rl=is_rl)
            e = edge_pct(prob_adj, odd)
            if e is None:
                continue
            candidates.append({
                "label": label,
                "matchup": matchup_label,
                "odd": odd,
                "prob": prob_adj,
                "edge": e,
                "tier": edge_tier(e),
            })

    candidates.sort(key=lambda c: c["edge"], reverse=True)
    return candidates


def top_diverse_picks(candidates, n=3):
    """Devuelve hasta n candidatos, sin repetir el mismo juego (matchup) más
    de una vez — para que el Top N muestre partidos distintos en vez de
    varios mercados del mismo cruce. Misma lógica que usa la app.
    """
    seen_matchups = set()
    diverse = []
    for c in candidates:
        if c["matchup"] in seen_matchups:
            continue
        seen_matchups.add(c["matchup"])
        diverse.append(c)
        if len(diverse) >= n:
            break
    return diverse
