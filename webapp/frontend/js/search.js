renderSidebar('Search');

const playerHref = (n) => '/player.html?name=' + encodeURIComponent(n);
const teamHref = (n) => '/team.html?name=' + encodeURIComponent(n);

// ---- unified player / team search ----
let timer;
async function runSearch(q) {
  const pEl = document.getElementById('players'), tEl = document.getElementById('teams');
  if (!q) {
    pEl.innerHTML = tEl.innerHTML = '<div class="empty">Start typing above…</div>';
    document.getElementById('pcount').textContent = document.getElementById('tcount').textContent = '';
    return;
  }
  const d = await api('/api/search?q=' + encodeURIComponent(q));
  document.getElementById('pcount').textContent = d.players.length ? d.players.length + ' found' : '';
  document.getElementById('tcount').textContent = d.teams.length ? d.teams.length + ' found' : '';

  pEl.innerHTML = d.players.length ? d.players.map(p => `
    <div class="sr" onclick="location.href='${playerHref(p.player)}'">
      <span class="pic">${avatarHTML(p.photo, p.player)}</span>
      <span class="srx"><div class="nm">${p.player}${p.former ? ` <span class="former-tag">former · ${p.season}</span>` : ''}</div>
        <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team || ''} · ${p.position || ''}</div></span>
      <span class="srend">${p.goals ?? 0}G · ${p.assists ?? 0}A</span>
    </div>`).join('') : '<div class="empty">No players.</div>';

  tEl.innerHTML = d.teams.length ? d.teams.map(t => `
    <div class="sr" onclick="location.href='${teamHref(t.team)}'">
      <span class="pic crest-pic">${crestHTML(t.team_logo, 'crest-md') || '🛡️'}</span>
      <span class="srx"><div class="nm">${t.team}</div>
        <div class="sub">${t.league} · ${t.country}</div></span>
      <span class="srend">›</span>
    </div>`).join('') : '<div class="empty">No teams.</div>';
}

const box = document.getElementById('searchBox');
box.addEventListener('input', (e) => {
  const q = e.target.value.trim();
  clearTimeout(timer); timer = setTimeout(() => runSearch(q), 180);
});

// ---- head-to-head ----
async function runH2H() {
  const a = document.getElementById('teamA').value.trim();
  const b = document.getElementById('teamB').value.trim();
  const out = document.getElementById('h2h');
  if (!a || !b) { out.innerHTML = '<div class="empty">Enter two teams.</div>'; return; }
  const d = await api('/api/match_search?a=' + encodeURIComponent(a) + '&b=' + encodeURIComponent(b));
  if (!d.team_a || !d.team_b) {
    out.innerHTML = `<div class="empty">Couldn’t find ${!d.team_a ? a : b}.</div>`; return;
  }
  // record from team_a's perspective, across all returned meetings
  const A = d.team_a.name;
  let w = 0, dr = 0, l = 0, gf = 0, ga = 0;
  d.matches.forEach(m => {
    if (m.home_goals == null || m.away_goals == null) return;
    const aHome = m.home === A, af = aHome ? m.home_goals : m.away_goals, bf = aHome ? m.away_goals : m.home_goals;
    gf += af; ga += bf;
    if (af > bf) w++; else if (af < bf) l++; else dr++;
  });
  const summary = d.matches.length ? `<div class="h2h-sum">
    <b>${A}</b>: ${w}W · ${dr}D · ${l}L <span class="muted">in ${d.matches.length} meetings (${gf}–${ga} goals)</span></div>` : '';
  const head = `<div class="h2h-head">
    <a class="h2h-team" href="${teamHref(d.team_a.name)}">${crestHTML(d.team_a.logo, 'crest-md')}${d.team_a.name}</a>
    <span class="vs">vs</span>
    <a class="h2h-team" href="${teamHref(d.team_b.name)}">${crestHTML(d.team_b.logo, 'crest-md')}${d.team_b.name}</a></div>`;
  const rows = d.matches.length ? d.matches.map(m => `
    <div class="mrow">
      <span class="mdate">${m.date.slice(0, 10)}<small class="muted"> · ${m.season}</small></span>
      <span class="mteam home">${m.home}${crestHTML(m.home_logo, 'crest-sm')}</span>
      <span class="mscore"><b>${m.home_goals}–${m.away_goals}</b></span>
      <span class="mteam away">${crestHTML(m.away_logo, 'crest-sm')}${m.away}</span>
      <span class="mxg muted">xG ${m.home_xg} – ${m.away_xg}</span>
    </div>`).join('') : '<div class="empty">No fixtures between these teams in the last 12 seasons.</div>';
  out.innerHTML = head + summary + rows;
}
document.getElementById('h2hBtn').onclick = runH2H;
['teamA', 'teamB'].forEach(id => document.getElementById(id)
  .addEventListener('keydown', (e) => { if (e.key === 'Enter') runH2H(); }));

// populate the team datalist for autocomplete (all 5 leagues' tables)
(async () => {
  const leagues = await api('/api/leagues');
  const names = new Set();
  for (const l of leagues) {
    const tbl = await api('/api/league_table?league=' + encodeURIComponent(l.key));
    tbl.forEach(t => names.add(t.team));
  }
  document.getElementById('teamlist').innerHTML =
    [...names].sort().map(n => `<option value="${n}">`).join('');
})();

// deep-link support: /search.html?q=...
const params = new URLSearchParams(location.search);
if (params.get('q')) { box.value = params.get('q'); runSearch(params.get('q')); }
