import { useState, useMemo, useEffect, useCallback } from "react";
import { RefreshCw, ChevronDown, ChevronUp, AlertCircle, Trophy, Info, Pencil, CheckCircle2, Flame, Search, Activity, ListChecks, LayoutGrid } from "lucide-react";

// ---------------------------------------------------------------------------
// MLB EDGE — v5
// - Sin abridores (no afectaban el cálculo; se quitan por completo de la UI)
// - Momios en formato DECIMAL en toda la app
// - Mercado F5 (primeras 5 entradas): ML, RL, Total — modelo independiente
// - Run Line dinámico: el modelo decide quién es favorito (-1.5) y quién
//   es desvalido (+1.5), sin asumir que siempre es el local. Hay un botón
//   para invertir manualmente si el mercado real difiere del modelo.
// - Layout denso tipo terminal: filas tabulares, momios decimales inline.
// ---------------------------------------------------------------------------
const DATA_JSON_URL = "https://raw.githubusercontent.com/Payehuno21/mlb-edge-data/main/data.json";

const TEAM_COLORS = {
  ARI:"#A71930", ATL:"#13274F", BAL:"#DF4601", BOS:"#BD3039", CHC:"#0E3386",
  CWS:"#27251F", CIN:"#C6011F", CLE:"#00385D", COL:"#33006F", DET:"#0C2340",
  HOU:"#EB6E1F", KC:"#004687", LAA:"#BA0021", LAD:"#005A9C", MIA:"#00A3E0",
  MIL:"#12284B", MIN:"#002B5C", NYM:"#FF5910", NYY:"#0C2340", OAK:"#003831",
  ATH:"#003831", PHI:"#E81828", PIT:"#FDB827", SD:"#2F241D", SF:"#FD5A1E",
  SEA:"#0C2C56", STL:"#C41E3A", TB:"#8FBCE6", TEX:"#003278", TOR:"#134A8E",
  WSH:"#AB0003"
};

// Logos oficiales de MLB vía el CDN público mlbstatic.com, indexados por
// team id (el mismo id que usa MLB Stats API en todo el pipeline).
function teamLogoUrl(teamId) {
  if (!teamId) return null;
  return `https://www.mlbstatic.com/team-logos/${teamId}.svg`;
}

function TeamLogo({ teamId, size = 20 }) {
  const [failed, setFailed] = useState(false);
  const url = teamLogoUrl(teamId);
  if (!url || failed) return null;
  return (
    <img
      src={url}
      alt=""
      width={size}
      height={size}
      onError={() => setFailed(true)}
      style={{ objectFit: "contain", flexShrink: 0 }}
    />
  );
}

const TEAMS_FALLBACK = [
  { abbr: "TB",  name: "Tampa Bay Rays",          winPct: 0.643, elo: 1586 },
  { abbr: "NYY", name: "New York Yankees",        winPct: 0.610, elo: 1566 },
  { abbr: "TOR", name: "Toronto Blue Jays",       winPct: 0.483, elo: 1490 },
  { abbr: "BAL", name: "Baltimore Orioles",       winPct: 0.467, elo: 1480 },
  { abbr: "BOS", name: "Boston Red Sox",          winPct: 0.431, elo: 1459 },
  { abbr: "CLE", name: "Cleveland Guardians",     winPct: 0.557, elo: 1534 },
  { abbr: "CWS", name: "Chicago White Sox",       winPct: 0.542, elo: 1525 },
  { abbr: "MIN", name: "Minnesota Twins",         winPct: 0.450, elo: 1470 },
  { abbr: "KC",  name: "Kansas City Royals",      winPct: 0.373, elo: 1424 },
  { abbr: "DET", name: "Detroit Tigers",          winPct: 0.367, elo: 1420 },
  { abbr: "SEA", name: "Seattle Mariners",        winPct: 0.517, elo: 1510 },
  { abbr: "ATH", name: "Athletics",               winPct: 0.475, elo: 1485 },
  { abbr: "TEX", name: "Texas Rangers",           winPct: 0.475, elo: 1485 },
  { abbr: "HOU", name: "Houston Astros",          winPct: 0.443, elo: 1466 },
  { abbr: "LAA", name: "Los Angeles Angels",      winPct: 0.383, elo: 1430 },
  { abbr: "ATL", name: "Atlanta Braves",          winPct: 0.667, elo: 1600 },
  { abbr: "WSH", name: "Washington Nationals",    winPct: 0.517, elo: 1510 },
  { abbr: "PHI", name: "Philadelphia Phillies",   winPct: 0.508, elo: 1505 },
  { abbr: "NYM", name: "New York Mets",           winPct: 0.441, elo: 1465 },
  { abbr: "MIA", name: "Miami Marlins",           winPct: 0.433, elo: 1460 },
  { abbr: "MIL", name: "Milwaukee Brewers",       winPct: 0.625, elo: 1575 },
  { abbr: "STL", name: "St. Louis Cardinals",     winPct: 0.544, elo: 1526 },
  { abbr: "PIT", name: "Pittsburgh Pirates",      winPct: 0.533, elo: 1520 },
  { abbr: "CHC", name: "Chicago Cubs",            winPct: 0.533, elo: 1520 },
  { abbr: "CIN", name: "Cincinnati Reds",         winPct: 0.517, elo: 1510 },
  { abbr: "LAD", name: "Los Angeles Dodgers",     winPct: 0.644, elo: 1586 },
  { abbr: "SD",  name: "San Diego Padres",        winPct: 0.552, elo: 1531 },
  { abbr: "ARI", name: "Arizona Diamondbacks",    winPct: 0.534, elo: 1520 },
  { abbr: "SF",  name: "San Francisco Giants",    winPct: 0.390, elo: 1434 },
  { abbr: "COL", name: "Colorado Rockies",        winPct: 0.367, elo: 1420 },
].sort((a, b) => a.name.localeCompare(b.name));

// ---------------------------------------------------------------------------
// FETCH del pipeline autónomo
// ---------------------------------------------------------------------------
async function fetchPipelineData(url) {
  if (!url) return null;
  let res;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch (networkErr) {
    throw new Error(`Fallo de red/CORS: ${networkErr.message || networkErr}`);
  }
  if (!res.ok) throw new Error(`GitHub respondió HTTP ${res.status}`);
  try {
    return await res.json();
  } catch (parseErr) {
    throw new Error(`JSON inválido: ${parseErr.message || parseErr}`);
  }
}

function normalizeTeamsFromPipeline(payload) {
  if (!payload?.teams) return [];
  return payload.teams.map(t => ({
    id: t.id, abbr: t.abbr, name: t.name, winPct: t.winPct,
    homeWinPct: t.homeWinPct, awayWinPct: t.awayWinPct, last10: t.last10,
    elo: t.elo, runsPerGame: t.runsPerGame, staffEra: t.staffEra,
    topPowerHitter: t.topPowerHitter, topContactHitter: t.topContactHitter,
  })).sort((a, b) => a.name.localeCompare(b.name));
}

function normalizeGamesFromPipeline(payload, teamsById) {
  if (!payload?.games) return [];
  return payload.games.map(g => {
    const home = teamsById[g.homeTeamId];
    const away = teamsById[g.awayTeamId];
    if (!home || !away) return null;
    const time = new Date(g.gameDate);
    return {
      gamePk: g.gamePk,
      timeLabel: time.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      dateStr: g.gameDateStr ?? g.gameDate?.slice(0, 10),
      home, away,
      homeStarter: g.homeStarter ?? null,
      awayStarter: g.awayStarter ?? null,
      weather: g.weather ?? null,
      venue: g.venue ?? null,
      autoOdds: g.autoOdds ?? null,
    };
  }).filter(Boolean);
}

// ---------------------------------------------------------------------------
// Utilidades de momios — TODO en formato DECIMAL
// ---------------------------------------------------------------------------
function impliedProbDecimal(decOdds) {
  const d = Number(decOdds);
  if (!d || d <= 1) return null;
  return 1 / d;
}

function edgePct(modelProb, decOdds) {
  const imp = impliedProbDecimal(decOdds);
  if (imp === null || modelProb === null || modelProb === undefined) return null;
  return (modelProb - imp) * 100;
}

function edgeTier(edge) {
  if (edge === null || edge === undefined || Number.isNaN(edge)) return null;
  if (edge >= 6) return { label: "BET", color: "#39FF7A", glow: true };
  if (edge >= 2.5) return { label: "LEAN", color: "#FFB319", glow: false };
  if (edge > -2.5) return { label: "PASS", color: "#6B7280", glow: false };
  return { label: "FADE", color: "#FF4655", glow: false };
}

function kellyFraction(modelProb, decOdds, fractionalMultiplier = 0.25) {
  const d = Number(decOdds);
  if (!d || d <= 1 || modelProb === null || modelProb === undefined) return null;
  const b = d - 1;
  const p = modelProb;
  const q = 1 - p;
  const fullKelly = (b * p - q) / b;
  if (fullKelly <= 0) return 0;
  return fullKelly * fractionalMultiplier;
}

function eloWinProb(diff) {
  return 1 / (1 + Math.pow(10, -diff / 400));
}

// ---------------------------------------------------------------------------
// MERCADO SIN VIG (no-vig) — referencia independiente del modelo, inspirada
// en el patrón de jrey999/mlb-positive-ev: promedia la probabilidad implícita
// de varias casas para un mismo lado, y remueve el margen (vig) comparando
// contra el lado opuesto si está disponible.
// ---------------------------------------------------------------------------
function averageImpliedProb(oddsArray) {
  const probs = oddsArray.map(impliedProbDecimal).filter(p => p !== null);
  if (!probs.length) return null;
  return probs.reduce((s, p) => s + p, 0) / probs.length;
}

// Si tenemos probabilidad implícita promedio de AMBOS lados de un mercado de
// 2 resultados, removemos el vig normalizando para que sumen 100%.
function noVigProb(probSide, probOtherSide) {
  if (probSide === null) return null;
  if (probOtherSide === null) return probSide; // sin el otro lado, no se puede de-vigear
  const total = probSide + probOtherSide;
  if (total <= 0) return null;
  return probSide / total;
}

// (La función MarketCompare vive más abajo, en el bloque de UI reconstruido)

// ---------------------------------------------------------------------------
// MODELO — juego completo + F5 (primeras 5 entradas) como cálculo separado.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// MODELO — ensemble de señales reales, no heurística de un solo número.
// Componentes y peso aproximado en puntos Elo:
//   1. Elo de equipo (temporada)              — base
//   2. Splits home/away                        — ajuste de contexto
//   3. Forma reciente (últimos 10)              — ajuste de momentum
//   4. ABRIDOR (ERA/WHIP/K9 del día)            — peso fuerte, ~igual o mayor
//      que el Elo de equipo; la literatura de apuestas deportivas es clara en
//      que el abridor puede hacer favorito a un equipo inferior (Odds Shark:
//      "an average team with a stud pitcher is often favored against a
//      superior team"). Liga promedio ERA ≈ 4.20.
//   5. BULLPEN (ERA de staff como proxy)        — peso menor, entra el 6to+
//   6. CLIMA (viento, temperatura)               — afecta sobre todo el TOTAL
//      de carreras, no tanto quién gana; viento de salida sube el total,
//      viento de entrada lo baja, frío reduce ofensiva.
// ---------------------------------------------------------------------------
function pitcherScore(starter) {
  if (!starter || starter.era == null) return 0;
  const leagueEra = 4.20;
  const leagueWhip = 1.30;
  const leagueK9 = 8.5;
  const eraAdj = (leagueEra - starter.era) * 55;   // peso dominante
  const whipAdj = (leagueWhip - (starter.whip ?? leagueWhip)) * 70;
  const k9Adj = ((starter.k9 ?? leagueK9) - leagueK9) * 5;
  return eraAdj + whipAdj + k9Adj;
}

