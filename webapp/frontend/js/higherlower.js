renderSidebar('Higher or Lower');
attachSearchDropdown(document.getElementById('searchBox'));

const STATS_KEY = 'higherlower';
const DEFAULTS = { best: 0, played: 0 };
const statVal = (p, label) => { const s = p.stats.find(x => x.label === label); return s ? Number(s.value) : 0; };
// like statVal but NaN when the stat is missing (null) — so the deck filter drops
// players without that stat instead of treating them as 0 (e.g. no datamb data).
const statValN = (p, label) => { const s = p.stats.find(x => x.label === label); return s && s.value != null ? Number(s.value) : NaN; };
const METRICS = [
  { key: 'rating', label: 'Atlastra Rating', get: p => p.rating, fmt: v => v },
  { key: 'goals', label: 'Goals', get: p => statVal(p, 'Goals'), fmt: v => v },
  { key: 'assists', label: 'Assists', get: p => statVal(p, 'Assists'), fmt: v => v },
  { key: 'xg90', label: 'xG / 90', get: p => statVal(p, 'xG / 90'), fmt: v => v.toFixed(2) },
  { key: 'dribbles', label: 'Dribbles / 90', get: p => statVal(p, 'Dribbles / 90'), fmt: v => v.toFixed(2) },
  { key: 'progpass', label: 'Prog. Passes / 90', get: p => statValN(p, 'Prog. passes / 90'), fmt: v => v.toFixed(2) },
  { key: 'progcarry', label: 'Prog. Carries / 90', get: p => statValN(p, 'Prog. carries / 90'), fmt: v => v.toFixed(2) },
];

let deck = [], metric = METRICS[0], left = null, right = null, streak = 0, locked = false;

function renderScoreboard() {
  const g = loadStats(STATS_KEY, DEFAULTS);
  const cell = (v, l, hot) => `<div class="gr-sb${hot ? ' hot' : ''}"><b>${v}</b><span>${l}</span></div>`;
  document.getElementById('scoreboard').innerHTML =
    cell((streak >= 2 ? '🔥' : '') + streak, 'Current streak', streak >= 2) +
    cell(g.best, 'Best streak') +
    cell(g.played, 'Games played');
}

function renderMetrics() {
  const el = document.getElementById('metrics');
  el.innerHTML = '<span class="lbl">Compare by:</span>' + METRICS.map(m =>
    `<button class="pill-btn ${m.key === metric.key ? 'active' : ''}" data-k="${m.key}">${m.label}</button>`).join('');
  el.querySelectorAll('.pill-btn').forEach(b => b.onclick = () => {
    if (b.dataset.k === metric.key) return;
    metric = METRICS.find(m => m.key === b.dataset.k);
    renderMetrics(); restart();
  });
}

async function refill() {
  const d = await api('/api/guess?count=20&min_rating=66');
  if (d.available) deck = deck.concat(d.rounds.filter(r => Number.isFinite(metric.get(r))));
}
async function draw() {
  if (deck.length < 3) { try { await refill(); } catch { return null; } }
  return deck.shift() || null;
}

function card(p, side, revealed) {
  const photo = p.photo ? `<img src="${p.photo}" onerror="this.remove()">` : `<span class="ini">${initials(p.name)}</span>`;
  const valOrQ = side === 'left' || revealed
    ? `<div class="hl-val">${metric.fmt(metric.get(p))}</div>`
    : `<div class="hl-q" id="rq">?</div>`;
  return `<div class="hl-card">
    <div class="hl-photo">${photo}</div>
    <div class="hl-nm">${p.name}</div>
    <div class="hl-sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · ${p.position}</div>
    ${valOrQ}
    <div class="hl-metric-tag">${metric.label}</div></div>`;
}

