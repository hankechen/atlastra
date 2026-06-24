renderSidebar('Draft Battle');
attachSearchDropdown(document.getElementById('searchBox'));

const BUDGET = 300;            // €M salary cap (fixed → fair leaderboard per formation)
const FORMATIONS = ['4-3-3', '4-4-2', '4-2-3-1', '4-1-4-1', '4-5-1', '3-5-2', '3-4-3', '5-3-2'];
let formation = '4-3-3', data = null, slots = [], picks = [], submitted = false;

const spentM = () => picks.reduce((a, p) => a + (p ? p.value_m : 0), 0);
const totalRating = () => picks.reduce((a, p) => a + (p ? p.rating : 0), 0);
const filledCount = () => picks.filter(Boolean).length;
const ratColor = (r) => r >= 85 ? '#39d07f' : r >= 78 ? '#a6d14a' : r >= 70 ? '#e7c14a' : '#e79a4a';

function renderForms() {
  document.getElementById('forms').innerHTML =
    `<span class="muted" style="font-size:12.5px">Formation:</span>` +
    FORMATIONS.map(f => `<button class="pill-btn ${f === formation ? 'active' : ''}" data-f="${f}">${f}</button>`).join('');
  document.querySelectorAll('#forms .pill-btn').forEach(b => b.onclick = () => {
    if (b.dataset.f === formation || submitted) return;
    formation = b.dataset.f; loadPool();
  });
}

async function loadPool() {
  submitted = false;
  document.getElementById('dft-out').innerHTML = '<section class="card"><div class="placeholder-note">Loading squad pool…</div></section>';
  try { data = await api('/api/draft_pool?formation=' + encodeURIComponent(formation)); }
  catch { document.getElementById('dft-out').innerHTML = '<section class="card"><div class="empty">Could not load the pool.</div></section>'; return; }
  slots = [];
  data.slots.forEach((line, li) => line.forEach((s) => slots.push({ cat: s.cat, line: li })));
  picks = slots.map(() => null);
  renderForms(); renderBudget(); renderPitch();
}

function renderBudget() {
  const rem = BUDGET - spentM(), over = rem < 0;
  const cell = (v, l, cls = '') => `<div class="dft-b ${cls}"><b>${v}</b><span>${l}</span></div>`;
  document.getElementById('budget').innerHTML =
    cell('€' + spentM() + 'M', 'Spent', over ? 'over' : '') +
    cell('€' + rem + 'M', 'Remaining', over ? 'over' : '') +
    cell(totalRating(), 'Squad rating') +
    cell(filledCount() + ' / 11', 'Picked');
}

function slotToken(i) {
  const s = slots[i], p = picks[i];
  if (!p) return `<div class="dft-slot empty" data-i="${i}">
    <div class="dft-av"><span class="plus">+</span></div>
    <div class="dft-cat">${data.cat_labels[s.cat] || s.cat}</div></div>`;
  const av = p.photo ? `<img src="${p.photo}" onerror="this.remove()">` : `<span class="ini">${initials(p.player)}</span>`;
  return `<div class="dft-slot" data-i="${i}">
    <div class="dft-av">${av}<span class="dft-rat" style="background:${ratColor(p.rating)};color:#06121f">${p.rating}</span></div>
    <div class="dft-nm">${p.player}</div><div class="dft-vl">€${p.value_m}M</div></div>`;
}

function renderPitch() {
  // group slot indices by line, render attack on top (reverse of back→front)
  const byLine = {};
  slots.forEach((s, i) => (byLine[s.line] = byLine[s.line] || []).push(i));
  const lineKeys = Object.keys(byLine).map(Number).sort((a, b) => b - a);
  const rows = lineKeys.map(lk => `<div class="dft-row">${byLine[lk].map(slotToken).join('')}</div>`).join('');
  const ready = filledCount() === 11 && spentM() <= BUDGET;
  document.getElementById('dft-out').innerHTML = `
    <section class="card">
      <div class="dft-pitch"><div class="dft-stripes"></div><div class="dft-rows">${rows}</div></div>
      <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap;align-items:center">
        <button class="btn btn-primary" id="lockBtn" ${ready ? '' : 'disabled'} style="flex:1;min-width:200px">${ready ? 'Lock in XI & battle the optimal team' : `Fill all 11 slots within €${BUDGET}M`}</button>
        <button class="btn btn-ghost" id="clearBtn">Clear</button>
      </div>
      <p class="muted" style="font-size:12px;margin-top:10px">Tap an empty slot to sign a player; tap a signed player to release them. Everyone plays the same €${BUDGET}M cap.</p>
    </section>`;
  document.querySelectorAll('.dft-slot').forEach(el => el.onclick = () => {
    if (submitted) return;
    const i = +el.dataset.i;
    if (picks[i]) { picks[i] = null; renderBudget(); renderPitch(); }
    else openPicker(i);
  });
  document.getElementById('clearBtn').onclick = () => { picks = slots.map(() => null); renderBudget(); renderPitch(); };
  const lb = document.getElementById('lockBtn');
  if (ready) lb.onclick = submit;
}