function weatherRunFactor(weather) {
  if (!weather) return 1.0;
  let factor = 1.0;
  // viento: dirección 0-360°, aproximamos "de salida" como soplando hacia el
  // jardín central (rango amplio 200-340°) y "de entrada" lo opuesto.
  if (weather.windMph >= 8) {
    if (weather.windDirDeg >= 200 && weather.windDirDeg <= 340) factor += 0.05;
    else if (weather.windDirDeg <= 60 || weather.windDirDeg >= 300) factor -= 0.04;
  }
  if (weather.tempF != null) {
    if (weather.tempF > 85) factor += 0.025;
    if (weather.tempF < 50) factor -= 0.03;
  }
  if (weather.precipProb != null && weather.precipProb > 50) factor -= 0.02;
  return factor;
}

function buildModel(matchup) {
  const { home, away } = matchup;

  let diff = (home.elo + 24) - away.elo;

  const homeSplit = ((home.homeWinPct ?? home.winPct ?? 0.5) - 0.5) * 220;
  const awaySplit = ((away.awayWinPct ?? away.winPct ?? 0.5) - 0.5) * 220;
  diff += homeSplit - awaySplit;

  const formAdj = (((home.last10 ?? 5) - (away.last10 ?? 5)) / 10) * 70;
  diff += formAdj;

  // Abridor: si el abridor LOCAL es mejor, suma a favor del local, y viceversa.
  const homeSP = pitcherScore(matchup.homeStarter);
  const awaySP = pitcherScore(matchup.awayStarter);
  diff += homeSP - awaySP;

  // Bullpen (proxy de ERA de staff completo) — peso menor que el abridor.
  const bullpenAdj = ((away.staffEra ?? 4.0) - (home.staffEra ?? 4.0)) * 18;
  diff += bullpenAdj;

  const homeWinProb = eloWinProb(diff);

  const leagueAvgTotal = 8.6;
  const offenseFactor = ((home.runsPerGame ?? 4.3) + (away.runsPerGame ?? 4.3)) / 8.6;
  const pitchingFactor = ((matchup.homeStarter?.era ?? 4.2) + (matchup.awayStarter?.era ?? 4.2)) / 8.4;
  let projectedTotal = leagueAvgTotal * (0.45 * offenseFactor + 0.35 * pitchingFactor + 0.20);
  const wFactor = weatherRunFactor(matchup.weather);
  projectedTotal *= wFactor;
  projectedTotal *= matchup.parkFactor ?? 1.0;

  const expectedMargin = (diff / 400) * 1.4;

  const homeIsFavorite = homeWinProb >= 0.5;
  // Probabilidad de que el FAVORITO cubra -1.5. Pendiente calibrada contra
  // referencias reales de mercado: favoritos moderados (55-65% ML) suelen
  // cotizar el RL -1.5 con momio positivo (cobertura ~35-40%), no 15-20%
  // como daba la pendiente anterior, demasiado agresiva.
  const favMinus1_5 = 1 / (1 + Math.exp(-(Math.abs(expectedMargin) - 1.5) / 2.5));
  const dogPlus1_5 = 1 - favMinus1_5;

  const f5Diff = diff * 0.62;
  const f5HomeWinProb = eloWinProb(f5Diff);
  const f5ExpectedMargin = (f5Diff / 400) * 1.4;
  const f5HomeIsFavorite = f5HomeWinProb >= 0.5;
  const f5FavMinus0_5 = 1 / (1 + Math.exp(-(Math.abs(f5ExpectedMargin) - 0.5) / 1.4));
  const f5DogPlus0_5 = 1 - f5FavMinus0_5;
  const f5ProjectedTotal = projectedTotal * 0.56;

  return {
    homeWinProb,
    awayWinProb: 1 - homeWinProb,
    projectedTotal: Math.round(projectedTotal * 10) / 10,
    homeIsFavorite,
    favMinus1_5,
    dogPlus1_5,
    weatherFactor: wFactor,
    f5: {
      homeWinProb: f5HomeWinProb,
      awayWinProb: 1 - f5HomeWinProb,
      projectedTotal: Math.round(f5ProjectedTotal * 10) / 10,
      homeIsFavorite: f5HomeIsFavorite,
      favMinus0_5: f5FavMinus0_5,
      dogPlus0_5: f5DogPlus0_5,
    },
  };
}

function suggestPropsFromPipeline(matchup) {
  const { home, away } = matchup;
  const props = [];
  if (home?.topPowerHitter?.hrRate != null) {
    const gameProb = 1 - Math.pow(1 - home.topPowerHitter.hrRate, 4.2);
    props.push({ type: "HR", player: home.topPowerHitter.name, note: `${(home.topPowerHitter.hrRate * 100).toFixed(1)}% HR/turno (${home.abbr})`, confidence: Math.min(gameProb * 100, 38) });
  }
  if (away?.topContactHitter?.avg != null) {
    const gameProb = 1 - Math.pow(1 - away.topContactHitter.avg, 4.0);
    props.push({ type: "1+ Hit", player: away.topContactHitter.name, note: `AVG ${away.topContactHitter.avg.toFixed(3)} (${away.abbr})`, confidence: Math.min(gameProb * 100, 88) });
  }
  return props.slice(0, 2);
}

// ---------------------------------------------------------------------------
// BITÁCORA
// ---------------------------------------------------------------------------
const LOG_STORAGE_KEY = "mlbEdgeBetLog_v2";

function loadBetLog() {
  try {
    const raw = localStorage.getItem(LOG_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}
function saveBetLog(entries) {
  try { localStorage.setItem(LOG_STORAGE_KEY, JSON.stringify(entries)); }
  catch (e) { console.error("No se pudo guardar la bitácora:", e); }
}
function profitForEntry(entry) {
  if (!entry.result || entry.result === "pending") return 0;
  if (entry.result === "lost") return -Number(entry.stake || 0);
  if (entry.result === "push") return 0;
  const d = Number(entry.odds);
  if (!d) return 0;
  return Number(entry.stake || 0) * (d - 1);
}
function summarizeLog(entries) {
  const settled = entries.filter(e => e.result === "won" || e.result === "lost" || e.result === "push");
  const totalStaked = settled.reduce((s, e) => s + Number(e.stake || 0), 0);
  const totalProfit = settled.reduce((s, e) => s + profitForEntry(e), 0);
  const wins = settled.filter(e => e.result === "won").length;
  const losses = settled.filter(e => e.result === "lost").length;
  const decided = wins + losses;
  return {
    totalBets: entries.length,
    pending: entries.filter(e => !e.result || e.result === "pending").length,
    wins, losses,
    winRate: decided > 0 ? (wins / decided) * 100 : null,
    totalStaked, totalProfit,
    roi: totalStaked > 0 ? (totalProfit / totalStaked) * 100 : null,
  };
}

// ---------------------------------------------------------------------------
// COTEJO AUTOMÁTICO — compara una apuesta registrada contra el resultado
// final real del juego (vía pipeline). Limitación honesta: el F5 (primeras
// 5 entradas) NO se puede cotejar automáticamente porque MLB Stats API no
// expone el score parcial de 5 entradas de forma confiable en este pipeline
// — esas entradas se quedan en "pending" para que el usuario las marque a mano.
function gradeEntry(entry, resultsByGamePk) {
  if (entry.isF5) return null; // no cotejable automáticamente
  if (!entry.gamePk) return null;
  const r = resultsByGamePk[entry.gamePk];
  if (!r || r.homeScore == null || r.awayScore == null) return null;

  const homeWon = r.homeScore > r.awayScore;
  const margin = r.homeScore - r.awayScore; // positivo = ganó el local
  const totalRuns = r.homeScore + r.awayScore;

  if (entry.betType === "ML") {
    const sideWon = entry.side === "home" ? homeWon : !homeWon;
    if (r.homeScore === r.awayScore) return null; // no hay empates en MLB, pero por seguridad
    return sideWon ? "won" : "lost";
  }

  if (entry.betType === "RL") {
    // line ya viene con signo: -1.5 para el lado que da puntos, +1.5 para el que recibe
    const sideMargin = entry.side === "home" ? margin : -margin;
    const covered = sideMargin + entry.line > 0;
    const pushed = sideMargin + entry.line === 0; // no debería pasar con .5, pero por seguridad
    if (pushed) return "push";
    return covered ? "won" : "lost";
  }

  if (entry.betType === "Total") {
    if (entry.line == null) return null;
    if (totalRuns === entry.line) return "push";
    const overHit = totalRuns > entry.line;
    const sideWon = entry.side === "over" ? overHit : !overHit;
    return sideWon ? "won" : "lost";
  }

  return null; // props y otros tipos no estructurados se cotejan a mano
}

function autoGradeLog(entries, resultsByGamePk) {
  return entries.map(e => {
    if (e.result && e.result !== "pending") return e; // ya tiene resultado manual, no lo tocamos
    const graded = gradeEntry(e, resultsByGamePk);
    return graded ? { ...e, result: graded, autoGraded: true } : e;
  });
}

// ---------------------------------------------------------------------------
// REPORTES — agregación de la bitácora por día/semana/mes.
// ---------------------------------------------------------------------------
function getWeekKey(dateISO) {
  const d = new Date(dateISO + "T12:00:00");
  const day = d.getDay() || 7; // lunes=1 ... domingo=7
  const monday = new Date(d);
  monday.setDate(d.getDate() - day + 1);
  return monday.toISOString().slice(0, 10);
}

function getMonthKey(dateISO) {
  return dateISO.slice(0, 7); // YYYY-MM
}

function buildReport(entries, period) {
  const settled = entries.filter(e => (e.result === "won" || e.result === "lost" || e.result === "push") && e.dateISO);
  const groups = {};
  for (const e of settled) {
    const key = period === "day" ? e.dateISO : period === "week" ? getWeekKey(e.dateISO) : getMonthKey(e.dateISO);
    if (!groups[key]) groups[key] = [];
    groups[key].push(e);
  }
  return Object.entries(groups)
    .map(([key, group]) => {
      const staked = group.reduce((s, e) => s + Number(e.stake || 0), 0);
      const profit = group.reduce((s, e) => s + profitForEntry(e), 0);
      const wins = group.filter(e => e.result === "won").length;
      const losses = group.filter(e => e.result === "lost").length;
      return {
        key, count: group.length, wins, losses,
        staked, profit,
        roi: staked > 0 ? (profit / staked) * 100 : null,
      };
    })
    .sort((a, b) => b.key.localeCompare(a.key));
}

function parsePropsText(text) {
  if (!text?.trim()) return [];
  return text.split("\n").map(line => {
    const parts = line.split("|").map(p => p.trim());
    if (parts.length < 2) return null;
    const [player, type, conf] = parts;
    if (!player || !type) return null;
    return { player, type, confidence: conf ? Number(conf.replace("%", "")) : null };
  }).filter(Boolean).slice(0, 6);
}

// ---------------------------------------------------------------------------
// SALUD DEL MODELO — mide el desempeño real del modelo contra resultados ya
// liquidados en la bitácora. No ajusta nada solo; solo te muestra la verdad
// de los números para que TÚ decidas si algo necesita revisión. Con pocas
// muestras (<30) el resultado es ruido estadístico, no señal — se marca
// explícitamente para no generar falsa confianza.
// ---------------------------------------------------------------------------
const MIN_SAMPLE_SIZE = 30;

function analyzeModelHealth(entries) {
  const settled = entries.filter(e => e.result === "won" || e.result === "lost");

  const byGroup = (keyFn) => {
    const groups = {};
    for (const e of settled) {
      const key = keyFn(e);
      if (!key) continue;
      if (!groups[key]) groups[key] = { wins: 0, losses: 0 };
      if (e.result === "won") groups[key].wins++;
      else groups[key].losses++;
    }
    return Object.entries(groups).map(([key, g]) => ({
      key,
      wins: g.wins,
      losses: g.losses,
      total: g.wins + g.losses,
      winRate: (g.wins / (g.wins + g.losses)) * 100,
      sufficient: (g.wins + g.losses) >= MIN_SAMPLE_SIZE,
    })).sort((a, b) => b.total - a.total);
  };

  const byMarket = byGroup(e => e.betType ?? (e.market?.split(" ")[0] ?? null));
  const byTier = byGroup(e => {
    if (e.edge === null || e.edge === undefined) return null;
    return edgeTier(e.edge)?.label ?? null;
  });

  // Calibración: agrupa por bandas de probabilidad del modelo y compara contra
  // el win rate real observado en esa banda. Una banda bien calibrada tiene
  // winRate real ≈ centro de la banda.
  const calibrationBands = [
    { label: "50-60%", min: 0.5, max: 0.6 },
    { label: "60-70%", min: 0.6, max: 0.7 },
    { label: "70%+", min: 0.7, max: 1.01 },
  ];
  const calibration = calibrationBands.map(band => {
    const inBand = settled.filter(e => e.prob != null && e.prob >= band.min && e.prob < band.max);
    const wins = inBand.filter(e => e.result === "won").length;
    return {
      label: band.label,
      total: inBand.length,
      winRate: inBand.length ? (wins / inBand.length) * 100 : null,
      sufficient: inBand.length >= MIN_SAMPLE_SIZE,
    };
  }).filter(b => b.total > 0);

  return {
    totalSettled: settled.length,
    overallSufficient: settled.length >= MIN_SAMPLE_SIZE,
    byMarket,
    byTier,
    calibration,
  };
}


// ---------------------------------------------------------------------------
// UI — componentes base. Paleta: fondo casi negro con tinte azulado (#06070A),
// acento principal cian-verde neón (#00FFB2) con glow sutil, acento secundario
// magenta para "pro/máxima" (#FF2E97), rojo-coral para negativo (#FF3D71).
// Tipografía: Big Shoulders (títulos/badges) + Space Grotesk (cuerpo) +
// JetBrains Mono (números). Glow usado como acento de borde/línea, nunca
// detrás del texto, para no comprometer legibilidad.
// ---------------------------------------------------------------------------
function EdgeMeter({ value }) {
  const clamped = Math.max(-25, Math.min(25, value ?? 0));
  const pct = ((clamped + 25) / 50) * 100;
  const positive = (value ?? 0) >= 0;
  return (
    <div className="h-[5px] w-full rounded-full bg-meter-track overflow-hidden relative">
      <div className="absolute left-1/2 top-0 h-full w-px bg-white/15" />
      <div
        className="h-full rounded-full transition-all duration-500 ease-out"
        style={{
          width: `${Math.abs(pct - 50)}%`,
          marginLeft: pct >= 50 ? "50%" : `${pct}%`,
          background: positive ? "#00FFB2" : "#FF3D71",
        }}
      />
    </div>
  );
}

function TierChip({ edge, size = "sm" }) {
  const tier = edgeTier(edge);
  if (!tier) return null;
  const colorMap = { BET: "#00FFB2", LEAN: "#FFB200", PASS: "#6B7280", FADE: "#FF3D71" };
  const color = colorMap[tier.label] ?? tier.color;
  const sizeClasses = size === "lg" ? "text-xs px-3 py-1.5" : "fs-9 px-2 py-1";
  return (
    <span
      className={`font-display font-bold uppercase tracking-wide rounded-md shrink-0 ${sizeClasses}`}
      style={{ color, background: `${color}1A`, boxShadow: tier.label === "BET" ? `0 0 10px ${color}55` : "none" }}
    >
      {tier.label}
    </span>
  );
}

function OddsInput({ value, onChange, placeholder = "1.91", highlight }) {
  return (
    <input
      type="text"
      inputMode="decimal"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value.replace(",", "."))}
      placeholder={placeholder}
      className="w-full bg-input ring-1 focus-ring-accent rounded-lg px-2 py-2 text-center font-mono text-sm text-brand placeholder:text-white/20 transition-all"
      style={{ boxShadow: highlight ? "0 0 0 1px rgba(0,255,178,0.4) inset" : "0 0 0 1px rgba(255,255,255,0.1) inset" }}
    />
  );
}

