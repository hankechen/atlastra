// Shared helpers for the Atlastra games (Guess the Rating, Daily Challenge,
// Higher or Lower, Guess the Player, Draft Battle). Loaded after api.js.

// ---- closeness scoring for rating-guess games ----
function scoreFor(diff) {
  if (diff === 0) return 100;
  if (diff === 1) return 70;
  if (diff === 2) return 50;
  if (diff === 3) return 30;
  if (diff === 4) return 15;
  if (diff <= 6) return 5;
  return 0;
}
function verdictFor(diff) {
  if (diff === 0) return { t: 'Bang on! 🎯', c: 'var(--green)' };
  if (diff <= 1) return { t: 'Spot on!', c: 'var(--green)' };
  if (diff <= 2) return { t: 'Very close!', c: 'var(--green)' };
  if (diff <= 4) return { t: 'Close-ish', c: 'var(--gold)' };
  if (diff <= 6) return { t: 'Not far off', c: 'var(--gold)' };
  return { t: 'Way off', c: 'var(--red)' };
}

// ---- per-game personal stats, kept in the synced Store blob under `games[key]` ----
function loadStats(key, defaults) {
  const all = (Store._read().games) || {};
  // migrate the original single-game `game` blob into games.guess
  if (key === 'guess' && !all.guess && Store._read().game) {
    return Object.assign({}, defaults, Store._read().game);
  }
  return Object.assign({}, defaults, all[key] || {});
}
function saveStats(key, obj) {
  const s = Store._read();
  s.games = s.games || {};
  s.games[key] = obj;
  Store._write(s);
}

// ---- global leaderboard ----
async function postScore(game, period, score) {
  if (!Auth.user) return null;                 // guests keep local scores only
  try { return await apiPost('/api/score', { game, period, score }); }
  catch { return null; }
}
async function fetchLeaderboard(game, period, limit = 25) {
  try { return await api(`/api/leaderboard?game=${encodeURIComponent(game)}&period=${encodeURIComponent(period)}&limit=${limit}`); }
  catch { return []; }
}
// today's date as a stable YYYY-MM-DD key (local time)
function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function flagFor(cc) { return cc ? (flagEmoji(cc) || '') : ''; }

// Leaderboard table HTML. `meName` highlights the signed-in user's row.
function leaderboardHTML(rows, meName, scoreLabel = 'Score') {
  if (!rows || !rows.length)
    return `<div class="lb-empty">No scores yet — be the first on the board!</div>`;
  const medal = (r) => r === 1 ? '🥇' : r === 2 ? '🥈' : r === 3 ? '🥉' : r;
  return `<table class="lb"><thead><tr><th>#</th><th>Player</th><th>${scoreLabel}</th></tr></thead><tbody>${
    rows.map(r => `<tr class="${r.username === meName ? 'me' : ''}">
      <td class="rk">${medal(r.rank)}</td><td class="who">@${r.username}</td>
      <td class="sc">${Math.round(r.score).toLocaleString()}</td></tr>`).join('')}</tbody></table>`;
}

// ---- score predictor (shared by /predict.html and the match page) ----
// Predictions live in the synced Store under games.predict:
//   { preds: { [eventId]: {h,a, ko, comp, home, away, ...meta, done, pts, ah, aa} }, total }
const PRED_KEY = 'predict';
const PRED_EXACT = 5, PRED_RESULT = 2;
const predBlank = () => ({ preds: {}, total: 0 });
const predStore = () => loadStats(PRED_KEY, predBlank());
const predOutcome = (h, a) => h > a ? 'H' : h < a ? 'A' : 'D';
function predPoints(ph, pa, ah, aa) {
  if (ph === ah && pa === aa) return PRED_EXACT;            // exact score
  if (predOutcome(ph, pa) === predOutcome(ah, aa)) return PRED_RESULT;  // right result
  return 0;
}
const predRecompute = (d) => { d.total = Object.values(d.preds).reduce((s, p) => s + (p.done ? p.pts : 0), 0); };
// Save (or clear, when h/a are blank) a prediction; `meta` carries the display
// fields (home/away/ko/comp/...) needed to render it later. Returns the new store.
function predSave(eid, meta, h, a) {
  const d = predStore();
  if (h == null || a == null || isNaN(h) || isNaN(a)) delete d.preds[eid];
  else d.preds[eid] = Object.assign({}, meta, { h, a });
  saveStats(PRED_KEY, d);
  if (typeof Auth !== 'undefined' && Auth.push) Auth.push();   // sync the blob when signed in
  return d;
}
// Settle one event against its finished result; posts the new total. True if newly settled.
function predSettle(eid, ah, aa, comp) {
  const d = predStore(); const p = d.preds[eid];
  if (!p || p.done) return false;
  p.done = true; p.ah = ah; p.aa = aa; p.pts = predPoints(p.h, p.a, ah, aa);
  if (comp) p.comp = comp;
  predRecompute(d); saveStats(PRED_KEY, d);
  postScore(PRED_KEY, 'alltime', d.total);
  return true;
}

// A small "sign in to compete" nudge for guests, shown under leaderboards.
function signInNudge() {
  if (Auth.user) return '';
  return `<div class="lb-nudge">Your scores are saved on this device. <a href="#" onclick="openAuthModal();return false">Sign in</a> to post them to the global leaderboard.</div>`;
}
