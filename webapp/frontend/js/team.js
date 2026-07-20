renderSidebar('Teams');

// [label, key, kind]  kind: 'int' | 'dec' | 'signed' | 'record'
const PERF_TILES = [
  ['🏆', 'Position', 'position', 'rank'], ['◆', 'Points', 'points', 'int'],
  ['⚖', 'Record', 'record', 'record'], ['⚽', 'Goals For', 'goals_for', 'int'],
  ['🥅', 'Goals Against', 'goals_against', 'int'], ['±', 'Goal Diff', 'goal_difference', 'signed'],
  ['◎', 'xG For', 'xg_for', 'dec'], ['⊘', 'xG Against', 'xg_against', 'dec'],
  ['↗', 'Expected Pts', 'xpts', 'dec'],
];
const formPills = (form) => (form || []).map(f => `<span class="form-pill form-${f}">${f}</span>`).join('');

function tileVal(kind, s, key, nTeams) {
  if (kind === 'rank') return `${s.position}<span class="ord">/${nTeams}</span>`;
  if (kind === 'record') return `${s.wins}-${s.draws}-${s.losses}`;
  const v = s[key];
  if (v == null) return '—';
  if (kind === 'signed') return (v > 0 ? '+' : '') + v;
  return v;
}

async function load(name) {
  const d = await api('/api/team?name=' + encodeURIComponent(name));
  if (!d.team) {
    document.getElementById('crumb').textContent = 'not found';
    document.getElementById('tname').textContent = 'Team not found';
    document.querySelector('.tsub').style.display = 'none';   // hide the stray " · " / "—" placeholders
    document.getElementById('trank').style.display = 'none';
    return;
  }
  document.getElementById('crumb').textContent = d.team;
  document.getElementById('tcrest').innerHTML = crestHTML(d.team_logo, 'crest-xl') || '🛡️';
  document.getElementById('tname').textContent = d.team;

  // fan comment thread (mount once per page; keyed by canonical team name)
  if (window.mountComments && !window._cmtsMounted) {
    window._cmtsMounted = true;
    mountComments('team:' + d.team, document.getElementById('comments'),
      { title: 'Fan Comments', subject: d.team });
  }
  const tf = document.getElementById('teamFollow');
  const titem = { id: d.team, name: d.team, league: d.league, crest: d.team_logo };
  const syncT = () => { const on = Store.has('teams', d.team); tf.classList.toggle('on', on); tf.textContent = on ? '✓ Following' : '★ Follow'; };
  tf.onclick = () => { Store.toggle('teams', titem); syncT(); };
  wireFavBtn(document.getElementById('teamFav'), 'favClubs', { name: d.team, crest: d.team_logo });
  syncT();
  document.getElementById('tleague').textContent = d.league;
  document.getElementById('tcountry').textContent = d.country;
  document.getElementById('tform').innerHTML = formPills(d.form);

  const s = d.stats;
  document.getElementById('trank').textContent =
    s ? `${ordinal(s.position)} in ${d.league}` : d.league;

  // manager + venue (use case 7)
  const cap = d.capacity ? ' · ' + d.capacity.toLocaleString() + ' cap' : '';
  document.getElementById('tinfo').innerHTML = [
    d.manager ? `<span class="ti"><span class="k">👔 Manager</span><b>${d.manager}</b></span>` : '',
    d.venue ? `<span class="ti"><span class="k">🏟 Venue</span><b>${d.venue}${d.city ? ', ' + d.city : ''}${cap}</b></span>` : '',
  ].join('');

  document.getElementById('perfTiles').innerHTML = s ? PERF_TILES.map(([ic, lab, key, kind]) =>
    `<div class="tile"><div class="ic">${ic}</div><b>${tileVal(kind, s, key, d.n_teams)}</b><span>${lab}</span></div>`).join('')
    : '<div class="muted">No season stats.</div>';

  document.getElementById('results').innerHTML = (d.results || []).map(r => `
    <div class="res-row res-${r.result}">
      <span class="res-badge">${r.result}</span>
      <span class="res-ven">${r.venue}</span>
      <span class="res-opp">${crestHTML(r.opponent_logo, 'crest-sm')}${r.opponent}</span>
      <span class="res-sc"><b>${r.gf}–${r.ga}</b></span>
      <span class="res-xg muted">xG ${r.xg_for} – ${r.xg_against}</span>
    </div>`).join('') || '<div class="muted">No matches.</div>';

  document.getElementById('scorers').innerHTML = (d.top_scorers || []).map((p, i) => `
    <div class="prow" onclick="location.href='${pHref(p.player)}'" style="cursor:pointer">
      <span class="rk">${i + 1}</span>
      <span class="pic">${avatarHTML(null, p.player)}</span>
      <span style="flex:1"><div class="nm">${p.player}</div></span>
      <span class="end"><b>${p.goals}</b> G · ${p.assists} A</span></div>`).join('') || '<div class="muted">—</div>';

  renderSquad(d.squad || []);
}

const POS_GROUPS = [['GK', 'Goalkeepers'], ['DEF', 'Defenders'], ['MID', 'Midfielders'], ['FWD', 'Forwards']];
function renderSquad(squad) {
  document.getElementById('squadCount').textContent = squad.length ? squad.length + ' players' : '';
  const byGroup = (g) => squad.filter(p => p.position_group === g);
  const card = (p) => `
    <div class="scard" onclick="location.href='${pHref(p.player)}'">
      <span class="pic">${avatarHTML(p.photo, p.player)}</span>
      <div class="sx"><div class="nm">${p.player}</div>
        <div class="sub">${p.age ? p.age + 'y · ' : ''}${p.apps ?? 0} apps · ${p.minutes ?? 0}'</div></div>
      <div class="sga"><b>${p.goals ?? 0}</b>G <b>${p.assists ?? 0}</b>A</div>
    </div>`;
  document.getElementById('squad').innerHTML = POS_GROUPS.map(([g, label]) => {
    const ps = byGroup(g);
    return ps.length ? `<div class="sgroup"><div class="sglabel">${label} <span>${ps.length}</span></div>
      <div class="sgrid">${ps.map(card).join('')}</div></div>` : '';
  }).join('') || '<div class="muted">No squad data.</div>';
}

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

const params = new URLSearchParams(location.search);
load(params.get('name') || 'Arsenal');
// topbar search = the global player/team/match dropdown (same as Home)
attachSearchDropdown(document.getElementById('searchBox'));
