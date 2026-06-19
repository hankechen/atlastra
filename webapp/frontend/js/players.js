renderSidebar('Players');

const GROUPS = [['all', 'All'], ['FWD', 'Forwards'], ['MID', 'Midfield'], ['DEF', 'Defenders'], ['GK', 'Goalkeepers']];
const SCOPES = [['league', 'Top 5 Leagues'], ['ucl', 'Champions League']];
let group = 'all', search = '', scope = 'league';

// Top-5-leagues vs UCL toggle — switches which players + which rating show
const scopeEl = document.getElementById('dirScope');
scopeEl.innerHTML = SCOPES.map(([s, n], i) =>
  `<button class="ds-btn ${i ? '' : 'active'}" data-s="${s}">${n}</button>`).join('');
scopeEl.querySelectorAll('.ds-btn').forEach(b => b.onclick = () => {
  scopeEl.querySelectorAll('.ds-btn').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); scope = b.dataset.s; render();
});

const filtersEl = document.getElementById('filters');
filtersEl.innerHTML = GROUPS.map(([g, n], i) =>
  `<span class="pill ${i ? '' : 'active'}" data-g="${g}">${n}</span>`).join('');
filtersEl.querySelectorAll('.pill').forEach(p => p.onclick = () => {
  filtersEl.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
  p.classList.add('active'); group = p.dataset.g; render();
});

const card = (p) => `
  <div class="pcard" onclick="location.href='/player.html?name=${encodeURIComponent(p.player)}'">
    <div class="top">
      <div class="photo">${avatarHTML(p.photo, p.player)}</div>
      <div class="rt">${p.rating ?? '—'}</div>
    </div>
    <div class="nm">${p.player}</div>
    <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · ${p.position}</div>
    <span class="cls">${p.classification ?? ''}</span>
    <div class="row">
      <div class="x"><span>Goals</span><b>${p.goals ?? '—'}</b></div>
      <div class="x"><span>Assists</span><b>${p.assists ?? '—'}</b></div>
      <div class="x"><span>Value</span><b>${eurM(p.market_value_eur)}</b></div>
    </div>
  </div>`;

let timer;
async function render() {
  document.getElementById('dirNote').textContent = scope === 'ucl'
    ? 'Champions League players ranked by their UCL rating, with their UCL goals & assists. Click any card for the full profile.'
    : "Top-rated players across Europe's big five leagues. Click any card for the full profile.";
  const qs = `group=${group}&scope=${scope}&limit=30` + (search ? `&search=${encodeURIComponent(search)}` : '');
  const players = await api('/api/players?' + qs);
  document.getElementById('grid').innerHTML =
    players.length ? players.map(card).join('') : `<div class="empty">No players found.</div>`;
}

document.getElementById('searchBox').addEventListener('input', (e) => {
  search = e.target.value.trim();
  clearTimeout(timer); timer = setTimeout(render, 200);
});

render();
