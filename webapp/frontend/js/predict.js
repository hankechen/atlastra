renderSidebar('Predictions');
attachSearchDropdown(document.getElementById('searchBox'));

// Prediction storage + scoring helpers (predStore/predSave/predSettle/predPoints,
// PRED_KEY/PRED_EXACT) are shared from games.js. Scoring is settled on the client
// from real match results (/api/match per event -- authoritative for ANY past match,
// not just ones still in the live window).
function renderScoreboard(d) {
  const done = Object.values(d.preds).filter(p => p.done);
  const exact = done.filter(p => p.pts === PRED_EXACT).length;
  const hit = done.filter(p => p.pts > 0).length;
  const acc = done.length ? Math.round(hit / done.length * 100) : 0;
  const cell = (v, l, hot) => `<div class="gr-sb${hot ? ' hot' : ''}"><b>${v}</b><span>${l}</span></div>`;
  document.getElementById('scoreboard').innerHTML =
    cell(d.total, 'Total points', d.total > 0) +
    cell(Object.keys(d.preds).length, 'Predictions') +
    cell(exact, 'Exact scores') +
    cell(done.length ? acc + '%' : '—', 'Result hit-rate');
}

// One fixture object (from /api/live or a stored prediction) -> teamBadge-ready shape.
const asMatch = (m) => ({
  home: m.home, away: m.away, home_logo: m.home_logo, away_logo: m.away_logo,
  home_country: m.home_country, away_country: m.away_country,
});
const koLabel = (ts) => {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }) +
    '<br>' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

function fixtureRow(m, pred, locked, liveTxt) {
  const v = (x) => (x ?? x === 0) ? x : '';
  const state = liveTxt ? `<span class="live">● ${liveTxt}</span>`
    : pred ? '<span class="saved">✓ Saved</span>'
    : locked ? '<span class="lock">Locked</span>' : '';
  const dis = locked ? ' disabled' : '';
  const set = pred ? ' set' : '';
  return `<div class="pred-row" data-eid="${m.event_id}">
    <div class="pred-meta">${koLabel(m.kickoff_ts)}</div>
    <div class="pred-team home">${teamBadge(asMatch(m), 'home')}<span class="pn">${m.home}</span></div>
    <div class="pred-score">
      <input class="pin${set}" data-side="h" type="number" min="0" max="30" inputmode="numeric" value="${pred ? v(pred.h) : ''}"${dis}>
      <span class="dash">-</span>
      <input class="pin${set}" data-side="a" type="number" min="0" max="30" inputmode="numeric" value="${pred ? v(pred.a) : ''}"${dis}>
    </div>
    <div class="pred-team away"><span class="pn">${m.away}</span>${teamBadge(asMatch(m), 'away')}</div>
    <div class="pred-state">${state}</div>
  </div>`;
}

function renderFixtures(d, feed) {
  const now = Date.now() / 1000;
  const liveById = {}; (feed.live || []).forEach(m => liveById[m.event_id] = m);
  // predictable = upcoming (not kicked off) + currently live (locked but show their score)
  const rows = [...(feed.live || []), ...(feed.upcoming || [])]
    .filter((m, i, a) => a.findIndex(x => x.event_id === m.event_id) === i)
    .sort((x, y) => x.kickoff_ts - y.kickoff_ts);
  const el = document.getElementById('fixtures');
  if (!rows.length) { el.innerHTML = '<div class="placeholder-note">No upcoming fixtures right now — check back near kickoff.</div>'; return; }
  document.getElementById('fxNote').textContent = `${rows.length} match${rows.length > 1 ? 'es' : ''}`;
  // group by competition, preserving kickoff order
  const groups = [];
  for (const m of rows) {
    let g = groups.find(x => x.comp === m.competition);
    if (!g) { g = { comp: m.competition, items: [] }; groups.push(g); }
    g.items.push(m);
  }
  el.innerHTML = groups.map(g => `<div class="pred-grp"><div class="ghead">${g.comp}</div>${
    g.items.map(m => {
      const live = liveById[m.event_id];
      const locked = !!live || m.kickoff_ts <= now;
      const liveTxt = live ? `${live.home_score ?? 0}-${live.away_score ?? 0}${live.minute ? " " + live.minute + "'" : ''}` : '';
      return fixtureRow(m, d.preds[m.event_id], locked, liveTxt);
    }).join('')}</div>`).join('');
}

