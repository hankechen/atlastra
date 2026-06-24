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

// A small "sign in to compete" nudge for guests, shown under leaderboards.
function signInNudge() {
  if (Auth.user) return '';
  return `<div class="lb-nudge">Your scores are saved on this device. <a href="#" onclick="openAuthModal();return false">Sign in</a> to post them to the global leaderboard.</div>`;
}
