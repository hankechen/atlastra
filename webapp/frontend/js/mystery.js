renderSidebar('Guess the Player');
attachSearchDropdown(document.getElementById('searchBox'));

const QKEY = 'quiz';
const QDEF = { played: 0, solved: 0, daily: {} };
const norm = (s) => (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
  .toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();

let mode = 'daily', puzzle = null, wrong = [], revealed = 1, done = false;

function renderTabs() {
  document.getElementById('modeTabs').innerHTML =
    `<button class="pill-btn ${mode === 'daily' ? 'active' : ''}" data-m="daily">Daily puzzle</button>
     <button class="pill-btn ${mode === 'practice' ? 'active' : ''}" data-m="practice">Practice (endless)</button>`;
  document.querySelectorAll('#modeTabs .pill-btn').forEach(b => b.onclick = () => {
    if (b.dataset.m === mode) return; mode = b.dataset.m; renderTabs(); load();
  });
}

async function load() {
  const out = document.getElementById('my-out');
  out.innerHTML = '<section class="card"><div class="placeholder-note">Loading puzzle…</div></section>';
  wrong = []; revealed = 1; done = false;
  const url = mode === 'daily' ? `/api/player_quiz?date=${todayKey()}` : '/api/player_quiz';
  try { puzzle = await api(url); } catch { out.innerHTML = '<section class="card"><div class="empty">Could not load the puzzle.</div></section>'; return; }
  if (!puzzle.available) { out.innerHTML = '<section class="card"><div class="empty">No puzzle available.</div></section>'; return; }
  // already played today's daily? show the result straight away.
  if (mode === 'daily') {
    const rec = loadStats(QKEY, QDEF).daily[todayKey()];
    if (rec) { renderDone(rec.solved, rec.score, rec.guesses || 0, true); return; }
  }
  render();
}

function render() {
  const maxG = puzzle.max_guesses;
  const clues = puzzle.clues.map((c, i) => {
    const open = i < revealed;
    const flag = c.country_code ? flagFor(c.country_code) + ' ' : '';
    return `<div class="clue ${open ? '' : 'locked'}">
      <span class="cl">${open ? c.label : 'Clue ' + (i + 1)}</span>
      <span class="cv">${open ? flag + c.value : '🔒'}</span></div>`;
  }).join('');
  const tags = wrong.map(w => `<span class="gtag bad">✕ ${w}</span>`).join('');
  document.getElementById('my-out').innerHTML = `<div class="my-wrap">
    <section class="card">
      <div class="card-h"><h3>Clues</h3><span class="see">${revealed} / ${maxG} revealed</span></div>
      ${clues}
    </section>
    <section class="card">
      <div class="card-h"><h3>Your guess</h3><span class="see">guess ${wrong.length + 1} of ${maxG}</span></div>
      ${tags ? `<div class="my-guesses">${tags}</div>` : ''}
      <div class="my-inputwrap">
        <input class="my-input" id="guessInput" placeholder="Type a player name…" autocomplete="off">
        <div class="my-dd" id="guessDD"></div>
      </div>
      <div class="my-attempts">${maxG - wrong.length} guess${maxG - wrong.length === 1 ? '' : 'es'} left · each wrong guess unlocks a clue</div>
    </section></div>`;
  setupInput();
}

function setupInput() {
  const input = document.getElementById('guessInput'), dd = document.getElementById('guessDD');
  input.focus();
  let timer, items = [], hi = -1;
  const close = () => { dd.classList.remove('open'); hi = -1; };
  input.addEventListener('input', () => {
    clearTimeout(timer); const q = input.value.trim();
    if (!q) { close(); return; }
    timer = setTimeout(async () => {
      let d; try { d = await api('/api/search?q=' + encodeURIComponent(q)); } catch { return; }
      items = (d.players || []).slice(0, 6);
      if (!items.length) { close(); return; }
      dd.innerHTML = items.map((p, i) => `<a data-i="${i}"><span class="pic">${avatarHTML(p.photo, p.player)}</span>${p.player}</a>`).join('');
      dd.classList.add('open');
      dd.querySelectorAll('a').forEach(a => a.onclick = () => submit(items[+a.dataset.i].player));
    }, 160);
  });
  input.addEventListener('keydown', (e) => {
    const links = [...dd.querySelectorAll('a')];
    if (e.key === 'ArrowDown') { e.preventDefault(); hi = Math.min(hi + 1, links.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); hi = Math.max(hi - 1, 0); }
    else if (e.key === 'Enter') { e.preventDefault(); submit(hi >= 0 && items[hi] ? items[hi].player : input.value.trim()); return; }
    else return;
    links.forEach((l, i) => l.classList.toggle('hi', i === hi));
  });
  document.addEventListener('click', (e) => { if (!dd.contains(e.target) && e.target !== input) close(); });
}

function isCorrect(guess) {
  const n = norm(guess), accept = puzzle.accept || [];
  return accept.includes(n) || accept.includes(n.replace(/ /g, '')) || n === norm(puzzle.answer);
}

function submit(guess) {
  if (done || !guess || !guess.trim()) return;
  if (isCorrect(guess)) { finish(true); return; }
  wrong.push(guess.trim());
  revealed = Math.min(wrong.length + 1, puzzle.max_guesses);
  if (wrong.length >= puzzle.max_guesses) { finish(false); return; }
  render();
}

function finish(solved) {
  done = true;
  const maxG = puzzle.max_guesses;
  const guessesUsed = solved ? wrong.length + 1 : maxG;
  const score = solved ? (maxG - wrong.length) * 100 : 0;   // fewer wrong → more points
  // personal stats
  const g = loadStats(QKEY, QDEF);
  g.played += 1; if (solved) g.solved += 1;
  if (mode === 'daily') {
    g.daily[todayKey()] = { solved, score, guesses: guessesUsed };
    if (solved) postScore('quiz', todayKey(), score);   // to global leaderboard
  }
  saveStats(QKEY, g);
  renderDone(solved, score, guessesUsed, false);
}

function renderDone(solved, score, guessesUsed, replay) {
  const p = puzzle, photo = p.photo ? `<img src="${p.photo}" onerror="this.remove()">` : `<span class="ini">${initials(p.answer)}</span>`;
  const head = solved
    ? `<div class="gr-verdict" style="color:var(--green);font-size:20px;font-weight:800">${replay ? 'You solved today\'s puzzle' : 'Correct!'} ${solved ? '🎉' : ''}</div>`
    : `<div class="gr-verdict" style="color:var(--red);font-size:20px;font-weight:800">Out of guesses</div>`;
  const sline = mode === 'daily'
    ? (solved ? `<div class="gr-pts" style="font-weight:700">+${score} points · solved in ${guessesUsed} guess${guessesUsed === 1 ? '' : 'es'}</div>` : '<div class="gr-pts muted">No points today — better luck tomorrow.</div>')
    : (solved ? `<div class="gr-pts" style="font-weight:700">Solved in ${guessesUsed} guess${guessesUsed === 1 ? '' : 'es'}</div>` : '');
  const btn = mode === 'practice'
    ? `<button class="btn btn-primary" id="nextBtn" style="max-width:240px;margin:14px auto 0">Next player →</button>`
    : `<a class="btn btn-ghost" href="/daily.html" style="max-width:260px;margin:14px auto 0">Try the Daily Challenge →</a>`;
  document.getElementById('my-out').innerHTML = `<div class="my-wrap">
    <section class="card my-reveal">
      <div class="my-photo">${photo}</div>
      <div class="hl-nm" style="font-size:21px;font-weight:800"><a href="${pHref(p.answer)}" style="color:inherit;text-decoration:none">${p.answer}</a></div>
      <div class="hl-sub" style="color:var(--muted);font-size:12.5px;margin-top:4px;justify-content:center;display:flex;gap:6px;align-items:center">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · Atlastra ${p.rating}</div>
      ${head}${sline}${btn}
    </section>
    <section class="card" id="lbCard"><div class="card-h"><h3>${mode === 'daily' ? "Today's Leaderboard" : 'Leaderboard'}</h3></div><div class="placeholder-note">Loading…</div></section>
  </div>`;
  if (mode === 'practice') document.getElementById('nextBtn').onclick = load;
  loadBoard();
}

async function loadBoard() {
  const card = document.getElementById('lbCard'); if (!card) return;
  if (mode !== 'daily') { card.innerHTML = '<div class="card-h"><h3>Practice mode</h3></div><div class="lb-empty">Practice scores aren\'t ranked. Play the Daily puzzle to hit the global board.</div>'; return; }
  const rows = await fetchLeaderboard('quiz', todayKey());
  card.innerHTML = `<div class="card-h"><h3>Today's Leaderboard</h3><span class="see">${todayKey()}</span></div>
    ${leaderboardHTML(rows, Auth.user && Auth.user.username, 'Points')}${signInNudge()}`;
}

renderTabs();
load();
