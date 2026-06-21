renderSidebar('Rankings & Awards');
attachSearchDropdown(document.getElementById('searchBox'));

const MODES = [['position', 'By Position'], ['season', 'Best Seasons (All-Time)']];
const SEASON_SCOPES = [['combined', 'Combined'], ['league', 'League'], ['ucl', 'Champions League']];
let mode = 'position';
let posGroups = [], posActive = null;            // position mode
let seasonScope = 'combined';                    // season mode
const seasonCache = {};                          // scope -> player-season list

function renderModeTabs() {
  document.getElementById('modeTabs').innerHTML = MODES.map(([k, l]) =>
    `<button class="ds-btn ${k === mode ? 'active' : ''}" data-k="${k}">${l}</button>`).join('');
  document.querySelectorAll('#modeTabs .ds-btn').forEach(b =>
    b.onclick = () => { mode = b.dataset.k; renderModeTabs(); renderSubTabs(); render(); });
}

function renderSubTabs() {
  const el = document.getElementById('posTabs');
  const pills = mode === 'position'
    ? posGroups.map(g => [g.key, g.label, g.key === posActive])
    : SEASON_SCOPES.map(([k, l]) => [k, l, k === seasonScope]);
  el.innerHTML = pills.map(([k, l, on]) => `<span class="pill ${on ? 'active' : ''}" data-k="${k}">${l}</span>`).join('');
  el.querySelectorAll('.pill').forEach(p => p.onclick = () => {
    if (mode === 'position') posActive = p.dataset.k; else seasonScope = p.dataset.k;
    renderSubTabs(); render();
  });
}

// one ranked player-season row (links to that exact season on the profile)
function seasonRow(p) {
  return `<div class="prow" onclick="location.href='/player.html?name=${encodeURIComponent(p.player)}&season=${p.season_code}'">
    <span class="rk">${p.rank}</span>
    <span class="pic" title="${p.player}">${avatarHTML(p.photo, p.player)}</span>
    <span><div class="nm">${p.player}</div>
      <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · <b>${p.season}</b> · ${p.position}</div></span>
    <span class="end"><span class="ratingchip">${p.rating}</span></span></div>`;
}

async function render() {
  const el = document.getElementById('rankList');
  if (mode === 'position') {
    const g = posGroups.find(x => x.key === posActive);
    if (!g || !g.players.length) { el.innerHTML = '<div class="empty">No players.</div>'; return; }
    el.innerHTML = `<section class="card">
      <div class="card-h"><h3>${g.label} — Top ${g.players.length}</h3><span class="see">by combined League rating · this season</span></div>
      <div class="rank-grid">${g.players.map(p => playerRow(p, { chip: true })).join('')}</div></section>`;
  } else {
    let list = seasonCache[seasonScope];
    if (!list) {
      el.innerHTML = '<section class="card"><div class="placeholder-note">Loading…</div></section>';
      list = seasonCache[seasonScope] = await api('/api/alltime_seasons?scope=' + seasonScope + '&limit=20');
    }
    const lbl = SEASON_SCOPES.find(([k]) => k === seasonScope)[1];
    el.innerHTML = `<section class="card">
      <div class="card-h"><h3>Best ${lbl} Seasons — All-Time Top ${list.length}</h3><span class="see">by Atlastra rating · since 2014/15</span></div>
      <div class="rank-grid">${list.map(seasonRow).join('')}</div></section>`;
  }
}

(async () => {
  const sp = new URLSearchParams(location.search);     // ?mode=season&scope=ucl deep-links
  if (MODES.some(([k]) => k === sp.get('mode'))) mode = sp.get('mode');
  if (SEASON_SCOPES.some(([k]) => k === sp.get('scope'))) seasonScope = sp.get('scope');
  const d = await api('/api/position_rankings?limit=20');
  posGroups = d.groups || [];
  posActive = posGroups.length ? posGroups[0].key : null;
  renderModeTabs();
  renderSubTabs();
  render();
})();