function renderBoard() {
  document.getElementById('hl-out').innerHTML = `<div class="hl-board">
    ${card(left, 'left', true)}
    <div class="hl-mid"><span class="hl-vsbadge">VS</span></div>
    <div>${card(right, 'right', false)}
      <div class="hl-actions">
        <button class="btn btn-primary" id="hiBtn">▲ Higher</button>
        <button class="btn btn-ghost" id="loBtn">▼ Lower</button>
      </div></div>
  </div>`;
  document.getElementById('hiBtn').onclick = () => answer(true);
  document.getElementById('loBtn').onclick = () => answer(false);
}

function answer(saidHigher) {
  if (locked) return;
  locked = true;
  const lv = metric.get(left), rv = metric.get(right);
  const correct = saidHigher ? rv >= lv : rv <= lv;   // ties count as correct
  // reveal the right value
  const rq = document.getElementById('rq');
  if (rq) rq.outerHTML = `<div class="hl-val" style="color:${correct ? 'var(--green)' : 'var(--red)'};-webkit-text-fill-color:${correct ? 'var(--green)' : 'var(--red)'}">${metric.fmt(rv)}</div>`;
  document.getElementById('hiBtn').disabled = true;
  document.getElementById('loBtn').disabled = true;

  setTimeout(() => {
    if (correct) {
      streak += 1;
      const g = loadStats(STATS_KEY, DEFAULTS);
      if (streak > g.best) {
        g.best = streak; saveStats(STATS_KEY, g);
        // new personal best -> global leaderboard (ranked by best streak)
        postScore(STATS_KEY, 'alltime', g.best).then(res => { if (res && res.leaderboard) renderLB(res.leaderboard); });
      }
      renderScoreboard();
      next();
    } else {
      gameOver();
    }
  }, 850);
}

async function next() {
  left = right;
  right = await draw();
  // avoid an identical back-to-back name
  while (right && left && right.name === left.name) right = await draw();
  if (!right) { document.getElementById('hl-out').innerHTML = '<section class="card"><div class="empty">Out of players — well played!</div></section>'; return; }
  locked = false;
  renderBoard();
}

function gameOver() {
  const g = loadStats(STATS_KEY, DEFAULTS);
  g.played += 1; saveStats(STATS_KEY, g);
  renderScoreboard();
  document.getElementById('hl-out').insertAdjacentHTML('beforeend', `
    <section class="card" style="margin-top:14px;text-align:center">
      <div class="gr-verdict" style="color:var(--red);font-size:20px;font-weight:800">Streak ended at ${streak}</div>
      <p class="muted" style="margin:6px 0 14px">${right.name} had ${metric.fmt(metric.get(right))} ${metric.label} vs ${left.name}'s ${metric.fmt(metric.get(left))}.</p>
      <button class="btn btn-primary" id="againBtn" style="max-width:240px;margin:0 auto">Play again</button>
    </section>`);
  document.getElementById('againBtn').onclick = restart;
}

async function restart() {
  streak = 0; locked = false; renderScoreboard();
  document.getElementById('hl-out').innerHTML = '<section class="card"><div class="placeholder-note">Dealing…</div></section>';
  deck = [];
  left = await draw(); right = await draw();
  while (right && left && right.name === left.name) right = await draw();
  if (!left || !right) { document.getElementById('hl-out').innerHTML = '<section class="card"><div class="empty">Could not load players.</div></section>'; return; }
  renderBoard();
}

// ---- global leaderboard (best streak) ----
function renderLB(rows) {
  const card = document.getElementById('lbCard'); if (!card) return;
  card.innerHTML = `<div class="card-h"><h3>Global Leaderboard</h3><span class="see">Best streak</span></div>
    ${leaderboardHTML(rows, Auth.user && Auth.user.username, 'Streak')}${signInNudge()}`;
}
async function loadBoard() {
  try {
    const r = await syncScore(STATS_KEY, 'alltime', loadStats(STATS_KEY, DEFAULTS).best);  // post my best if signed in
    renderLB(r && r.leaderboard ? r.leaderboard : await fetchLeaderboard(STATS_KEY, 'alltime'));
  } catch { /* offline */ }
}

renderScoreboard();
renderMetrics();
restart();
loadBoard();
