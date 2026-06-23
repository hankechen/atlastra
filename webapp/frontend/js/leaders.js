renderSidebar('Stat Leaders');
attachSearchDropdown(document.getElementById('searchBox'));

let SEASONS = [], leagueList = [], curSeason = '', curScope = 'all';
const seasonLabel = () => (SEASONS.find(s => s.value === curSeason) || {}).label || '';
const ph = (p) => `/player.html?name=${encodeURIComponent(p.player)}`;

function card(b) {
  const lead = b.top[0];
  const rest = b.top.slice(1).map((p, i) => `<a class="ll-row" href="${ph(p)}">
      <span class="ll-rk">${i + 2}</span><span class="ll-rn">${p.player}</span><b>${p.value}</b></a>`).join('');
  return `<div class="ll-card">
    <div class="ll-h">${b.label}</div>
    <a class="ll-lead" href="${ph(lead)}">
      <span class="ll-ph">${avatarHTML(lead.photo, lead.player)}</span>
      <span class="ll-nm"><b>${lead.player}</b><small>${lead.team}</small></span>
      <span class="ll-v">${lead.value}</span></a>
    ${rest ? `<div class="ll-rest">${rest}</div>` : ''}</div>`;
}

function renderScope() {
  const tabs = [['all', 'Top 5 Leagues'], ...leagueList.map(l => [l.key, l.name])];
  const el = document.getElementById('scopeTabs');
  el.innerHTML = tabs.map(([k, n]) =>
    `<a class="${k === curScope ? 'active' : ''}" data-k="${k}" style="cursor:pointer">${n}</a>`).join('');
  el.querySelectorAll('a').forEach(a => a.onclick = () => { curScope = a.dataset.k; renderScope(); load(); });
}

async function load() {
  const out = document.getElementById('ld-out');
  out.innerHTML = '<section class="card"><div class="placeholder-note">Loading…</div></section>';
  let d;
  try { d = await api(`/api/league_leaders?league=${encodeURIComponent(curScope)}&season=${encodeURIComponent(curSeason)}`); }
  catch { out.innerHTML = '<section class="card"><div class="placeholder-note">Could not load leaders.</div></section>'; return; }
  if (!d.available || !d.leaders.length) { out.innerHTML = '<div class="empty">No leader data for this scope.</div>'; return; }
  out.innerHTML = `<section class="card ll-sec">
      <div class="card-h"><h3>${d.league} — Stat Leaders</h3>
        <span class="see">top 3 per stat · ${seasonLabel()}</span></div>
      <div class="ll-grid">${d.leaders.map(card).join('')}</div></section>`;
}

(async () => {
  const [seasons, leagues] = await Promise.all([api('/api/seasons'), api('/api/leagues')]);
  SEASONS = seasons; leagueList = leagues;
  curSeason = (SEASONS[0] || {}).value || '';
  const p = new URLSearchParams(location.search);
  if (p.get('league')) curScope = p.get('league');
  if (p.get('season')) curSeason = p.get('season');
  const sel = document.getElementById('ldSeason');
  sel.innerHTML = SEASONS.map(s =>
    `<option value="${s.value}"${s.value === curSeason ? ' selected' : ''}>${s.label}</option>`).join('');
  sel.onchange = () => { curSeason = sel.value; load(); };
  renderScope();
  load();
})();
