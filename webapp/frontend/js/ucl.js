renderSidebar('Champions League');

let SEASONS = [], curSeason = '', curSub = 'bracket';
const _compCache = {};   // season -> /api/ucl bundle (shared by bracket + results)
const seasonLabel = () => (SEASONS.find(s => s.value === curSeason) || {}).label || 'this season';
const _content = () => document.getElementById('uclContent');
const playerHref = (name) => '/player.html?name=' + encodeURIComponent(name);
const teamHref = (name) => '/team.html?name=' + encodeURIComponent(name);

async function comp() {
  if (!_compCache[curSeason])
    _compCache[curSeason] = await api('/api/ucl?season=' + encodeURIComponent(curSeason));
  return _compCache[curSeason];
}

// one match row (crest – score – crest), clickable through to the match page
function matchRow(m) {
  const score = m.home_goals != null ? `${m.home_goals}<span class="fx-dash">-</span>${m.away_goals}` : '<span class="muted">vs</span>';
  const click = m.event_id ? ` onclick="location.href='/match.html?id=${m.event_id}'" style="cursor:pointer"` : '';
  return `<div class="fx-row"${click}>
    <a class="fx-t home" href="${teamHref(m.home)}" onclick="event.stopPropagation()"><span class="tn">${m.home}</span>${crestHTML(m.home_logo, 'crest-sm')}</a>
    <span class="fx-sc">${score}</span>
    <a class="fx-t away" href="${teamHref(m.away)}" onclick="event.stopPropagation()">${crestHTML(m.away_logo, 'crest-sm')}<span class="tn">${m.away}</span></a></div>`;
}

// ---- sub-tab: Bracket (knockout tree, Round of 16 -> Final) ----
function bktSide(t, k) {
  const name = t[k], agg = t[k + '_agg'];
  if (!name) return `<div class="bkt-row tbd"><span class="bkt-tm">TBD</span><b></b></div>`;
  const cls = t.winner === k ? 'win' : t.winner ? 'lose' : '';
  return `<div class="bkt-row ${cls}">
    <a class="bkt-tm" href="${teamHref(name)}" onclick="event.stopPropagation()">${crestHTML(t[k + '_logo'], 'crest-sm')}<span class="tn">${name}</span></a>
    <b>${agg == null ? '' : agg}</b></div>`;
}
function bktTie(t) {
  const click = t.event_id ? ` onclick="location.href='/match.html?id=${t.event_id}'"` : '';
  const legs = t.legs.filter(l => l.home_goals != null).map(l => `${l.home_goals}-${l.away_goals}`).join(', ');
  const title = t.two_legs && legs ? ` title="Two legs: ${legs}"` : '';
  return `<div class="bkt-tie-wrap"><div class="bkt-tie"${click}${title}>${bktSide(t, 'a')}${bktSide(t, 'b')}</div></div>`;
}
async function loadBracket() {
  const d = await comp();
  const b = d.bracket || [];
  if (!b.length) { _content().innerHTML = '<div class="empty">No knockout matches yet for this season.</div>'; return; }
  _content().innerHTML = `<section class="card"><div class="bkt">${b.map(r => `
    <div class="bkt-col">
      <div class="bkt-rh">${r.label}</div>
      <div class="bkt-col-body">${r.ties.map(bktTie).join('')}</div>
    </div>`).join('')}</div></section>`;
}

// ---- sub-tab: Results (every round, chronological) ----
async function loadResults() {
  const d = await comp();
  if (!d.rounds || !d.rounds.length) { _content().innerHTML = '<div class="empty">No matches for this season.</div>'; return; }
  _content().innerHTML = `<section class="card"><div class="card-h"><h3>All Results</h3>
      <span class="see">${seasonLabel()}</span></div>
    ${d.rounds.map(r => `<div class="fx-date">${r.label}</div>
      <div class="fx-list ucl-fx">${r.matches.map(matchRow).join('')}</div>`).join('')}</section>`;
}

// ---- sub-tab: Stat Leaders (top players per stat, from v_stats_ucl) ----
async function loadLeaders() {
  const d = await api('/api/ucl_leaders?season=' + encodeURIComponent(curSeason));
  if (!d.available || !d.leaders.length) { _content().innerHTML = '<div class="empty">No leader data for this season.</div>'; return; }
  const card = (b) => {
    const lead = b.top[0];
    const rest = b.top.slice(1).map((p, i) => `<a class="ll-row" href="${playerHref(p.player)}">
        <span class="ll-rk">${i + 2}</span><span class="ll-rn">${p.player}</span><b>${p.value}</b></a>`).join('');
    return `<div class="ll-card">
      <div class="ll-h">${b.label}</div>
      <a class="ll-lead" href="${playerHref(lead.player)}">
        <span class="ll-ph">${avatarHTML(lead.photo, lead.player)}</span>
        <span class="ll-nm"><b>${lead.player}</b><small>${lead.team}</small></span>
        <span class="ll-v">${lead.value}</span></a>
      ${rest ? `<div class="ll-rest">${rest}</div>` : ''}</div>`;
  };
  _content().innerHTML = `<section class="card ll-sec"><div class="card-h"><h3>Champions League Leaders</h3>
      <span class="see">top 3 per stat · ${seasonLabel()}</span></div>
    <div class="ll-grid">${d.leaders.map(card).join('')}</div></section>`;
}

const SUBS = [['bracket', 'Bracket', loadBracket],
  ['results', 'Results', loadResults], ['leaders', 'Stat Leaders', loadLeaders]];
const runSub = () => (SUBS.find(s => s[0] === curSub)[2])();

// champion banner above the tabs (only once a Final is decided)
async function renderChampion() {
  const el = document.getElementById('uclChampion');
  const d = await comp();
  el.innerHTML = d.champion ? `<div class="ucl-champ">
      <span class="ucl-trophy">🏆</span>
      <span class="ucl-champ-x"><small>${seasonLabel()} winners</small>
        <a href="${teamHref(d.champion.team)}">${crestHTML(d.champion.team_logo, 'crest-md')}<b>${d.champion.team}</b></a></span>
    </div>` : '';
}

function render() {
  const opts = SEASONS.map(s =>
    `<option value="${s.value}"${s.value === curSeason ? ' selected' : ''}>${s.label}</option>`).join('');
  document.getElementById('uclBody').innerHTML =
    `<div class="lg-bar">
       <div class="tabs lg-subtabs" id="uclSub">${SUBS.map(([k, label]) =>
         `<span class="tab ${k === curSub ? 'active' : ''}" data-k="${k}">${label}</span>`).join('')}</div>
       <label class="lg-season">Season <select id="uclSeason" class="season-sel">${opts}</select></label>
     </div>
     <div id="uclContent"><section class="card"><div class="placeholder-note">Loading…</div></section></div>`;
  const bar = document.getElementById('uclSub');
  bar.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    bar.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    curSub = t.dataset.k; runSub();
  });
  document.getElementById('uclSeason').onchange = (e) => {
    curSeason = e.target.value; renderChampion(); runSub();
  };
  renderChampion();
  runSub();
}

(async () => {
  SEASONS = await api('/api/ucl_seasons');
  curSeason = (SEASONS[0] || {}).value || '';
  const params = new URLSearchParams(location.search);
  if (params.get('season') && SEASONS.some(s => s.value === params.get('season'))) curSeason = params.get('season');
  if (params.get('tab') && SUBS.some(s => s[0] === params.get('tab'))) curSub = params.get('tab');
  render();
})();

// search -> jump to unified search results on Enter
document.getElementById('searchBox').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim())
    location.href = '/search.html?q=' + encodeURIComponent(e.target.value.trim());
});
