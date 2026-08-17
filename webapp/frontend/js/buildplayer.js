renderSidebar('buildplayer');
attachSearchDropdown(document.getElementById('searchBox'));

// ---- state ----
let CFG = null;             // /api/build_player/config response
let position = null;        // e.g. 'ST'
let minTier = null;         // e.g. 'starter'
let attrs = {};             // {column: 1-99}
let lastResult = null;
let bestSeen = 0;           // highest rating this session, for the leaderboard post

const ratColor = (r) => r >= 85 ? '#39d07f' : r >= 78 ? '#a6d14a' : r >= 70 ? '#e7c14a' : r >= 55 ? '#e79a4a' : '#e5484d';
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

function posOf(key) { return (CFG.positions || []).find((p) => p.key === key); }
function budgetSpent() { const p = posOf(position); return (p ? p.sliders : []).reduce((s, c) => s + (attrs[c.column] ?? CFG.attr_default), 0); }

// ---- step 1: position ----
function renderPosGrid() {
  document.getElementById('posGrid').innerHTML = CFG.positions.map((p) => `
    <div class="bp-pos ${p.key === position ? 'active' : ''}" data-k="${p.key}">
      <b>${p.key}</b><span>${p.label}</span></div>`).join('');
  document.querySelectorAll('.bp-pos').forEach((el) => el.onclick = () => selectPosition(el.dataset.k));
}
function selectPosition(key) {
  if (key === position) return;
  position = key; attrs = {};
  const p = posOf(key);
  p.sliders.forEach((s) => { attrs[s.column] = CFG.attr_default; });
  renderPosGrid();
  document.getElementById('minCard').style.display = '';
  if (!minTier) selectMinutes(CFG.minute_tiers[1].key);   // default to Regular Starter
  else renderSliders();
  document.getElementById('buildArea').style.display = '';
  document.getElementById('resPos').textContent = p.label;
  document.getElementById('lbCard').style.display = '';
  loadBoard();
}

// ---- step 2: minutes ----
function renderMinGrid() {
  document.getElementById('minGrid').innerHTML = CFG.minute_tiers.map((t) => `
    <button class="pill-btn ${t.key === minTier ? 'active' : ''}" data-k="${t.key}">${t.label} · ${t.minutes}'</button>`).join('');
  document.querySelectorAll('#minGrid .pill-btn').forEach((b) => b.onclick = () => selectMinutes(b.dataset.k));
  const t = CFG.minute_tiers.find((x) => x.key === minTier);
  if (t) {
    const lam = Math.round(t.minutes / (t.minutes + 600) * 100);
    document.getElementById('shrinkNote').textContent =
      `The engine trusts a smaller sample less: at ${t.minutes}' the rating is shrunk toward average by ${100 - lam}% — the same rule every real player's rating obeys.`;
  }
}
function selectMinutes(key) {
  minTier = key;
  renderMinGrid();
  if (position) renderSliders();
}

// ---- step 3: sliders ----
function sliderRow(s) {
  const v = attrs[s.column] ?? CFG.attr_default;
  const anchor = (x) => x == null ? '—' : (x + (s.unit.trim() === '%' ? '%' : s.unit));
  return `<div class="bp-slider" data-col="${s.column}">
    <div class="bp-slabel"><span>${esc(s.label)}</span><b>${v}</b></div>
    <input type="range" min="${CFG.attr_min}" max="${CFG.attr_max}" value="${v}" data-col="${s.column}">
    <div class="bp-shint"><span>${s.invert ? 'worse ← ' : ''}${anchor(s.p10)}${s.invert ? '' : ' ← weak'}</span>
      <span>median ${anchor(s.p50)}</span>
      <span>${s.invert ? 'best → ' : ''}${anchor(s.p90)}${s.invert ? '' : ' → elite'}</span></div>
  </div>`;
}
function renderSliders() {
  const p = posOf(position);
  document.getElementById('sliderSub').textContent = `${p.n_sliders} attributes · vs ${p.field_size} real ${p.label.toLowerCase()}s this season`;
  document.getElementById('sliderList').innerHTML = p.sliders.map(sliderRow).join('');
  document.querySelectorAll('#sliderList input[type=range]').forEach((inp) => {
    inp.oninput = () => {
      attrs[inp.dataset.col] = +inp.value;
      inp.closest('.bp-slider').querySelector('.bp-slabel b').textContent = inp.value;
      renderBudget();
      scheduleRate();
    };
  });
  renderBudget();
  scheduleRate();
}
function renderBudget() {
  const p = posOf(position), spent = budgetSpent(), budget = p.budget, over = spent > budget;
  document.getElementById('budgetNum').innerHTML = `${spent} <span class="muted" style="font-weight:600">/ ${budget}</span>`;
  document.getElementById('budgetNum').className = over ? 'over' : '';
  const bar = document.getElementById('budgetBar');
  bar.className = 'bp-bbar' + (over ? ' over' : '');
  bar.firstElementChild.style.width = Math.min(100, spent / budget * 100) + '%';
}

