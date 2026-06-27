renderSidebar('Guess the Rating');
attachSearchDropdown(document.getElementById('searchBox'));

// ---- persistent score (lives in the synced Store blob so it follows a signed-in
// user across devices, and persists in localStorage for guests) ----
const GAME_DEFAULTS = { points: 0, played: 0, correct: 0, perfect: 0, streak: 0, best: 0 };
function loadGame() { return Object.assign({}, GAME_DEFAULTS, Store._read().game || {}); }
function saveGame(g) { const s = Store._read(); s.game = g; Store._write(s); }

// points for a guess that is `diff` away from the true rating
function scoreFor(diff) {
  if (diff === 0) return 100;
  if (diff === 1) return 70;
  if (diff === 2) return 50;
  if (diff === 3) return 30;
  if (diff === 4) return 15;
  if (diff <= 6) return 5;
  return 0;
}
function verdict(diff) {
  if (diff === 0) return { t: 'Bang on! 🎯', c: 'var(--green)' };
  if (diff <= 1) return { t: 'Spot on!', c: 'var(--green)' };
  if (diff <= 2) return { t: 'Very close!', c: 'var(--green)' };
  if (diff <= 4) return { t: 'Close-ish', c: 'var(--gold)' };
  if (diff <= 6) return { t: 'Not far off', c: 'var(--gold)' };
  return { t: 'Way off', c: 'var(--red)' };
}

let queue = [], current = null, guess = 75;

function renderScoreboard() {
  const g = loadGame();
  const acc = g.played ? Math.round(g.correct / g.played * 100) : 0;
  const cell = (val, label, hot) =>
    `<div class="gr-sb${hot ? ' hot' : ''}"><b>${val}</b><span>${label}</span></div>`;
  document.getElementById('scoreboard').innerHTML =
    cell(g.points.toLocaleString(), 'Points') +
    cell((g.streak >= 2 ? '🔥' : '') + g.streak, 'Streak', g.streak >= 2) +
    cell(acc + '%', 'Accuracy') +
    cell(g.played, 'Played') +
    cell(g.best, 'Best streak');
}

async function refill() {
  const d = await api('/api/guess?count=10');
  if (d.available) queue = queue.concat(d.rounds || []);
}

async function nextRound() {
  const out = document.getElementById('gr-out');
  if (queue.length < 2) {
    try { await refill(); } catch { out.innerHTML = '<section class="card"><div class="empty">Could not load players. Try again.</div></section>'; return; }
  }
  current = queue.shift();
  if (!current) { out.innerHTML = '<section class="card"><div class="empty">No players available.</div></section>'; return; }
  guess = 75;
  renderRound();
}

function flagFor(cc) { return cc ? (flagEmoji(cc) || '') : ''; }

function renderRound() {
  const c = current;
  const meta = `<div class="gr-meta">
      <span class="gr-pos">${c.position}</span>
      <span class="gr-tag">${crestHTML(c.team_logo, 'crest-sm')}${c.team || ''}</span>
      ${c.age ? `<span class="gr-tag">${flagFor(c.country_code)} ${c.nationality || ''} · ${c.age}y</span>` : ''}
    </div>`;
  const stats = c.stats.map(s =>
    `<div class="gr-stat"><span class="l">${s.label}</span><span class="v">${s.value == null ? '—' : s.value}</span></div>`).join('');
  document.getElementById('gr-out').innerHTML = `<div class="gr-wrap">
    <section class="card">
      ${meta}
      <div class="card-h" style="margin-top:6px"><h3>Mystery player — 2025/26</h3>
        <span class="see">guess the rating</span></div>
      <div class="gr-statgrid">${stats}</div>
    </section>
    <section class="card gr-guess" id="gr-panel"></section>
  </div>`;
  renderGuessPanel();
}

function renderGuessPanel() {
  document.getElementById('gr-panel').innerHTML = `
    <div class="gr-photo"><span class="gr-q">?</span></div>
    <div class="gr-bignum" id="grNum">${guess}</div>
    <div class="gr-hint">Drag to set your rating guess</div>
    <input class="gr-slider" id="grSlider" type="range" min="50" max="99" step="1" value="${guess}">
    <div class="gr-scale"><span>50</span><span>75</span><span>99</span></div>
    <button class="btn btn-primary gr-btn" id="grSubmit">Lock in guess</button>`;
  const slider = document.getElementById('grSlider'), num = document.getElementById('grNum');
  slider.oninput = () => { guess = +slider.value; num.textContent = guess; };
  document.getElementById('grSubmit').onclick = submitGuess;
  window.onkeydown = (e) => { if (e.key === 'Enter' && document.getElementById('grSubmit')) submitGuess(); };
}

function submitGuess() {
  const c = current, actual = c.rating, diff = Math.abs(guess - actual);
  const v = verdict(diff), base = scoreFor(diff), correct = diff <= 2;

  const g = loadGame();
  g.played += 1;
  g.streak = correct ? g.streak + 1 : 0;
  g.best = Math.max(g.best, g.streak);
  if (correct) g.correct += 1;
  if (diff === 0) g.perfect += 1;
  const bonus = correct && g.streak >= 2 ? (g.streak - 1) * 5 : 0;   // streak reward
  const earned = base + bonus;
  g.points += earned;
  saveGame(g);
  renderScoreboard();
  // cumulative points -> global leaderboard (max-kept, so the running total ranks)
  postScore('guess', 'alltime', g.points).then(res => { if (res && res.leaderboard) renderLB(res.leaderboard); });

  const photo = c.photo
    ? `<img src="${c.photo}" alt="" onerror="this.remove()">` : `<span class="ini">${initials(c.name)}</span>`;
  document.getElementById('gr-panel').innerHTML = `<div class="gr-reveal">
    <div class="gr-photo">${photo}</div>
    <div class="gr-name"><a href="${pHref(c.name)}">${c.name}</a></div>
    <div class="gr-tag" style="justify-content:center;margin-top:4px">${crestHTML(c.team_logo, 'crest-sm')}${c.team || ''}</div>
    <div class="gr-vs">
      <div class="col"><b>${guess}</b><span>Your guess</span></div>
      <div class="col"><b style="color:${v.c}">${actual}</b><span>Actual</span></div>
    </div>
    <div class="gr-verdict" style="color:${v.c}">${v.t}</div>
    <div class="gr-pts">+${earned} points${bonus ? ` <span class="muted">(${base} +${bonus} streak)</span>` : ''}</div>
    <button class="btn btn-primary gr-btn" id="grNext" style="margin-top:16px">Next player →</button>
  </div>`;
  document.getElementById('grNext').onclick = nextRound;
  window.onkeydown = (e) => { if (e.key === 'Enter') nextRound(); };
}

document.getElementById('resetBtn').onclick = () => {
  if (!confirm('Reset your Guess the Rating score back to zero?')) return;
  saveGame({ ...GAME_DEFAULTS });
  renderScoreboard();
};

// ---- global leaderboard (total points) ----
function renderLB(rows) {
  const card = document.getElementById('lbCard'); if (!card) return;
  card.innerHTML = `<div class="card-h"><h3>Global Leaderboard</h3><span class="see">Total points</span></div>
    ${leaderboardHTML(rows, Auth.user && Auth.user.username, 'Points')}${signInNudge()}`;
}
async function loadBoard() { try { renderLB(await fetchLeaderboard('guess', 'alltime')); } catch { /* offline */ } }

renderScoreboard();
nextRound();
loadBoard();