function TeamSelect({ value, onChange, exclude, label, teams }) {
  return (
    <div className="flex-1">
      <label className="fs-9 uppercase tracking-wider text-white/35 font-semibold block mb-1">{label}</label>
      <select
        value={value?.abbr ?? ""}
        onChange={(e) => onChange(teams.find(t => t.abbr === e.target.value) ?? null)}
        className="w-full bg-input ring-1 ring-white/10 focus-ring-accent rounded-lg px-3 py-2.5 text-sm font-bold text-brand appearance-none"
      >
        <option value="">Equipo…</option>
        {teams.filter(t => t.abbr !== exclude).map(t => (
          <option key={t.abbr} value={t.abbr}>{t.name}</option>
        ))}
      </select>
    </div>
  );
}

// Fila de momio: columnas con ancho fijo y espacio real entre ellas, para que
// el número del momio y el edge no compitan visualmente con la barra.
function BetRow({ label, prob, oddsValue, edge, onOddsChange, bankroll, onLog, logContext, bothSidesFilled = true }) {
  const kelly = oddsValue ? kellyFraction(prob, oddsValue) : null;
  const stake = kelly && kelly > 0 && bankroll ? kelly * Number(bankroll) : null;
  const canLog = onLog && oddsValue;
  const showTier = oddsValue && bothSidesFilled;
  const positive = (edge ?? 0) >= 0;
  return (
    <div className="grid items-center gap-3 py-2.5 border-b border-white/[0.04] last:border-b-0" style={{ gridTemplateColumns: "56px 1fr 84px 80px 40px" }}>
      <span className="text-sm font-bold text-brand">{label}</span>
      <div>
        <div className="fs-9 text-white/35 mb-1">modelo {(prob * 100).toFixed(0)}%</div>
        <EdgeMeter value={showTier ? edge : null} />
      </div>
      <OddsInput value={oddsValue} onChange={onOddsChange} highlight={showTier && positive} />
      <div className="flex flex-col items-end gap-1">
        {showTier ? (
          <>
            <span className="font-mono text-sm font-bold" style={{ color: positive ? "#00FFB2" : "#FF3D71" }}>
              {edge !== null && edge !== undefined && !Number.isNaN(edge) ? `${edge > 0 ? "+" : ""}${edge.toFixed(1)}%` : "—"}
            </span>
            <TierChip edge={edge} />
          </>
        ) : oddsValue ? (
          <span className="fs-9 text-white/25 italic text-right">falta el otro lado</span>
        ) : (
          <span className="font-mono text-sm text-white/20">—</span>
        )}
        {stake && stake > 0 && showTier && <span className="fs-9 font-mono text-amber-300/80">${stake.toFixed(0)}</span>}
      </div>
      {canLog ? (
        <button
          onClick={() => onLog({ ...logContext, label, prob, odds: oddsValue, edge, stake: stake ?? 0 })}
          title="Agregar a la bitácora"
          className="w-7 h-7 rounded-full bg-accent-chip ring-1 ring-accent-30 text-accent text-base font-bold flex items-center justify-center active:scale-90 transition-transform justify-self-end"
        >
          +
        </button>
      ) : <span />}
    </div>
  );
}

function RunLineSection({ model, oddsKeyFav, oddsKeyDog, invertedKey = "rlInverted", odds, setOdds, homeAbbr, awayAbbr, bankroll, onLog, logContext, line = 1.5 }) {
  const modelFavIsHome = model.homeIsFavorite;
  const inverted = !!odds[invertedKey];
  const favIsHome = inverted ? !modelFavIsHome : modelFavIsHome;

  const favAbbr = favIsHome ? homeAbbr : awayAbbr;
  const dogAbbr = favIsHome ? awayAbbr : homeAbbr;
  const favProb = model.favMinus1_5 ?? model.favMinus0_5;
  const dogProb = model.dogPlus1_5 ?? model.dogPlus0_5;

  const favEdge = edgePct(favProb, odds[oddsKeyFav]);
  const dogEdge = edgePct(dogProb, odds[oddsKeyDog]);
  const bothFilled = !!(odds[oddsKeyFav] && odds[oddsKeyDog]);

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <p className="fs-10 uppercase tracking-wider text-white/35 font-semibold">Run line ±{line}</p>
        <button
          onClick={() => setOdds({ ...odds, [invertedKey]: !inverted })}
          className="fs-9 text-white/30 font-mono flex items-center gap-1 hover:text-white/50"
          title="Invertir favorito/desvalido si el mercado real difiere del modelo"
        >
          ⇄ {inverted ? "invertido" : "modelo"}
        </button>
      </div>
      <div>
        <BetRow label={`${favAbbr} -${line}`} prob={favProb} oddsValue={odds[oddsKeyFav]} edge={favEdge} onOddsChange={(v) => setOdds({ ...odds, [oddsKeyFav]: v })} bankroll={bankroll} onLog={onLog} logContext={{ ...logContext, market: `Run Line -${line}` }} bothSidesFilled={bothFilled} />
        <BetRow label={`${dogAbbr} +${line}`} prob={dogProb} oddsValue={odds[oddsKeyDog]} edge={dogEdge} onOddsChange={(v) => setOdds({ ...odds, [oddsKeyDog]: v })} bankroll={bankroll} onLog={onLog} logContext={{ ...logContext, market: `Run Line +${line}` }} bothSidesFilled={bothFilled} />
      </div>
    </div>
  );
}