// ---- live rating ----
let _t;
function scheduleRate() { clearTimeout(_t); _t = setTimeout(runRate, 220); }
async function runRate() {
  if (!position || !minTier) return;
  const t = CFG.minute_tiers.find((x) => x.key === minTier);
  const body = document.getElementById('resultBody');
  body.classList.add('bp-loading');
  let r;
  try {
    r = await apiPost('/api/build_player/rate', { position, minutes: t.minutes, attrs });
  } catch { r = null; }
  body.classList.remove('bp-loading');
  if (!r || !r.available) { body.innerHTML = '<div class="empty-state">Could not score that build.</div>'; return; }
  lastResult = r;
  renderResult(r);
  renderComparables(r);
  if (r.rating > bestSeen) { bestSeen = r.rating; postScore('buildplayer', position, bestSeen); }
}

function renderResult(r) {
  const over = r.spent > r.budget;
  const drivers = r.contributions.slice(0, 6).map((c) => {
    const pos = c.contribution >= 0;
    const mag = Math.min(100, Math.abs(c.contribution) / 0.6 * 100);
    return `<div class="bp-drv"><span class="l">${esc(c.label)}</span>
      <span class="bar"><i class="${pos ? 'good' : 'bad'}" style="width:${mag}%"></i></span>
      <b class="${pos ? 'good' : 'bad'}">${pos ? '+' : ''}${c.contribution.toFixed(2)}</b></div>`;
  }).join('');
  document.getElementById('resultBody').innerHTML = `
    <div class="bp-result">
      <div class="bp-rnum" style="-webkit-text-fill-color:${ratColor(r.rating)};background:none">${r.rating}</div>
      <div class="bp-rmeta">
        <span class="fin-badge ${r.rating >= 80 ? 'great' : r.rating >= 65 ? 'good' : r.rating >= 50 ? 'neutral' : 'bad'}">${esc(r.classification)}</span>
        <span class="bp-rank">${ordinal(Math.round(r.percentile))} percentile · ranks #${r.rank_in_group} of ${r.n_in_group} real ${posOf(position).label.toLowerCase()}s</span>
        <span class="bp-shrink">Shrinkage: <b>${Math.round(r.shrinkage * 100)}%</b> of the composite is trusted at ${r.minutes}'</span>
      </div>
    </div>
    ${over ? `<div class="muted" style="font-size:11.5px;margin-bottom:10px;color:var(--red)">Over budget by ${r.spent - r.budget} — the rating above is what these numbers WOULD score, but a legal build has to fit ${r.budget}.</div>` : ''}
    <h5 class="val-dh">What's driving the rating</h5>
    <div class="val-drivers">${drivers}</div>`;
}

const ordinal = (n) => { const v = n % 100, s = ['th', 'st', 'nd', 'rd']; return n + (s[(v - 20) % 10] || s[v] || s[0]); };

function renderComparables(r) {
  const card = document.getElementById('compCard');
  if (!r.comparables || !r.comparables.length) { card.style.display = 'none'; return; }
  card.style.display = '';
  const rows = r.comparables.map((c) => `
    <div class="bp-comp"><a href="/player.html?name=${encodeURIComponent(c.player)}" target="_blank" rel="noopener">${esc(c.player)}</a>
      <span class="muted">#${c.rank_in_group} · ${esc(c.team)}</span>
      <b style="color:${ratColor(c.rating)}">${c.rating}</b></div>`).join('');
  const mine = `<div class="bp-comp me"><span><b>Your Creation</b></span><span class="muted">#${r.rank_in_group}</span>
    <b style="color:${ratColor(r.rating)}">${r.rating}</b></div>`;
  // splice "me" in by rank so the list reads top-to-bottom
  const above = r.comparables.filter((c) => c.rank_in_group < r.rank_in_group);
  const below = r.comparables.filter((c) => c.rank_in_group > r.rank_in_group);
  const row = (c) => `<div class="bp-comp"><a href="/player.html?name=${encodeURIComponent(c.player)}" target="_blank" rel="noopener">${esc(c.player)}</a>
      <span class="muted">#${c.rank_in_group} · ${esc(c.team)}</span>
      <b style="color:${ratColor(c.rating)}">${c.rating}</b></div>`;
  document.getElementById('compList').innerHTML = above.map(row).join('') + mine + below.map(row).join('');
}

// ---- leaderboard (per position: 'buildplayer' game, period = position key) ----
async function loadBoard() {
  const card = document.getElementById('lbCard');
  const rows = await fetchLeaderboard('buildplayer', position);
  card.innerHTML = `<div class="card-h"><h3>Highest-Rated Creations</h3><span class="see">${posOf(position).label} · global leaderboard</span></div>
    ${leaderboardHTML(rows, Auth.user && Auth.user.username, 'Rating')}${signInNudge()}`;
}

// ---- boot ----
(async function boot() {
  try { CFG = await api('/api/build_player/config'); }
  catch { CFG = null; }
  if (!CFG || !CFG.available) {
    document.getElementById('posGrid').innerHTML = '<div class="empty-state">Could not load positions.</div>';
    return;
  }
  renderPosGrid();
  renderMinGrid();
})();
