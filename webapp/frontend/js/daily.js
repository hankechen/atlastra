renderSidebar('Daily Challenge');
attachSearchDropdown(document.getElementById('searchBox'));

const DKEY = 'daily';
const DDEF = { history: {} };          // history[date] = {score, results:[...]}
let data = null, idx = 0, guess = 75, results = [], total = 0;

async function start() {
  const out = document.getElementById('dc-out');
  const stats = loadStats(DKEY, DDEF);
  const today = todayKey();
  if (stats.history[today]) { renderFinal(stats.history[today].score, stats.history[today].results, true); return; }
  try { data = await api(`/api/daily_challenge?date=${today}`); } catch { out.innerHTML = '<section class="card"><div class="empty">Could not load the challenge.</div></section>'; return; }
  if (!data.available || !data.rounds.length) { out.innerHTML = '<section class="card"><div class="empty">No challenge available today.</div></section>'; return; }
  idx = 0; results = []; total = 0; guess = 75;
  renderRound();
}

function dots() {
  return `<div class="dc-progress">${data.rounds.map((_, i) =>
    `<span class="dc-dot ${i < idx ? 'done' : ''}"></span>`).join('')}</div>`;
}

function renderRound() {
  const c = data.rounds[idx];
  const meta = `<div class="dc-meta"><span class="dc-pos">${c.position}</span>
    <span class="dc-tag">${crestHTML(c.team_logo, 'crest-sm')}${c.team}</span>
    ${c.age ? `<span class="dc-tag">${flagFor(c.country_code)} ${c.nationality || ''} · ${c.age}y</span>` : ''}</div>`;
  const stats = c.stats.map(s => `<div class="dc-stat"><span class="l">${s.label}</span><span class="v">${s.value}</span></div>`).join('');
  document.getElementById('dc-out').innerHTML = `
    ${dots()}
    <div class="dc-top">
      <section class="card">${meta}
        <div class="card-h" style="margin-top:6px"><h3>Player ${idx + 1} of ${data.rounds.length}</h3><span class="see">guess the rating</span></div>
        <div class="dc-statgrid">${stats}</div></section>
      <section class="card" id="dc-panel"></section>
    </div>`;
  renderPanel();
}

function renderPanel() {
  document.getElementById('dc-panel').innerHTML = `
    <div class="dc-q">?</div>
    <div class="dc-bignum" id="dcNum">${guess}</div>
    <div class="placeholder-note" style="text-align:center;font-style:normal;color:var(--muted)">Drag to set your rating guess</div>
    <input class="dc-slider" id="dcSlider" type="range" min="50" max="99" value="${guess}">
    <div class="dc-scale"><span>50</span><span>75</span><span>99</span></div>
    <button class="btn btn-primary" id="dcSubmit" style="width:100%">${idx === data.rounds.length - 1 ? 'Finish & submit' : 'Lock in & next'}</button>`;
  const sl = document.getElementById('dcSlider'), n = document.getElementById('dcNum');
  sl.oninput = () => { guess = +sl.value; n.textContent = guess; };
  document.getElementById('dcSubmit').onclick = lockIn;
  window.onkeydown = (e) => { if (e.key === 'Enter') lockIn(); };
}

function lockIn() {
  const c = data.rounds[idx], diff = Math.abs(guess - c.rating), pts = scoreFor(diff);
  total += pts;
  results.push({ name: c.name, photo: c.photo, team_logo: c.team_logo, guess, actual: c.rating, diff, pts });
  idx += 1; guess = 75;
  if (idx < data.rounds.length) renderRound();
  else finish();
}

function finish() {
  const stats = loadStats(DKEY, DDEF);
  stats.history[todayKey()] = { score: total, results };
  saveStats(DKEY, stats);
  postScore('daily', todayKey(), total);
  renderFinal(total, results, false);
}

function renderFinal(score, res, replay) {
  const correct = res.filter(r => r.diff <= 2).length;
  const rows = res.map(r => `<div class="dc-rowmini">
    <span class="pic" style="width:26px;height:26px;border-radius:50%;overflow:hidden;background:var(--card2);position:relative">${avatarHTML(r.photo, r.name)}</span>
    <span class="nm">${r.name}</span>
    <span class="muted">${r.guess} → ${r.actual}</span>
    <span class="pts" style="color:${r.diff <= 2 ? 'var(--green)' : r.diff <= 4 ? 'var(--gold)' : 'var(--red)'}">+${r.pts}</span></div>`).join('');
  document.getElementById('dc-out').innerHTML = `<div class="dc-top">
    <section class="card dc-final">
      <div class="card-h"><h3>${replay ? "Today's result" : 'Challenge complete'}</h3><span class="see">${todayKey()}</span></div>
      <div class="dc-total">${score}</div>
      <p class="muted" style="margin:2px 0 14px">${correct} / ${res.length} within 2 of the real rating</p>
      <div style="text-align:left">${rows}</div>
      ${replay ? '<p class="muted" style="margin-top:14px">Come back tomorrow for a new challenge.</p>'
        : '<p class="muted" style="margin-top:14px">Score locked in for today.</p>'}
    </section>
    <section class="card" id="lbCard"><div class="card-h"><h3>Today's Leaderboard</h3></div><div class="placeholder-note">Loading…</div></section>
  </div>`;
  loadBoard();
}

async function loadBoard() {
  const card = document.getElementById('lbCard'); if (!card) return;
  const mine = (loadStats(DKEY, DDEF).history[todayKey()] || {}).score || 0;
  const r = await syncScore('daily', todayKey(), mine);          // post today's score if signed in
  const rows = (r && r.leaderboard) ? r.leaderboard : await fetchLeaderboard('daily', todayKey());
  card.innerHTML = `<div class="card-h"><h3>Today's Leaderboard</h3><span class="see">global</span></div>
    ${leaderboardHTML(rows, Auth.user && Auth.user.username, 'Score')}${signInNudge()}`;
}

start();