function renderResults(d) {
  const done = Object.values(d.preds).filter(p => p.done).sort((a, b) => b.ko - a.ko);
  const el = document.getElementById('results');
  if (!done.length) { el.innerHTML = '<div class="placeholder-note">No settled predictions yet — your finished calls will show here.</div>'; return; }
  el.innerHTML = done.map(p => {
    const hit = p.pts > 0;
    return `<div class="pres">
      <span class="comp">${p.comp || ''}</span>
      <span class="line">${p.home} <b>${p.ah}-${p.aa}</b> ${p.away}
        <span class="guess${p.pts === PRED_EXACT ? ' hit' : ''}">· your call ${p.h}-${p.a}</span></span>
      <span class="pts ${hit ? 'pos' : 'zero'}">${hit ? '+' + p.pts : '0'}</span></div>`;
  }).join('');
}

// Settle any prediction whose kickoff has passed but isn't scored yet, by reading
// the authoritative result from /api/match (works for matches outside the live window).
async function settlePending(d) {
  const now = Date.now() / 1000;
  const pend = Object.entries(d.preds).filter(([, p]) => !p.done && p.ko && p.ko < now).slice(0, 24);
  if (!pend.length) return false;
  const res = await Promise.all(pend.map(([eid]) => api('/api/match?id=' + eid).catch(() => null)));
  let changed = false;
  pend.forEach(([eid], i) => {
    const m = res[i];
    if (m && m.available && m.status === 'finished' && m.home_score != null) {
      if (predSettle(eid, m.home_score, m.away_score, m.competition)) changed = true;
    }
  });
  return changed;
}

// Save (or clear) a prediction when an input changes. Both boxes must be filled.
function onInput(e) {
  const inp = e.target.closest('.pin'); if (!inp) return;
  const row = inp.closest('.pred-row'); const eid = row.dataset.eid;
  const ins = row.querySelectorAll('.pin');
  const h = ins[0].value === '' ? null : Math.max(0, Math.min(30, parseInt(ins[0].value, 10)));
  const a = ins[1].value === '' ? null : Math.max(0, Math.min(30, parseInt(ins[1].value, 10)));
  const m = FIXTURES[eid];
  const cleared = h == null || a == null || isNaN(h) || isNaN(a);
  const meta = m ? {
    ko: m.kickoff_ts, comp: m.competition, home: m.home, away: m.away,
    home_logo: m.home_logo, away_logo: m.away_logo,
    home_country: m.home_country, away_country: m.away_country,
  } : {};
  const d = predSave(eid, meta, cleared ? null : h, cleared ? null : a);
  ins.forEach(x => x.classList.toggle('set', !cleared));
  row.querySelector('.pred-state').innerHTML = cleared
    ? (m && m.kickoff_ts <= Date.now() / 1000 ? '<span class="lock">Locked</span>' : '')
    : '<span class="saved">✓ Saved</span>';
  renderScoreboard(d);
}

let FIXTURES = {};                 // eventId -> fixture object (for input handler)

async function loadLeaderboard() {
  const rows = await fetchLeaderboard(PRED_KEY, 'alltime', 25);
  document.getElementById('leaderboard').innerHTML =
    leaderboardHTML(rows, Auth.user && Auth.user.username, 'Pts') + signInNudge();
}

async function load() {
  let d = predStore();
  let feed = { live: [], upcoming: [], recent: [] };
  try { feed = await api('/api/live?upcoming=30&recent=0'); } catch { /* offline */ }
  FIXTURES = {};
  [...(feed.live || []), ...(feed.upcoming || [])].forEach(m => FIXTURES[m.event_id] = m);
  try { await settlePending(d); } catch { /* network */ }
  d = predStore();                 // pick up any just-settled results
  renderScoreboard(d);
  renderFixtures(d, feed);
  renderResults(d);
  loadLeaderboard();
}

document.getElementById('fixtures').addEventListener('change', onInput);
document.getElementById('clearBtn').onclick = (e) => {
  e.preventDefault();
  if (!confirm('Clear all your predictions and points?')) return;
  saveStats(PRED_KEY, predBlank()); load();
};

load();
// keep live scores / settlements fresh while the page is open -- but never while a
// score box is focused, so an in-progress entry isn't wiped by the re-render.
setInterval(() => {
  if (!document.hidden && !(document.activeElement && document.activeElement.classList.contains('pin'))) load();
}, 60000);
