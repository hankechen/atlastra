renderSidebar('Rankings & Awards');
attachSearchDropdown(document.getElementById('searchBox'));

const MODES = [['position', 'By Position'], ['season', 'Best Seasons (All-Time)']];
const POS_SCOPES = [['league', 'League'], ['ucl', 'Champions League']];
const SEASON_SCOPES = [['combined', 'Combined'], ['league', 'League'], ['ucl', 'Champions League']];
let mode = 'position', posScope = 'league', posActive = null, seasonScope = 'combined';
const posCache = {}, seasonCache = {};       // posCache keyed by scope; seasonCache by scope

function renderModeTabs() {
  document.getElementById('modeTabs').innerHTML = MODES.map(([k, l]) =>
    `<button class="ds-btn ${k === mode ? 'active' : ''}" data-k="${k}">${l}</button>`).join('');
  document.querySelectorAll('#modeTabs .ds-btn').forEach(b => b.onclick = () => { mode = b.dataset.k; refresh(); });
}

function renderScope() {   // League/UCL toggle — only in "By Position" mode
  const el = document.getElementById('posScope');
  if (mode !== 'position') { el.innerHTML = ''; return; }
  el.innerHTML = POS_SCOPES.map(([k, l]) =>
    `<button class="ds-btn ${k === posScope ? 'active' : ''}" data-k="${k}">${l}</button>`).join('');
  el.querySelectorAll('.ds-btn').forEach(b => b.onclick = () => { posScope = b.dataset.k; posActive = null; refresh(); });
}

function renderSubTabs() {
  const el = document.getElementById('posTabs');
  if (mode === 'position') {
    const gs = posCache[posScope] || [];
    el.innerHTML = gs.map(g => `<span class="pill ${g.key === posActive ? 'active' : ''}" data-k="${g.key}">${g.label}</span>`).join('');
    el.querySelectorAll('.pill').forEach(p => p.onclick = () => { posActive = p.dataset.k; renderSubTabs(); drawList(); });
  } else {
    el.innerHTML = SEASON_SCOPES.map(([k, l]) => `<span class="pill ${k === seasonScope ? 'active' : ''}" data-k="${k}">${l}</span>`).join('');
    // each season scope is a separate fetch -> refresh() so the new scope loads
    el.querySelectorAll('.pill').forEach(p => p.onclick = () => { seasonScope = p.dataset.k; refresh(); });
  }
}

function seasonRow(p) {
  return `<div class="prow" onclick="location.href='/player.html?name=${encodeURIComponent(p.player)}&season=${p.season_code}'">
    <span class="rk">${p.rank}</span>
    <span class="pic" title="${p.player}">${avatarHTML(p.photo, p.player)}</span>
    <span><div class="nm">${p.player}</div>
      <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · <b>${p.season}</b> · ${p.position}</div></span>
    <span class="end"><span class="ratingchip">${p.rating}</span></span></div>`;
}

function drawList() {     // render the list for the current selection (data already cached)
  const el = document.getElementById('rankList');
  if (mode === 'position') {
    const g = (posCache[posScope] || []).find(x => x.key === posActive);
    if (!g || !g.players.length) { el.innerHTML = '<div class="empty">No players.</div>'; return; }
    const scopeLbl = POS_SCOPES.find(([k]) => k === posScope)[1];
    el.innerHTML = `<section class="card">
      <div class="card-h"><h3>${g.label} — Top ${g.players.length}</h3><span class="see">${scopeLbl} rating · this season</span></div>
      <div class="rank-grid">${g.players.map(p => playerRow(p, { chip: true })).join('')}</div></section>`;
  } else {
    const list = seasonCache[seasonScope] || [];
    const lbl = SEASON_SCOPES.find(([k]) => k === seasonScope)[1];
    el.innerHTML = `<section class="card">
      <div class="card-h"><h3>Best ${lbl} Seasons — All-Time Top ${list.length}</h3><span class="see">by Atlastra rating · since 2014/15</span></div>
      <div class="rank-grid">${list.map(seasonRow).join('')}</div></section>`;
  }
}

async function refresh() {
  renderModeTabs();
  renderScope();
  const el = document.getElementById('rankList');
  if (mode === 'position') {
    if (!posCache[posScope]) {
      el.innerHTML = '<section class="card"><div class="placeholder-note">Loading…</div></section>';
      posCache[posScope] = (await api('/api/position_rankings?limit=20&scope=' + posScope)).groups || [];
    }
    const gs = posCache[posScope];
    if (!posActive || !gs.some(g => g.key === posActive)) posActive = gs.length ? gs[0].key : null;
  } else if (!seasonCache[seasonScope]) {
    el.innerHTML = '<section class="card"><div class="placeholder-note">Loading…</div></section>';
    seasonCache[seasonScope] = await api('/api/alltime_seasons?scope=' + seasonScope + '&limit=20');
  }
  renderSubTabs();
  drawList();
}

(() => {
  const sp = new URLSearchParams(location.search);
  if (MODES.some(([k]) => k === sp.get('mode'))) mode = sp.get('mode');
  const s = sp.get('scope');
  if (mode === 'season' && SEASON_SCOPES.some(([k]) => k === s)) seasonScope = s;
  if (mode === 'position' && POS_SCOPES.some(([k]) => k === s)) posScope = s;
  refresh();
})();