function MarketCompare({ oddsA, oddsB, onChangeA, onChangeB, labelA, labelB }) {
  const probA = averageImpliedProb(oddsA.filter(Boolean));
  const probB = averageImpliedProb(oddsB.filter(Boolean));
  const fairA = noVigProb(probA, probB);
  const fairB = noVigProb(probB, probA);

  const renderInputs = (values, onChange) => (
    <div className="flex gap-1.5">
      {[0, 1, 2].map((i) => (
        <OddsInput key={i} value={values[i]} onChange={(v) => onChange(i, v)} placeholder={i === 0 ? "1.91" : "—"} />
      ))}
    </div>
  );

  return (
    <div className="rounded-lg bg-white/[0.02] ring-1 ring-white/5 px-3 py-3 space-y-2">
      <p className="fs-9 uppercase tracking-wider text-white/30 font-semibold">Comparar casas (sin vig)</p>
      <div className="grid items-center gap-2" style={{ gridTemplateColumns: "48px 1fr 56px" }}>
        <span className="fs-10 font-bold text-white/60">{labelA}</span>
        {renderInputs(oddsA, onChangeA)}
        <span className="fs-10 font-mono text-amber-200 text-right">{fairA !== null ? `${(fairA * 100).toFixed(0)}%` : "—"}</span>
      </div>
      <div className="grid items-center gap-2" style={{ gridTemplateColumns: "48px 1fr 56px" }}>
        <span className="fs-10 font-bold text-white/60">{labelB}</span>
        {renderInputs(oddsB, onChangeB)}
        <span className="fs-10 font-mono text-amber-200 text-right">{fairB !== null ? `${(fairB * 100).toFixed(0)}%` : "—"}</span>
      </div>
    </div>
  );
}