function openPicker(slotIdx) {
  const cat = slots[slotIdx].cat, rem = BUDGET - spentM();
  const pickedIds = new Set(picks.filter(Boolean).map(p => p.id));
  const cands = (data.candidates[cat] || []);
  const ov = document.createElement('div');
  ov.className = 'pk-ov';
  ov.innerHTML = `<div class="pk-card">
    <div class="pk-h"><h3>Sign a ${data.cat_labels[cat] || cat} · €${rem}M left</h3><button class="pk-x">✕</button></div>
    <input class="pk-search" placeholder="Search players…">
    <div class="pk-list"></div></div>`;
  document.body.appendChild(ov);
  const list = ov.querySelector('.pk-list'), search = ov.querySelector('.pk-search');
  const draw = (q = '') => {
    const ql = q.toLowerCase();
    list.innerHTML = cands.filter(p => !ql || p.player.toLowerCase().includes(ql)).map(p => {
      const owned = pickedIds.has(p.id), tooDear = p.value_m > rem, dis = owned || tooDear;
      return `<div class="pk-row ${dis ? 'dis' : ''}" data-id="${p.id}">
        <span class="pk-pic">${avatarHTML(p.photo, p.player)}</span>
        <span class="pk-info"><div class="nm">${p.player}</div><div class="sub">${p.team} · ${p.position}${owned ? ' · picked' : tooDear ? ' · too expensive' : ''}</div></span>
        <span class="pk-rt" style="color:${ratColor(p.rating)}">${p.rating}</span><span class="pk-vl">€${p.value_m}M</span></div>`;
    }).join('') || '<div class="lb-empty">No players.</div>';
    list.querySelectorAll('.pk-row:not(.dis)').forEach(r => r.onclick = () => {
      picks[slotIdx] = cands.find(p => p.id === +r.dataset.id);
      close(); renderBudget(); renderPitch();
    });
  };
  const close = () => ov.remove();
  ov.querySelector('.pk-x').onclick = close;
  ov.onclick = (e) => { if (e.target === ov) close(); };
  search.oninput = () => draw(search.value.trim());
  draw(); search.focus();
}

async function submit() {
  submitted = true;
  const myTotal = totalRating(), mySpent = spentM();
  const out = document.getElementById('dft-out');
  out.insertAdjacentHTML('afterbegin', '');
  let opt; try { opt = await api(`/api/best_xi?budget=${BUDGET}&formation=${encodeURIComponent(formation)}`); } catch { opt = null; }
  const optTotal = opt && opt.available ? opt.total_rating : null;
  const pct = optTotal ? Math.round(myTotal / optTotal * 100) : null;
  postScore('draft', formation, myTotal);
  const verdict = pct == null ? { t: 'XI locked in', c: 'var(--accent)' }
    : pct >= 98 ? { t: 'Near-perfect squad! 🏆', c: 'var(--green)' }
    : pct >= 92 ? { t: 'Elite drafting', c: 'var(--green)' }
    : pct >= 84 ? { t: 'Solid XI', c: 'var(--gold)' }
    : { t: 'Room to improve', c: 'var(--red)' };
  out.insertAdjacentHTML('afterbegin', `<section class="card" id="dft-result">
    <div class="card-h"><h3>${formation} · €${mySpent}M spent</h3><span class="see">vs the optimal €${BUDGET}M XI</span></div>
    <div class="dft-vs">
      <div class="col"><b>${myTotal}</b><span>Your XI</span></div>
      <div class="col"><b style="color:var(--muted)">${optTotal ?? '—'}</b><span>Optimal XI</span></div>
      ${pct != null ? `<div class="col"><b style="color:${verdict.c}">${pct}%</b><span>of perfect</span></div>` : ''}
    </div>
    <div style="text-align:center;font-weight:800;font-size:18px;color:${verdict.c}">${verdict.t}</div>
    <div style="text-align:center;margin-top:8px"><button class="btn btn-ghost" id="redraft">Draft again</button></div>
  </section>
  <section class="card" id="lbCard" style="margin-bottom:14px"><div class="card-h"><h3>${formation} Leaderboard</h3></div><div class="placeholder-note">Loading…</div></section>`);
  document.getElementById('redraft').onclick = loadPool;
  document.getElementById('dft-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  loadBoard();
}

async function loadBoard() {
  const card = document.getElementById('lbCard'); if (!card) return;
  const rows = await fetchLeaderboard('draft', formation);
  card.innerHTML = `<div class="card-h"><h3>${formation} Leaderboard</h3><span class="see">best squad rating</span></div>
    ${leaderboardHTML(rows, Auth.user && Auth.user.username, 'Rating')}${signInNudge()}`;
}

renderForms();
loadPool();
