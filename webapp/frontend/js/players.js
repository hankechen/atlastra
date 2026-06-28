renderSidebar('Players');

const GROUPS = [['all', 'All'], ['FWD', 'Forwards'], ['MID', 'Midfield'], ['DEF', 'Defenders'], ['GK', 'Goalkeepers']];
const SCOPES = [['league', 'Top 5 Leagues'], ['ucl', 'Champions League'], ['former', 'Former Players']];
let group = 'all', search = '', scope = 'league';
const _sp = new URLSearchParams(location.search).get('scope');   // ?scope=former deep-link
if (SCOPES.some(([s]) => s === _sp)) scope = _sp;

// Top-5-leagues / UCL / Former toggle — switches which players + which rating show
const scopeEl = document.getElementById('dirScope');
scopeEl.innerHTML = SCOPES.map(([s, n]) =>
  `<button class="ds-btn ${s === scope ? 'active' : ''}" data-s="${s}">${n}</button>`).join('');
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

// classification -> colour tier (gold/violet/blue/green/slate/bronze). Check
// "above"/"below" before "average" since both contain it.
function clsTier(c) {
  c = (c || '').toLowerCase();
  if (!c) return '';
  if (c.includes('best in')) return 'bip';
  if (c.includes('world')) return 'wc';
  if (c.includes('elite')) return 'elite';
  if (c.includes('above')) return 'aa';
  if (c.includes('below')) return 'ba';
  if (c.includes('average')) return 'avg';
  return '';
}
const tierClass = (p) => { const t = clsTier(p.classification); return t ? ' t-' + t : ''; };

const card = (p) => `
  <div class="pcard${tierClass(p)}" onclick="location.href='/player.html?name=${encodeURIComponent(p.player)}'">
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

// Former players: peak former season + advanced stats; click deep-links to that
// season on the profile (the season selector reads ?season=).
const formerCard = (p) => `
  <div class="pcard${tierClass(p)}" onclick="location.href='/player.html?name=${encodeURIComponent(p.player)}&season=${p.season_code}'">
    <div class="top">
      <div class="photo">${avatarHTML(p.photo, p.player)}</div>
      <div class="rt">${p.rating ?? '—'}</div>
    </div>
    <div class="nm">${p.player}</div>
    <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · ${p.position}</div>
    <span class="cls">${p.classification ?? ''} · ${p.season}</span>
    <div class="rsplit">League <b>${p.rating_league ?? '—'}</b>${p.rating_ucl != null ? ` · UCL <b>${p.rating_ucl}</b>` : ''} <span class="cmb">→ combined ${p.rating ?? '—'}</span></div>
    <div class="row r4">
      <div class="x"><span>Goals</span><b>${p.goals ?? '—'}</b></div>
      <div class="x"><span>Assists</span><b>${p.assists ?? '—'}</b></div>
      <div class="x"><span>xG</span><b>${p.xg ?? '—'}</b></div>
      <div class="x"><span>xA</span><b>${p.xa ?? '—'}</b></div>
    </div>
  </div>`;

let timer;
const NOTES = {
  ucl: 'Champions League players ranked by their UCL rating, with their UCL goals & assists. Click any card for the full profile.',
  former: 'Notable players who have left the big five leagues (transferred abroad or retired), ranked by their best season here — the headline number is the combined League + UCL rating (minutes-weighted), shown with that season and its advanced stats. Players who did not feature in the Champions League that season have a league-only rating and are listed after the UCL players. Click a card to open that season on their profile.',
  league: "Top-rated players across Europe's big five leagues. Click any card for the full profile.",
};
async function render() {
  document.getElementById('dirNote').textContent = NOTES[scope] || NOTES.league;
  const qs = `group=${group}&scope=${scope}&limit=30` + (search ? `&search=${encodeURIComponent(search)}` : '');
  const players = await api('/api/players?' + qs);
  const draw = scope === 'former' ? formerCard : card;
  document.getElementById('grid').innerHTML =
    players.length ? players.map(draw).join('') : `<div class="empty">No players found.</div>`;
}

// topbar search = the global player/team/match dropdown (same as Home), not a
// directory filter, so the search behaves consistently across the app.
attachSearchDropdown(document.getElementById('searchBox'));

render();