function PropsPanel({ value, onChange, autoProps, onLog, logContext, bankroll }) {
  const [editing, setEditing] = useState(false);
  const [propOdds, setPropOdds] = useState({});
  const parsedManual = useMemo(() => parsePropsText(value), [value]);
  const hasAuto = autoProps && autoProps.length > 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <p className="fs-10 uppercase tracking-wider text-white/35 font-semibold flex items-center gap-1.5"><Flame size={12}/> Props {hasAuto && <CheckCircle2 size={12} className="text-accent" />}</p>
        {!hasAuto && (
          <button onClick={() => setEditing(!editing)} className="flex items-center gap-1 fs-9 text-white/40 font-semibold uppercase">
            <Pencil size={10} /> {editing ? "listo" : "pegar"}
          </button>
        )}
      </div>
      {hasAuto ? (
        <div className="space-y-2">
          {autoProps.map((p, i) => {
            const oddsVal = propOdds[i] ?? "";
            const stake = oddsVal && bankroll ? (kellyFraction(p.confidence / 100, oddsVal) ?? 0) * Number(bankroll) : null;
            return (
              <div key={i} className="grid items-center gap-2" style={{ gridTemplateColumns: "1fr 40px 64px 40px 28px" }}>
                <span className="text-sm font-bold text-brand truncate">{p.player} <span className="text-white/40 font-normal">· {p.type}</span></span>
                <span className="fs-10 font-mono text-amber-300 text-right">{p.confidence.toFixed(0)}%</span>
                <OddsInput value={oddsVal} onChange={(v) => setPropOdds({ ...propOdds, [i]: v })} />
                <span className="fs-9 font-mono text-amber-300/80 text-right">{stake && stake > 0 ? `$${stake.toFixed(0)}` : ""}</span>
                {onLog && oddsVal ? (
                  <button onClick={() => onLog({ ...logContext, market: "Prop", label: `${p.player} · ${p.type}`, prob: p.confidence / 100, odds: oddsVal, edge: null, stake: stake ?? 0 })} className="w-6 h-6 rounded-full bg-accent-chip ring-1 ring-accent-30 text-accent text-sm font-bold flex items-center justify-center justify-self-end">+</button>
                ) : <span />}
              </div>
            );
          })}
        </div>
      ) : editing ? (
        <textarea
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={"Jugador | Tipo | Confianza%\nAaron Judge | HR | 28"}
          rows={3}
          className="w-full bg-input ring-1 ring-white/10 focus-ring-accent rounded-lg px-3 py-2 text-xs font-mono text-brand placeholder:text-white/20"
        />
      ) : parsedManual.length > 0 ? (
        <div className="space-y-1">
          {parsedManual.map((p, i) => (
            <div key={i} className="flex items-center justify-between">
              <span className="text-sm font-bold text-brand truncate">{p.player} <span className="text-white/40 font-normal">· {p.type}</span></span>
              {p.confidence !== null && !Number.isNaN(p.confidence) && <span className="fs-10 font-mono text-amber-300">{p.confidence}%</span>}
            </div>
          ))}
        </div>
      ) : (
        <p className="fs-10 text-white/30 leading-relaxed">Sin datos para este cruce. Pide a Claude props del partido y pégalas aquí.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MATCHUP CARD COMPACT — tarjeta de grilla con logos, más aire, momios en
// su propia línea con número grande para que se lean de un vistazo.
// ---------------------------------------------------------------------------
function MatchupCardCompact({ matchup, odds, onOpen, onRemove, teams, setMatchup }) {
  const ready = matchup.home && matchup.away;

  if (!ready) {
    return (
      <div className="rounded-2xl bg-card ring-1 ring-white-06 p-4">
        <div className="flex gap-2 items-end">
          <TeamSelect label="Visitante" value={matchup.away} exclude={matchup.home?.abbr} onChange={(t) => setMatchup({ ...matchup, away: t })} teams={teams} />
          <span className="text-white/20 text-sm pb-2.5">@</span>
          <TeamSelect label="Local" value={matchup.home} exclude={matchup.away?.abbr} onChange={(t) => setMatchup({ ...matchup, home: t })} teams={teams} />
          <button onClick={onRemove} className="text-white/25 text-sm pb-2.5 px-1">✕</button>
        </div>
      </div>
    );
  }

  const model = buildModel(matchup);
  const bothMlFilled = !!(odds.mlHome && odds.mlAway);
  const mlHomeEdge = bothMlFilled ? edgePct(model.homeWinProb, odds.mlHome) : null;
  const mlAwayEdge = bothMlFilled ? edgePct(model.awayWinProb, odds.mlAway) : null;
  const bestEdge = [mlHomeEdge, mlAwayEdge].filter(e => e !== null && !Number.isNaN(e));
  const topEdge = bestEdge.length ? Math.max(...bestEdge) : null;
  const tier = edgeTier(topEdge);
  const accentColor = tier?.label === "BET" ? "#00FFB2" : tier?.label === "LEAN" ? "#FFB200" : null;

  return (
    <button
      onClick={onOpen}
      className="rounded-2xl bg-card text-left p-5 transition-all hover:ring-white/20 relative group overflow-hidden"
      style={{ boxShadow: accentColor ? `0 0 0 1px ${accentColor}45 inset` : "0 0 0 1px rgba(255,255,255,0.06) inset" }}
    >
      {accentColor && <div className="absolute top-0 left-0 w-[3px] h-full" style={{ background: `linear-gradient(180deg, ${accentColor}, #00D9FF)` }} />}
      <button
        onClick={(e) => { e.stopPropagation(); onRemove(); }}
        className="absolute top-3 right-3 text-white/20 hover:text-white/50 text-sm opacity-0 group-hover:opacity-100 transition-opacity z-10"
      >
        ✕
      </button>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <TeamLogo teamId={matchup.away.id} size={22} />
          <span className="font-display text-xl font-bold tracking-wide leading-none">{matchup.away.abbr} <span className="text-white/30 fs-11">@</span> {matchup.home.abbr}</span>
          <TeamLogo teamId={matchup.home.id} size={22} />
        </div>
        {tier && <TierChip edge={topEdge} />}
      </div>
      <p className="fs-10 font-mono text-white/30 mb-4">{matchup.timeLabel ?? ""} · total {model.projectedTotal.toFixed(1)}</p>
      <div>
        <div className="flex items-center justify-between py-2 border-t border-white/[0.05]">
          <span className="text-sm font-bold text-brand">{matchup.away.abbr}</span>
          <span className="font-mono text-lg font-bold" style={{ color: odds.mlAway ? "#9CA3AF" : "rgba(255,255,255,0.2)" }}>{odds.mlAway ? Number(odds.mlAway).toFixed(2) : "—"}</span>
        </div>
        <div className="flex items-center justify-between py-2 border-t border-white/[0.05]">
          <span className="text-sm font-bold text-brand">{matchup.home.abbr}</span>
          <span className="font-mono text-lg font-bold" style={{ color: odds.mlHome ? "#9CA3AF" : "rgba(255,255,255,0.2)" }}>{odds.mlHome ? Number(odds.mlHome).toFixed(2) : "—"}</span>
        </div>
      </div>
      <p className="fs-9 text-white/25 mt-4 text-center">Ver RL · Total · F5 · props</p>
    </button>
  );
}

// ---------------------------------------------------------------------------
// MATCHUP DETAIL PANEL
// ---------------------------------------------------------------------------
function MatchupDetailPanel({ matchup, setMatchup, odds, setOdds, onClose, bankroll, onAddToLog }) {
  const [f5Open, setF5Open] = useState(false);
  const model = buildModel(matchup);
  const autoProps = suggestPropsFromPipeline(matchup);
  const matchupLabel = `${matchup.away.abbr} @ ${matchup.home.abbr}`;
  const logContext = {
    matchup: matchupLabel,
    gamePk: matchup.gamePk ?? null,
    homeTeamId: matchup.home.id ?? null,
    awayTeamId: matchup.away.id ?? null,
    homeAbbr: matchup.home.abbr,
    awayAbbr: matchup.away.abbr,
  };

  const mlHomeEdge = edgePct(model.homeWinProb, odds.mlHome);
  const mlAwayEdge = edgePct(model.awayWinProb, odds.mlAway);
  const totalDiff = odds.totalLine !== "" && odds.totalLine !== undefined ? model.projectedTotal - Number(odds.totalLine) : null;
  const overProb = totalDiff !== null ? 0.5 + Math.min(Math.max(totalDiff, 0) * 0.09, 0.30) : null;
  const underProb = totalDiff !== null ? 0.5 + Math.min(Math.max(-totalDiff, 0) * 0.09, 0.30) : null;
  const overEdge = overProb !== null ? edgePct(overProb, odds.over) : null;
  const underEdge = underProb !== null ? edgePct(underProb, odds.under) : null;

  const f5TotalDiff = odds.f5TotalLine !== "" && odds.f5TotalLine !== undefined ? model.f5.projectedTotal - Number(odds.f5TotalLine) : null;
  const f5OverProb = f5TotalDiff !== null ? 0.5 + Math.min(Math.max(f5TotalDiff, 0) * 0.12, 0.30) : null;
  const f5UnderProb = f5TotalDiff !== null ? 0.5 + Math.min(Math.max(-f5TotalDiff, 0) * 0.12, 0.30) : null;
  const f5OverEdge = f5OverProb !== null ? edgePct(f5OverProb, odds.f5Over) : null;
  const f5UnderEdge = f5UnderProb !== null ? edgePct(f5UnderProb, odds.f5Under) : null;
  const f5MlHomeEdge = edgePct(model.f5.homeWinProb, odds.f5MlHome);
  const f5MlAwayEdge = edgePct(model.f5.awayWinProb, odds.f5MlAway);

  return (
    <div className="fixed inset-0 z-30 flex justify-end" style={{ background: "rgba(0,0,0,0.6)" }} onClick={onClose}>
      <div
        className="bg-app h-full overflow-y-auto"
        style={{ width: "min(520px, 100%)", borderLeft: "1px solid rgba(255,255,255,0.08)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-header z-10 px-6 py-5 flex items-center justify-between" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <div className="flex items-center gap-3">
            <TeamLogo teamId={matchup.away.id} size={26} />
            <span className="font-display text-2xl font-bold tracking-wide">{matchup.away.abbr} <span className="text-white/30 fs-11">@</span> {matchup.home.abbr}</span>
            <TeamLogo teamId={matchup.home.id} size={26} />
          </div>
          <button onClick={onClose} className="text-white/40 hover:text-white text-2xl leading-none px-2">✕</button>
        </div>

        <div className="p-6 space-y-6">
          <div className="grid grid-cols-2 gap-3">
            {[
              { abbr: matchup.away.abbr, sp: matchup.awayStarter },
              { abbr: matchup.home.abbr, sp: matchup.homeStarter },
            ].map((x, i) => (
              <div key={i} className="rounded-xl bg-white-02 ring-1 ring-white/5 px-4 py-3">
                <p className="fs-9 uppercase tracking-wider text-white/35 font-semibold mb-1">Abridor {x.abbr}</p>
                {x.sp?.name ? (
                  <>
                    <p className="text-sm font-bold text-brand truncate">{x.sp.name}</p>
                    {x.sp.era != null ? (
                      <p className="fs-9 font-mono text-white/45 mt-1">ERA {x.sp.era} · WHIP {x.sp.whip ?? "—"} · K9 {x.sp.k9 ?? "—"}</p>
                    ) : (
                      <p className="fs-9 text-white/30 mt-1">Sin stats de temporada aún</p>
                    )}
                  </>
                ) : (
                  <p className="fs-9 text-white/30">Por confirmar</p>
                )}
              </div>
            ))}
          </div>

          {matchup.weather && (
            <div className="rounded-xl bg-white-02 ring-1 ring-white/5 px-4 py-3 flex items-center justify-between">
              <div>
                <p className="fs-9 uppercase tracking-wider text-white/35 font-semibold mb-1">Clima en venue</p>
                <p className="fs-10 font-mono text-white/50">
                  {matchup.weather.tempF != null ? `${matchup.weather.tempF}°F` : "—"} · viento {matchup.weather.windMph ?? "—"} mph
                  {matchup.weather.precipProb != null ? ` · ${matchup.weather.precipProb}% lluvia` : ""}
                </p>
              </div>
              <span className="fs-10 font-mono font-bold" style={{ color: model.weatherFactor > 1.01 ? "#00FFB2" : model.weatherFactor < 0.99 ? "#FF3D71" : "rgba(255,255,255,0.3)" }}>
                {model.weatherFactor > 1 ? "+" : ""}{((model.weatherFactor - 1) * 100).toFixed(1)}% al total
              </span>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-1">
              <p className="fs-10 uppercase tracking-wider text-white/35 font-semibold">Moneyline</p>
              <button onClick={() => setMatchup({ ...matchup, mlCompareOpen: !matchup.mlCompareOpen })} className="fs-9 text-white/30 font-mono hover:text-white/50">
                {matchup.mlCompareOpen ? "ocultar casas" : "+ comparar casas"}
              </button>
            </div>
            <div>
              <BetRow label={matchup.away.abbr} prob={model.awayWinProb} oddsValue={odds.mlAway} edge={mlAwayEdge} onOddsChange={(v) => setOdds({ ...odds, mlAway: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: "Moneyline", betType: "ML", side: "away" }} bothSidesFilled={!!(odds.mlAway && odds.mlHome)} />
              <BetRow label={matchup.home.abbr} prob={model.homeWinProb} oddsValue={odds.mlHome} edge={mlHomeEdge} onOddsChange={(v) => setOdds({ ...odds, mlHome: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: "Moneyline", betType: "ML", side: "home" }} bothSidesFilled={!!(odds.mlAway && odds.mlHome)} />
            </div>
            {matchup.mlCompareOpen && (
              <div className="mt-2">
                <MarketCompare
                  labelA={matchup.away.abbr}
                  labelB={matchup.home.abbr}
                  oddsA={[odds.mlAwayBook1, odds.mlAwayBook2, odds.mlAwayBook3]}
                  oddsB={[odds.mlHomeBook1, odds.mlHomeBook2, odds.mlHomeBook3]}
                  onChangeA={(i, v) => setOdds({ ...odds, [`mlAwayBook${i + 1}`]: v })}
                  onChangeB={(i, v) => setOdds({ ...odds, [`mlHomeBook${i + 1}`]: v })}
                />
              </div>
            )}
          </div>

          <RunLineSection model={model} oddsKeyFav="rlFav" oddsKeyDog="rlDog" odds={odds} setOdds={setOdds} homeAbbr={matchup.home.abbr} awayAbbr={matchup.away.abbr} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, betType: "RL" }} line={1.5} />

          <div>
            <p className="fs-10 uppercase tracking-wider text-white/35 font-semibold mb-1">Total — modelo {model.projectedTotal.toFixed(1)}</p>
            <div className="flex items-center gap-2 mb-2">
              <span className="fs-10 text-white/40">Línea</span>
              <input type="text" inputMode="decimal" value={odds.totalLine ?? ""} onChange={(e) => setOdds({ ...odds, totalLine: e.target.value })} placeholder="8.5" className="w-16 bg-input ring-1 ring-white/10 focus-ring-accent rounded-lg px-2 py-1 text-center font-mono text-sm text-brand placeholder:text-white/20" />
            </div>
            <div>
              <BetRow label="Over" prob={overProb ?? 0.5} oddsValue={odds.over} edge={overEdge} onOddsChange={(v) => setOdds({ ...odds, over: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: `Total ${odds.totalLine || ""}`, betType: "Total", side: "over", line: odds.totalLine ? Number(odds.totalLine) : null }} bothSidesFilled={!!(odds.over && odds.under)} />
              <BetRow label="Under" prob={underProb ?? 0.5} oddsValue={odds.under} edge={underEdge} onOddsChange={(v) => setOdds({ ...odds, under: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: `Total ${odds.totalLine || ""}`, betType: "Total", side: "under", line: odds.totalLine ? Number(odds.totalLine) : null }} bothSidesFilled={!!(odds.over && odds.under)} />
            </div>
          </div>

          <div className="rounded-xl bg-white/[0.015] ring-1 ring-white/5">
            <button onClick={() => setF5Open(!f5Open)} className="w-full flex items-center justify-between px-4 py-3">
              <span className="font-display fs-11 font-bold tracking-wide text-white/60">F5 · PRIMERAS 5 ENTRADAS</span>
              {f5Open ? <ChevronUp size={14} className="text-white/40" /> : <ChevronDown size={14} className="text-white/40" />}
            </button>
            {f5Open && (
              <div className="px-4 pb-4 space-y-4">
                <div>
                  <p className="fs-10 uppercase tracking-wider text-white/35 font-semibold mb-1">Moneyline F5</p>
                  <div>
                    <BetRow label={matchup.away.abbr} prob={model.f5.awayWinProb} oddsValue={odds.f5MlAway} edge={f5MlAwayEdge} onOddsChange={(v) => setOdds({ ...odds, f5MlAway: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: "ML F5", betType: "ML", side: "away", isF5: true }} bothSidesFilled={!!(odds.f5MlAway && odds.f5MlHome)} />
                    <BetRow label={matchup.home.abbr} prob={model.f5.homeWinProb} oddsValue={odds.f5MlHome} edge={f5MlHomeEdge} onOddsChange={(v) => setOdds({ ...odds, f5MlHome: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: "ML F5", betType: "ML", side: "home", isF5: true }} bothSidesFilled={!!(odds.f5MlAway && odds.f5MlHome)} />
                  </div>
                </div>
                <RunLineSection model={model.f5} oddsKeyFav="f5RlFav" oddsKeyDog="f5RlDog" invertedKey="f5RlInverted" odds={odds} setOdds={setOdds} homeAbbr={matchup.home.abbr} awayAbbr={matchup.away.abbr} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: "RL F5", betType: "RL", isF5: true }} line={0.5} />
                <div>
                  <p className="fs-10 uppercase tracking-wider text-white/35 font-semibold mb-1">Total F5 — modelo {model.f5.projectedTotal.toFixed(1)}</p>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="fs-10 text-white/40">Línea</span>
                    <input type="text" inputMode="decimal" value={odds.f5TotalLine ?? ""} onChange={(e) => setOdds({ ...odds, f5TotalLine: e.target.value })} placeholder="4.5" className="w-16 bg-input ring-1 ring-white/10 focus-ring-accent rounded-lg px-2 py-1 text-center font-mono text-sm text-brand placeholder:text-white/20" />
                  </div>
                  <div>
                    <BetRow label="Over" prob={f5OverProb ?? 0.5} oddsValue={odds.f5Over} edge={f5OverEdge} onOddsChange={(v) => setOdds({ ...odds, f5Over: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: `Total F5 ${odds.f5TotalLine || ""}`, betType: "Total", side: "over", line: odds.f5TotalLine ? Number(odds.f5TotalLine) : null, isF5: true }} bothSidesFilled={!!(odds.f5Over && odds.f5Under)} />
                    <BetRow label="Under" prob={f5UnderProb ?? 0.5} oddsValue={odds.f5Under} edge={f5UnderEdge} onOddsChange={(v) => setOdds({ ...odds, f5Under: v })} bankroll={bankroll} onLog={onAddToLog} logContext={{ ...logContext, market: `Total F5 ${odds.f5TotalLine || ""}`, betType: "Total", side: "under", line: odds.f5TotalLine ? Number(odds.f5TotalLine) : null, isF5: true }} bothSidesFilled={!!(odds.f5Over && odds.f5Under)} />
                  </div>
                </div>
              </div>
            )}
          </div>

          <PropsPanel value={matchup.propsText} onChange={(v) => setMatchup({ ...matchup, propsText: v })} autoProps={autoProps} onLog={onAddToLog} logContext={logContext} bankroll={bankroll} />

          <p className="fs-9 text-white/25 leading-relaxed pt-2 border-t border-white/[0.06]">
            Modelo: Elo + abridor + bullpen + splits + forma + clima. RL asigna -1.5/+1.5 según favorito del modelo — usa ⇄ si el mercado real difiere. BET ≥6%, LEAN ≥2.5%, FADE &lt;-2.5%. F5 se cotejan a mano.
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PICK OF DAY — "Apuesta Máxima", vive en el sidebar
// ---------------------------------------------------------------------------
function PickOfDay({ matchups, oddsMap, bankroll }) {
  const best = useMemo(() => {
    let top = null;
    for (const m of matchups) {
      if (!m.home || !m.away) continue;
      const model = buildModel(m);
      const odds = oddsMap[m.id] || {};
      const modelFavIsHome = model.homeIsFavorite;
      const inverted = !!odds.rlInverted;
      const favIsHome = inverted ? !modelFavIsHome : modelFavIsHome;
      const favAbbr = favIsHome ? m.home.abbr : m.away.abbr;
      const dogAbbr = favIsHome ? m.away.abbr : m.home.abbr;
      const matchupLabel = `${m.away.abbr} @ ${m.home.abbr}`;

      const f5ModelFavIsHome = model.f5.homeIsFavorite;
      const f5Inverted = !!odds.f5RlInverted;
      const f5FavIsHome = f5Inverted ? !f5ModelFavIsHome : f5ModelFavIsHome;
      const f5FavAbbr = f5FavIsHome ? m.home.abbr : m.away.abbr;
      const f5DogAbbr = f5FavIsHome ? m.away.abbr : m.home.abbr;

      const totalDiff = odds.totalLine !== "" && odds.totalLine !== undefined ? model.projectedTotal - Number(odds.totalLine) : null;
      const overProb = totalDiff !== null ? 0.5 + Math.min(Math.max(totalDiff, 0) * 0.09, 0.30) : null;
      const underProb = totalDiff !== null ? 0.5 + Math.min(Math.max(-totalDiff, 0) * 0.09, 0.30) : null;

      const candidates = [
        { label: `ML ${m.home.abbr}`, prob: model.homeWinProb, odd: odds.mlHome, matchup: matchupLabel, otherOdd: odds.mlAway },
        { label: `ML ${m.away.abbr}`, prob: model.awayWinProb, odd: odds.mlAway, matchup: matchupLabel, otherOdd: odds.mlHome },
        { label: `${favAbbr} -1.5`, prob: model.favMinus1_5, odd: odds.rlFav, matchup: matchupLabel, otherOdd: odds.rlDog },
        { label: `${dogAbbr} +1.5`, prob: model.dogPlus1_5, odd: odds.rlDog, matchup: matchupLabel, otherOdd: odds.rlFav },
        { label: "Over " + (odds.totalLine || ""), prob: overProb, odd: odds.over, matchup: matchupLabel, otherOdd: odds.under },
        { label: "Under " + (odds.totalLine || ""), prob: underProb, odd: odds.under, matchup: matchupLabel, otherOdd: odds.over },
        { label: `ML F5 ${m.home.abbr}`, prob: model.f5.homeWinProb, odd: odds.f5MlHome, matchup: matchupLabel, otherOdd: odds.f5MlAway },
        { label: `ML F5 ${m.away.abbr}`, prob: model.f5.awayWinProb, odd: odds.f5MlAway, matchup: matchupLabel, otherOdd: odds.f5MlHome },
        { label: `${f5FavAbbr} -0.5 F5`, prob: model.f5.favMinus0_5, odd: odds.f5RlFav, matchup: matchupLabel, otherOdd: odds.f5RlDog },
        { label: `${f5DogAbbr} +0.5 F5`, prob: model.f5.dogPlus0_5, odd: odds.f5RlDog, matchup: matchupLabel, otherOdd: odds.f5RlFav },
      ];
      for (const c of candidates) {
        if (!c.odd || !c.otherOdd || c.prob === null || c.prob === undefined) continue;
        const e = edgePct(c.prob, c.odd);
        if (e === null || Number.isNaN(e)) continue;
        if (!top || e > top.edge) top = { ...c, edge: e };
      }
    }
    return top;
  }, [matchups, oddsMap]);

  const isMaxBet = best && edgeTier(best.edge)?.label === "BET";

  if (!best) {
    return (
      <div className="rounded-2xl bg-white-02 ring-1 ring-white/5 px-4 py-4 flex items-start gap-2.5">
        <AlertCircle size={16} className="text-white/30 shrink-0 mt-0.5" />
        <p className="text-sm text-white/40 leading-relaxed">Ingresa momios en algún cruce para ver la apuesta máxima del día aquí.</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl relative overflow-hidden ring-1 ring-accent-glow px-5 py-5 bg-accent-grad">
      <div className="flex items-center gap-2 mb-3">
        <Trophy size={15} className="text-accent" />
        <span className="font-display fs-10 uppercase tracking-widest font-bold text-accent">Apuesta máxima</span>
      </div>
      <p className="font-display text-2xl font-bold text-brand leading-tight tracking-wide">{best.label}</p>
      <p className="text-sm text-white/40 mt-1">{best.matchup} · momio {Number(best.odd).toFixed(2)}</p>
      <div className="flex items-center gap-2.5 mt-3">
        <span className="text-3xl font-mono font-bold text-accent">+{best.edge.toFixed(1)}%</span>
        <TierChip edge={best.edge} size="lg" />
      </div>
      {(() => {
        const k = kellyFraction(best.prob, best.odd);
        const stake = k && k > 0 && bankroll ? k * Number(bankroll) : null;
        return stake ? (
          <p className="text-sm text-white/35 mt-3">¼ Kelly: <span className="text-amber-200 font-mono">${stake.toFixed(0)}</span> sobre ${Number(bankroll).toLocaleString()}</p>
        ) : null;
      })()}
      {!isMaxBet && (
        <p className="fs-9 text-white/30 mt-3 leading-relaxed italic">Mejor edge disponible hoy, pero no alcanza el umbral BET (≥6%) — trátalo como referencia.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BET LOG TAB — pestaña completa con buscador y filtros (antes vivía
// comprimida en el sidebar; ahora tiene su propio espacio para navegar
// fácil entre muchas apuestas).
// ---------------------------------------------------------------------------
function ResultButton({ active, color, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className="fs-9 font-bold uppercase tracking-wide px-2.5 py-1.5 rounded-md transition-all"
      style={{ color: active ? "#06070A" : color, background: active ? color : `${color}1A`, boxShadow: active ? "none" : `0 0 0 1px ${color}4D inset` }}
    >
      {children}
    </button>
  );
}

function BetLogTab({ entries, setEntries }) {
  const [search, setSearch] = useState("");
  const [resultFilter, setResultFilter] = useState("all");
  const [marketFilter, setMarketFilter] = useState("all");
  const summary = useMemo(() => summarizeLog(entries), [entries]);

  const updateEntry = (id, patch) => setEntries((prev) => prev.map(e => e.id === id ? { ...e, ...patch } : e));
  const removeEntry = (id) => setEntries((prev) => prev.filter(e => e.id !== id));

  const exportLog = () => {
    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `mlb-edge-bitacora-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const importLog = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const imported = JSON.parse(ev.target.result);
        if (Array.isArray(imported)) setEntries(imported);
      } catch { alert("Archivo inválido."); }
    };
    reader.readAsText(file);
    e.target.value = "";
  };

  const marketTypes = useMemo(() => {
    const types = new Set(entries.map(e => e.betType).filter(Boolean));
    return Array.from(types);
  }, [entries]);

  const filtered = useMemo(() => {
    return [...entries].reverse().filter(e => {
      if (resultFilter !== "all" && (e.result || "pending") !== resultFilter) return false;
      if (marketFilter !== "all" && e.betType !== marketFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const haystack = `${e.label} ${e.matchup} ${e.market}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [entries, search, resultFilter, marketFilter]);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-4 gap-3">
        <div className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 text-center">
          <p className="fs-9 text-white/35 uppercase tracking-wide">Apostado</p>
          <p className="text-lg font-mono font-bold text-brand mt-1">${summary.totalStaked.toFixed(0)}</p>
        </div>
        <div className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 text-center">
          <p className="fs-9 text-white/35 uppercase tracking-wide">Balance</p>
          <p className="text-lg font-mono font-bold mt-1" style={{ color: summary.totalProfit > 0 ? "#00FFB2" : summary.totalProfit < 0 ? "#FF3D71" : "#E4E7EC" }}>{summary.totalProfit > 0 ? "+" : ""}${summary.totalProfit.toFixed(0)}</p>
        </div>
        <div className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 text-center">
          <p className="fs-9 text-white/35 uppercase tracking-wide">ROI</p>
          <p className="text-lg font-mono font-bold mt-1" style={{ color: summary.roi > 0 ? "#00FFB2" : summary.roi < 0 ? "#FF3D71" : "#E4E7EC" }}>{summary.roi !== null ? `${summary.roi > 0 ? "+" : ""}${summary.roi.toFixed(1)}%` : "—"}</p>
        </div>
        <div className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 text-center">
          <p className="fs-9 text-white/35 uppercase tracking-wide">Win rate</p>
          <p className="text-lg font-mono font-bold text-brand mt-1">{summary.winRate !== null ? `${summary.winRate.toFixed(0)}%` : "—"}</p>
        </div>
      </div>

      <div className="flex items-center gap-2.5 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar equipo o mercado..."
            className="w-full bg-card ring-1 ring-white-06 focus-ring-accent rounded-lg pl-9 pr-3 py-2.5 text-sm text-brand placeholder:text-white/25"
          />
        </div>
        <select value={resultFilter} onChange={(e) => setResultFilter(e.target.value)} className="bg-card ring-1 ring-white-06 rounded-lg px-3 py-2.5 text-sm text-brand">
          <option value="all">Resultado: todos</option>
          <option value="pending">Pendiente</option>
          <option value="won">Ganada</option>
          <option value="lost">Perdida</option>
          <option value="push">Push</option>
        </select>
        <select value={marketFilter} onChange={(e) => setMarketFilter(e.target.value)} className="bg-card ring-1 ring-white-06 rounded-lg px-3 py-2.5 text-sm text-brand">
          <option value="all">Mercado: todos</option>
          {marketTypes.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-xl bg-card ring-1 ring-white-06 px-5 py-8 text-center">
          <p className="text-sm text-white/35">{entries.length === 0 ? "Sin apuestas registradas todavía. Usa el botón + junto a cualquier momio." : "Ninguna apuesta coincide con esos filtros."}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((entry) => (
            <div key={entry.id} className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-bold text-brand truncate flex items-center gap-1.5">{entry.label} {entry.autoGraded && <CheckCircle2 size={12} className="text-accent" />}</p>
                  <p className="fs-10 text-white/40 truncate mt-0.5">{entry.matchup} · {entry.market} · {Number(entry.odds).toFixed(2)}</p>
                  <p className="fs-9 text-white/30 mt-0.5">${Number(entry.stake).toFixed(0)} · {entry.date}</p>
                </div>
                <button onClick={() => removeEntry(entry.id)} className="text-white/25 fs-11 shrink-0">✕</button>
              </div>
              <div className="flex items-center gap-1.5 mt-2.5">
                <ResultButton active={!entry.result || entry.result === "pending"} color="#9CA3AF" onClick={() => updateEntry(entry.id, { result: "pending" })}>Pend</ResultButton>
                <ResultButton active={entry.result === "won"} color="#00FFB2" onClick={() => updateEntry(entry.id, { result: "won" })}>Ganada</ResultButton>
                <ResultButton active={entry.result === "lost"} color="#FF3D71" onClick={() => updateEntry(entry.id, { result: "lost" })}>Perdida</ResultButton>
                <ResultButton active={entry.result === "push"} color="#FFB200" onClick={() => updateEntry(entry.id, { result: "push" })}>Push</ResultButton>
                {(entry.result === "won" || entry.result === "lost") && (
                  <span className="fs-10 font-mono font-bold ml-auto" style={{ color: profitForEntry(entry) >= 0 ? "#00FFB2" : "#FF3D71" }}>{profitForEntry(entry) >= 0 ? "+" : ""}${profitForEntry(entry).toFixed(0)}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button onClick={exportLog} className="flex-1 fs-10 font-bold uppercase text-white/50 bg-card ring-1 ring-white-06 rounded-lg py-2.5">Exportar</button>
        <label className="flex-1 fs-10 font-bold uppercase text-white/50 bg-card ring-1 ring-white-06 rounded-lg py-2.5 text-center cursor-pointer">
          Importar
          <input type="file" accept="application/json" onChange={importLog} className="hidden" />
        </label>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// REPORTS TAB
// ---------------------------------------------------------------------------
function ReportsTab({ entries }) {
  const [period, setPeriod] = useState("day");
  const report = useMemo(() => buildReport(entries, period), [entries, period]);

  const formatKey = (key) => {
    if (period === "day") {
      const d = new Date(key + "T12:00:00");
      return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
    }
    if (period === "week") {
      const d = new Date(key + "T12:00:00");
      return `Semana del ${d.toLocaleDateString([], { day: "numeric", month: "short" })}`;
    }
    return new Date(`${key}-01T12:00:00`).toLocaleDateString([], { month: "long", year: "numeric" });
  };

  const settledWithoutDate = entries.filter(e => (e.result === "won" || e.result === "lost" || e.result === "push") && !e.dateISO).length;

  return (
    <div className="space-y-5">
      <div className="flex gap-2">
        {[{ k: "day", l: "Diario" }, { k: "week", l: "Semanal" }, { k: "month", l: "Mensual" }].map(p => (
          <button
            key={p.k}
            onClick={() => setPeriod(p.k)}
            className="flex-1 fs-10 font-bold uppercase tracking-wide py-2.5 rounded-lg transition-all"
            style={period === p.k
              ? { color: "#00FFB2", background: "rgba(0,255,178,0.1)", boxShadow: "0 0 0 1px rgba(0,255,178,0.3) inset" }
              : { color: "rgba(255,255,255,0.4)", background: "rgba(255,255,255,0.03)", boxShadow: "0 0 0 1px rgba(255,255,255,0.08) inset" }}
          >
            {p.l}
          </button>
        ))}
      </div>

      {report.length === 0 ? (
        <div className="rounded-xl bg-card ring-1 ring-white-06 px-5 py-8 text-center">
          {settledWithoutDate > 0 ? (
            <p className="text-sm text-white/35 leading-relaxed">Tienes {settledWithoutDate} apuesta(s) liquidada(s) registradas antes de que existiera el cotejo por fecha — cuentan en el Balance/ROI de la Bitácora, pero no aparecen aquí. Las que registres de ahora en adelante sí aparecen.</p>
          ) : (
            <p className="text-sm text-white/35">Sin apuestas liquidadas todavía para generar un reporte.</p>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {report.map((r) => (
            <div key={r.key} className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3.5">
              <div className="flex items-center justify-between">
                <p className="text-sm font-bold text-brand">{formatKey(r.key)}</p>
                <span className="font-mono text-base font-bold" style={{ color: r.profit > 0 ? "#00FFB2" : r.profit < 0 ? "#FF3D71" : "rgba(255,255,255,0.3)" }}>
                  {r.profit > 0 ? "+" : ""}${r.profit.toFixed(0)}
                </span>
              </div>
              <p className="fs-10 text-white/35 mt-1">
                {r.count} apuesta(s) · {r.wins}G-{r.losses}P · ROI {r.roi !== null ? `${r.roi > 0 ? "+" : ""}${r.roi.toFixed(1)}%` : "—"}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MODEL HEALTH TAB — métricas reales de desempeño, sin autoajuste. El
// usuario decide qué hacer con la información; la app solo la presenta clara.
// ---------------------------------------------------------------------------
function SampleBadge({ sufficient }) {
  return sufficient ? null : (
    <span className="fs-9 font-bold uppercase text-amber-300 bg-amber-400/10 px-2 py-0.5 rounded-md ml-2">muestra baja</span>
  );
}

function ModelHealthTab({ entries }) {
  const health = useMemo(() => analyzeModelHealth(entries), [entries]);

  if (health.totalSettled === 0) {
    return (
      <div className="rounded-xl bg-card ring-1 ring-white-06 px-5 py-10 text-center">
        <Activity size={28} className="text-white/20 mx-auto mb-3" />
        <p className="text-sm text-white/40 leading-relaxed max-w-sm mx-auto">Sin apuestas Ganadas/Perdidas todavía. En cuanto cotejes resultados (automático o a mano), aquí vas a ver qué tan bien le está pegando el modelo.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {!health.overallSufficient && (
        <div className="rounded-xl bg-amber-400/10 ring-1 ring-amber-400/30 px-4 py-3 flex items-start gap-2.5">
          <AlertCircle size={15} className="text-amber-300 mt-0.5 shrink-0" />
          <p className="text-sm text-white/60 leading-relaxed">Solo {health.totalSettled} apuesta(s) liquidada(s). Por debajo de {MIN_SAMPLE_SIZE} muestras, cualquier patrón aquí puede ser suerte, no señal real — trata estos números como referencia, no como conclusión.</p>
        </div>
      )}

      <div>
        <h3 className="font-display text-sm font-bold tracking-wide text-white/60 uppercase mb-3">Win rate por mercado</h3>
        <div className="space-y-2">
          {health.byMarket.map((m) => (
            <div key={m.key} className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 flex items-center justify-between">
              <div className="flex items-center">
                <span className="text-sm font-bold text-brand">{m.key}</span>
                <SampleBadge sufficient={m.sufficient} />
              </div>
              <div className="text-right">
                <span className="font-mono text-base font-bold" style={{ color: m.winRate >= 50 ? "#00FFB2" : "#FF3D71" }}>{m.winRate.toFixed(0)}%</span>
                <span className="fs-9 text-white/30 ml-2">{m.wins}G-{m.losses}P</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="font-display text-sm font-bold tracking-wide text-white/60 uppercase mb-3">Win rate por tier de edge</h3>
        <div className="space-y-2">
          {health.byTier.map((t) => (
            <div key={t.key} className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 flex items-center justify-between">
              <div className="flex items-center">
                <TierChip edge={t.key === "BET" ? 10 : t.key === "LEAN" ? 4 : t.key === "FADE" ? -10 : 0} />
                <SampleBadge sufficient={t.sufficient} />
              </div>
              <div className="text-right">
                <span className="font-mono text-base font-bold" style={{ color: t.winRate >= 50 ? "#00FFB2" : "#FF3D71" }}>{t.winRate.toFixed(0)}%</span>
                <span className="fs-9 text-white/30 ml-2">{t.wins}G-{t.losses}P</span>
              </div>
            </div>
          ))}
        </div>
        <p className="fs-9 text-white/30 mt-2 leading-relaxed">Si BET no le pega claramente mejor que LEAN o PASS, el umbral de tier podría necesitar ajuste — pero espera a tener muestra suficiente antes de decidir.</p>
      </div>

      <div>
        <h3 className="font-display text-sm font-bold tracking-wide text-white/60 uppercase mb-3">Calibración del modelo</h3>
        <div className="space-y-2">
          {health.calibration.map((c) => {
            const bandCenter = c.label === "50-60%" ? 55 : c.label === "60-70%" ? 65 : 75;
            const diff = c.winRate !== null ? c.winRate - bandCenter : null;
            return (
              <div key={c.label} className="rounded-xl bg-card ring-1 ring-white-06 px-4 py-3 flex items-center justify-between">
                <div className="flex items-center">
                  <span className="text-sm font-bold text-brand">Modelo dice {c.label}</span>
                  <SampleBadge sufficient={c.sufficient} />
                </div>
                <div className="text-right">
                  <span className="font-mono text-base font-bold text-brand">{c.winRate?.toFixed(0)}% real</span>
                  {diff !== null && (
                    <span className="fs-9 ml-2" style={{ color: Math.abs(diff) <= 8 ? "#00FFB2" : "#FF3D71" }}>
                      {diff > 0 ? "+" : ""}{diff.toFixed(0)}pp vs banda
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <p className="fs-9 text-white/30 mt-2 leading-relaxed">Si el modelo dice "65%" y en la realidad gana mucho menos (ej. 45%), está sobreconfiado en esa banda. Si gana mucho más, está siendo tímido.</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MAIN APP
// ---------------------------------------------------------------------------
let nextId = 1;
function emptyMatchup() {
  return { id: nextId++, home: null, away: null, propsText: "" };
}

export default function MLBEdge() {
  const [activeTab, setActiveTab] = useState("games");
  const [matchups, setMatchups] = useState([emptyMatchup()]);
  const [openId, setOpenId] = useState(null);
  const [oddsMap, setOddsMap] = useState({});
  const [bankroll, setBankroll] = useState("1000");
  const [betLog, setBetLog] = useState(() => loadBetLog());

  useEffect(() => { saveBetLog(betLog); }, [betLog]);

  const handleAddToLog = useCallback((bet) => {
    const now = new Date();
    const entry = {
      id: `${Date.now()}-${Math.round(Math.random() * 1000)}`,
      date: now.toLocaleDateString(),
      dateISO: now.toISOString().slice(0, 10),
      result: "pending",
      ...bet,
    };
    setBetLog((prev) => [...prev, entry]);
  }, []);

  const [pipelineStatus, setPipelineStatus] = useState(DATA_JSON_URL ? "loading" : "no-url");
  const [pipelineErrorMsg, setPipelineErrorMsg] = useState("");
  const [pipelineMeta, setPipelineMeta] = useState(null);
  const [teams, setTeams] = useState(TEAMS_FALLBACK);
  const [autoGames, setAutoGames] = useState([]);
  const [availableDates, setAvailableDates] = useState([]);
  const [selectedDate, setSelectedDate] = useState(null);
  const [calendarLoadedId, setCalendarLoadedId] = useState(null);

  const loadPipeline = useCallback(async () => {
    if (!DATA_JSON_URL) { setPipelineStatus("no-url"); return; }
    setPipelineStatus("loading");
    try {
      const payload = await fetchPipelineData(DATA_JSON_URL);
      const normTeams = normalizeTeamsFromPipeline(payload);
      if (!normTeams.length) throw new Error("El JSON no trae equipos.");
      const teamsById = Object.fromEntries(payload.teams.map(t => [t.id, normTeams.find(nt => nt.id === t.id)]));
      const normGames = normalizeGamesFromPipeline(payload, teamsById);
      const dates = payload.availableDates ?? [payload.date];
      setTeams(normTeams);
      setAutoGames(normGames);
      setAvailableDates(dates);
      setSelectedDate((prev) => prev && dates.includes(prev) ? prev : dates[0]);
      setPipelineMeta({ generatedAt: payload.generatedAt, date: payload.date });
      setPipelineStatus("ok");

      const resultsByGamePk = Object.fromEntries((payload.results ?? []).map(r => [r.gamePk, r]));
      if (Object.keys(resultsByGamePk).length) {
        setBetLog((prev) => autoGradeLog(prev, resultsByGamePk));
      }
    } catch (e) {
      console.error(e);
      setPipelineErrorMsg(e?.message || String(e));
      setPipelineStatus("error");
      setTeams(TEAMS_FALLBACK);
    }
  }, []);

  useEffect(() => { loadPipeline(); }, [loadPipeline]);

  const loadCalendarForDate = (dateStr) => {
    const games = autoGames.filter(g => g.dateStr === dateStr);
    if (!games.length) return;
    const newMatchups = [];
    const newOddsMap = {};
    for (const g of games) {
      const m = {
        ...emptyMatchup(),
        home: g.home, away: g.away, timeLabel: g.timeLabel,
        homeStarter: g.homeStarter, awayStarter: g.awayStarter, weather: g.weather,
        gamePk: g.gamePk,
      };
      newMatchups.push(m);
      const ao = g.autoOdds;
      if (ao) {
        const model = buildModel(m);
        const homeIsFav = model.homeIsFavorite;
        newOddsMap[m.id] = {
          mlHome: ao.mlHome ?? "", mlAway: ao.mlAway ?? "",
          over: ao.totalOverPrice ?? "", under: ao.totalUnderPrice ?? "", totalLine: ao.totalPoint ?? "",
          rlFav: homeIsFav ? (ao.rlHomePrice ?? "") : (ao.rlAwayPrice ?? ""),
          rlDog: homeIsFav ? (ao.rlAwayPrice ?? "") : (ao.rlHomePrice ?? ""),
        };
      }
    }
    setMatchups(newMatchups);
    setOddsMap(newOddsMap);
    setCalendarLoadedId(dateStr);
  };

  const updateMatchup = (id, patch) => setMatchups((prev) => prev.map(m => m.id === id ? { ...m, ...patch } : m));
  const setOddsForMatchup = (id, odds) => setOddsMap((prev) => ({ ...prev, [id]: odds }));
  const addMatchup = () => setMatchups((prev) => [...prev, emptyMatchup()]);
  const removeMatchup = (id) => setMatchups((prev) => prev.filter(m => m.id !== id));
  const openMatchup = matchups.find(m => m.id === openId);

  const TABS = [
    { k: "games", l: "Partidos", icon: LayoutGrid },
    { k: "log", l: "Bitácora", icon: ListChecks },
    { k: "reports", l: "Reportes", icon: Trophy },
    { k: "health", l: "Salud del modelo", icon: Activity },
  ];

  return (
    <div className="mlb-edge-root" style={{ minHeight: "100vh", background: "#06070A", color: "#E4E7EC", fontFamily: "'Space Grotesk', sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Big+Shoulders:wght@700;800;900&family=Space+Grotesk:wght@400;500;700&family=JetBrains+Mono:wght@500;700&display=swap');
        .font-display { font-family: 'Big Shoulders', sans-serif; text-transform: uppercase; }
        select option { background: #0D0F14; }
        .mlb-edge-root, .mlb-edge-root * { box-sizing: border-box; }
        .bg-app { background: #06070A; }
        .bg-card { background: #0D0F14; }
        .bg-input { background: #06070A; }
        .bg-header { background: rgba(6,7,10,0.97); backdrop-filter: blur(10px); }
        .bg-accent-chip { background: rgba(0,255,178,0.10); }
        .bg-accent-grad { background: linear-gradient(135deg, rgba(0,255,178,0.10), rgba(0,217,255,0.03)); }
        .bg-white-02 { background: rgba(255,255,255,0.03); }
        .bg-meter-track { background: #181C24; }
        .text-brand { color: #E4E7EC; }
        .text-accent { color: #00FFB2; }
        .ring-white-06 { box-shadow: 0 0 0 1px rgba(255,255,255,0.07) inset; }
        .ring-accent-30 { box-shadow: 0 0 0 1px rgba(0,255,178,0.3) inset; }
        .ring-accent-glow { box-shadow: 0 0 0 1px rgba(0,255,178,0.3) inset, 0 0 24px rgba(0,255,178,0.08); }
        .focus-ring-accent:focus { box-shadow: 0 0 0 2px rgba(0,255,178,0.5) inset; outline: none; }
        .fs-9 { font-size: 10px; }
        .fs-10 { font-size: 11px; }
        .fs-11 { font-size: 12px; }
        .sidebar-col { width: 300px; }
        .glow-orb { position: absolute; border-radius: 50%; pointer-events: none; filter: blur(2px); }
        @media (max-width: 900px) {
          .layout-grid { grid-template-columns: 1fr !important; }
          .sidebar-col { width: 100% !important; }
        }
      `}</style>

      <div className="glow-orb" style={{ top: -100, right: -80, width: 320, height: 320, background: "radial-gradient(circle, rgba(0,255,178,0.08), transparent 70%)" }} />
      <div className="glow-orb" style={{ top: 300, left: -120, width: 280, height: 280, background: "radial-gradient(circle, rgba(255,46,151,0.05), transparent 70%)" }} />

      <div className="bg-header" style={{ position: "sticky", top: 0, zIndex: 20, borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
        <div className="px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="font-display text-3xl font-bold tracking-wide leading-none">MLB <span className="text-accent" style={{ textShadow: "0 0 20px rgba(0,255,178,0.4)" }}>EDGE</span></h1>
            <p className="fs-9 text-white/35 mt-1 tracking-wide">TEMPORADA 2026 · PIPELINE AUTÓNOMO{autoGames.length > 0 ? ` · ${autoGames.length} JUEGOS` : ""}</p>
          </div>
          <div className="flex items-center gap-2.5">
            {pipelineStatus === "ok" && (
              <span className="fs-9 font-bold uppercase text-accent bg-accent-chip ring-1 ring-accent-30 px-3 py-1.5 rounded-full flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-accent" style={{ boxShadow: "0 0 6px #00FFB2" }} /> Live
              </span>
            )}
            <button onClick={loadPipeline} className="p-2 rounded-full bg-white/[0.05] ring-1 ring-white/10 active:scale-90 transition-transform">
              <RefreshCw size={15} className={pipelineStatus === "loading" ? "animate-spin text-accent" : "text-white/50"} />
            </button>
          </div>
        </div>
        <div className="px-6 flex gap-1">
          {TABS.map(t => {
            const Icon = t.icon;
            const active = activeTab === t.k;
            return (
              <button
                key={t.k}
                onClick={() => setActiveTab(t.k)}
                className="font-display fs-10 font-bold tracking-wide px-4 py-3 rounded-t-lg transition-all flex items-center gap-1.5"
                style={active ? { color: "#06070A", background: "#00FFB2" } : { color: "rgba(255,255,255,0.4)" }}
              >
                <Icon size={13} /> {t.l}
              </button>
            );
          })}
        </div>
      </div>

      {activeTab === "games" && (
        <>
          {pipelineStatus === "no-url" && (
            <div className="px-6 pt-4">
              <div className="rounded-xl bg-white-02 ring-1 ring-white/5 px-4 py-3 flex items-start gap-2.5">
                <Info size={15} className="text-white/30 mt-0.5 shrink-0" />
                <p className="text-sm text-white/35 leading-relaxed">Pipeline no configurado — edita <code className="text-white/50">DATA_JSON_URL</code>.</p>
              </div>
            </div>
          )}
          {pipelineStatus === "error" && (
            <div className="px-6 pt-4">
              <div className="rounded-xl bg-red-500/10 ring-1 ring-red-500/30 px-4 py-3 flex items-start gap-2.5">
                <AlertCircle size={15} className="text-red-400 mt-0.5 shrink-0" />
                <p className="text-sm text-white/50 leading-relaxed">No se pudo leer el pipeline.<br/><span className="text-red-400 font-mono fs-9">Detalle: {pipelineErrorMsg}</span></p>
              </div>
            </div>
          )}
          {pipelineStatus === "ok" && availableDates.length > 0 && (
            <div className="px-6 pt-4 flex items-center gap-3 flex-wrap">
              <div className="flex gap-2">
                {availableDates.map((d, i) => {
                  const dt = new Date(d + "T12:00:00");
                  const label = i === 0 ? "Hoy" : i === 1 ? "Mañana" : dt.toLocaleDateString([], { weekday: "short", day: "numeric" });
                  const count = autoGames.filter(g => g.dateStr === d).length;
                  const active = selectedDate === d;
                  return (
                    <button
                      key={d}
                      onClick={() => setSelectedDate(d)}
                      className="font-display fs-10 font-bold px-3.5 py-2 rounded-full transition-all"
                      style={active ? { color: "#00FFB2", background: "rgba(0,255,178,0.1)", boxShadow: "0 0 0 1px rgba(0,255,178,0.3) inset" } : { color: "rgba(255,255,255,0.4)", background: "rgba(255,255,255,0.03)", boxShadow: "0 0 0 1px rgba(255,255,255,0.08) inset" }}
                    >
                      {label} · {count}
                    </button>
                  );
                })}
              </div>
              {autoGames.filter(g => g.dateStr === selectedDate).length > 0 && calendarLoadedId !== selectedDate && (
                <button onClick={() => loadCalendarForDate(selectedDate)} className="font-display fs-10 font-bold text-amber-300 bg-amber-400/10 ring-1 ring-amber-400/30 px-4 py-2 rounded-full">
                  Cargar estos {autoGames.filter(g => g.dateStr === selectedDate).length} juegos
                </button>
              )}
            </div>
          )}

          <div className="px-6 py-5 layout-grid" style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: "24px", alignItems: "start" }}>
            <div className="sidebar-col space-y-4" style={{ position: "sticky", top: "130px" }}>
              <div className="rounded-2xl bg-card ring-1 ring-white-06 p-4">
                <div className="flex items-center justify-between">
                  <span className="fs-10 uppercase tracking-wider text-white/40 font-semibold">Bankroll</span>
                  <div className="flex items-center gap-1">
                    <span className="text-sm font-mono text-white/40">$</span>
                    <input type="text" inputMode="numeric" value={bankroll} onChange={(e) => setBankroll(e.target.value.replace(/[^0-9]/g, ""))} className="w-20 bg-input ring-1 ring-white/10 focus-ring-accent rounded-lg px-2 py-1 text-right font-mono text-sm text-brand" />
                  </div>
                </div>
              </div>
              <PickOfDay matchups={matchups} oddsMap={oddsMap} bankroll={bankroll} />
            </div>

            <div>
              <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
                {matchups.map((m) => (
                  <MatchupCardCompact
                    key={m.id}
                    matchup={m}
                    odds={oddsMap[m.id] || {}}
                    onOpen={() => setOpenId(m.id)}
                    onRemove={() => removeMatchup(m.id)}
                    teams={teams}
                    setMatchup={(patch) => updateMatchup(m.id, patch)}
                  />
                ))}
                <button onClick={addMatchup} className="rounded-2xl ring-1 ring-dashed ring-white/15 text-white/40 text-sm font-semibold py-10 active:bg-white/[0.02]">
                  + Agregar cruce
                </button>
              </div>
              <p className="fs-9 text-white/20 text-center pt-6 leading-relaxed">
                {pipelineStatus === "ok" ? "Datos generados automáticamente por GitHub Actions." : "Elo de respaldo — editable."}<br/>
                Herramienta de análisis, no garantiza resultados.
              </p>
            </div>
          </div>
        </>
      )}

      {activeTab === "log" && <div className="px-6 py-6 max-w-4xl mx-auto"><BetLogTab entries={betLog} setEntries={setBetLog} /></div>}
      {activeTab === "reports" && <div className="px-6 py-6 max-w-3xl mx-auto"><ReportsTab entries={betLog} /></div>}
      {activeTab === "health" && <div className="px-6 py-6 max-w-3xl mx-auto"><ModelHealthTab entries={betLog} /></div>}

      {openMatchup && (
        <MatchupDetailPanel
          matchup={openMatchup}
          setMatchup={(patch) => updateMatchup(openMatchup.id, patch)}
          odds={oddsMap[openMatchup.id] || {}}
          setOdds={(o) => setOddsForMatchup(openMatchup.id, o)}
          onClose={() => setOpenId(null)}
          bankroll={bankroll}
          onAddToLog={handleAddToLog}
        />
      )}
    </div>
  );
}
