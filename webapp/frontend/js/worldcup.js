renderSidebar('World Cup');

let SEASONS = [], curSeason = '', curSub = 'bracket';
const _cache = {};       // season -> /api/worldcup bundle
const _content = () => document.getElementById('wcContent');

// flag for a national team: home nations by name, else the ISO country code
const wcFlag = (name, cc) => (HOME_NATION[name] ? flagEmoji(HOME_NATION[name]) : flagISO2(cc)) || '';
// rankBadge(r) is shared from api.js
// SofaScore placeholder slot (e.g. '2A', '1C', 'W101', '3A/3B/3C') — not a real team yet
const isSlot = (name, cc) => !cc && !HOME_NATION[name];

async function wc() {
  if (!_cache[curSeason]) _cache[curSeason] = await api('/api/worldcup?season=' + encodeURIComponent(curSeason));
  return _cache[curSeason];
}

// ---- Bracket (knockout tree, Round of 32/16 → Final) ----
function bktSide(t, k) {
  const name = t[k];
  if (!name) return `<div class="bkt-row tbd"><span class="bkt-tm">TBD</span><b></b></div>`;
  const cc = t[k + '_cc'], agg = t[k + '_agg'];
  if (isSlot(name, cc)) return `<div class="bkt-row tbd"><span class="bkt-tm">${name}</span><b></b></div>`;
  const cls = t.winner === k ? 'win' : t.winner ? 'lose' : '';
  return `<div class="bkt-row ${cls}">
    <span class="bkt-tm"><span class="wc-flag">${wcFlag(name, cc)}</span><span class="tn">${name}</span>${rankBadge(t[k + '_rank'])}</span>
    <b>${agg == null ? '' : agg}</b></div>`;
}
function bktTie(t) {
  const click = t.event_id ? ` onclick="location.href='/match.html?id=${t.event_id}'"` : '';
  const live = t.live ? '<span class="bkt-live-tag">● LIVE</span>' : '';
  return `<div class="bkt-tie-wrap"><div class="bkt-tie${t.live ? ' live' : ''}"${click}>${bktSide(t, 'a')}${bktSide(t, 'b')}${live}</div></div>`;
}
function renderBracket(d) {
  const b = d.bracket || [];
  if (!b.length) { _content().innerHTML = '<div class="empty">No knockout matches for this edition.</div>'; return; }
  _content().innerHTML = `<section class="card"><div class="bkt">${b.map(r => `
    <div class="bkt-col">
      <div class="bkt-rh">${r.label}</div>
      <div class="bkt-col-body">${r.ties.map(bktTie).join('')}</div>
    </div>`).join('')}</div></section>`;
}
let _wcTimer = null;
async function loadBracket() {
  clearInterval(_wcTimer);
  renderBracket(await wc());
  // keep the bracket live: re-fetch every 30s while it's the open tab so scores,
  // winners and newly-resolved knockout slots update without a reload.
  _wcTimer = setInterval(async () => {
    if (document.hidden || curSub !== 'bracket') return;
    try {
      _cache[curSeason] = await api('/api/worldcup?season=' + encodeURIComponent(curSeason));
      if (curSub === 'bracket') renderBracket(_cache[curSeason]);
    } catch { /* keep last good render */ }
  }, 30000);
}

// ---- Results (matches by round) ----
function matchRow(m) {
  const score = m.home_goals != null ? `${m.home_goals}<span class="fx-dash">-</span>${m.away_goals}` : '<span class="muted">vs</span>';
  const pens = m.home_pens != null ? `<span class="wc-pens">pens ${m.home_pens}–${m.away_pens}</span>` : '';
  const click = m.event_id ? ` onclick="location.href='/match.html?id=${m.event_id}'" style="cursor:pointer"` : '';
  return `<div class="fx-row"${click}>
    <span class="fx-t home"><span class="tn">${m.home}</span>${rankBadge(m.home_rank)}<span class="wc-flag">${wcFlag(m.home, m.home_cc)}</span></span>
    <span class="fx-sc">${score}${pens}</span>
    <span class="fx-t away"><span class="wc-flag">${wcFlag(m.away, m.away_cc)}</span>${rankBadge(m.away_rank)}<span class="tn">${m.away}</span></span></div>`;
}
async function loadResults() {
  const d = await wc();
  if (!d.rounds || !d.rounds.length) { _content().innerHTML = '<div class="empty">No matches for this edition.</div>'; return; }
  _content().innerHTML = `<section class="card"><div class="card-h"><h3>Results &amp; Fixtures</h3>
      <span class="see">World Cup ${curSeason}</span></div>
    ${d.rounds.map(r => `<div class="fx-date">${r.label}</div>
      <div class="fx-list ucl-fx">${r.matches.map(matchRow).join('')}</div>`).join('')}</section>`;
}

// ---- Standings (group tables) ----
const posTint = (p, n) => (p <= n / 2 ? 'ucl' : '');   // top half of a 4-team group advance
async function loadStandings() {
  const d = await wc();
  if (!d.groups || !d.groups.length) { _content().innerHTML = '<div class="empty">No group standings for this edition.</div>'; return; }
  const table = (g) => {
    const n = g.rows.length;
    return `<section class="card wc-grp"><div class="card-h"><h3>${g.name}</h3></div>
      <table class="ltbl"><thead><tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th>
        <th>GF</th><th>GA</th><th>GD</th><th>Pts</th></tr></thead><tbody>
      ${g.rows.map(t => `<tr class="ltbl-row ${g.name.startsWith('Group') ? posTint(t.position, n) : ''}">
        <td class="pos">${t.position}</td>
        <td class="tcell"><span class="wc-flag">${wcFlag(t.team, t.cc)}</span><span class="tn">${t.team}</span>${rankBadge(t.rank)}</td>
        <td>${t.played}</td><td>${t.w}</td><td>${t.d}</td><td>${t.l}</td>
        <td>${t.gf}</td><td>${t.ga}</td><td class="${t.gd > 0 ? 'pos-n' : t.gd < 0 ? 'neg-n' : ''}">${t.gd > 0 ? '+' : ''}${t.gd}</td>
        <td><b>${t.pts}</b></td></tr>`).join('')}</tbody></table></section>`;
  };
  _content().innerHTML = `<div class="wc-groups">${d.groups.map(table).join('')}</div>`;
}

// ---- Stat Leaders (top players per stat, SofaScore tournament snapshot) ----
async function loadLeaders() {
  const d = await api('/api/wc_leaders?season=' + encodeURIComponent(curSeason));
  if (!d.available || !d.leaders.length) { _content().innerHTML = '<div class="empty">No leader data for this edition.</div>'; return; }
  const card = (b) => {
    const lead = b.top[0];
    const row = (p, i) => `<div class="ll-row">
        <span class="ll-rk">${i + 2}</span><span class="ll-rn"><span class="wc-flag">${wcFlag(p.player, p.cc)}</span>${p.player}</span><b>${p.value}</b></div>`;
    const rest = b.top.slice(1).map(row).join('');
    return `<div class="ll-card">
      <div class="ll-h">${b.label}</div>
      <div class="ll-lead">
        <span class="ll-ph">${avatarHTML(null, lead.player)}</span>
        <span class="ll-nm"><b>${lead.player}</b><small><span class="wc-flag">${wcFlag(lead.player, lead.cc)}</span> ${lead.team}</small></span>
        <span class="ll-v">${lead.value}</span></div>
      ${rest ? `<div class="ll-rest">${rest}</div>` : ''}</div>`;
  };
  _content().innerHTML = `<section class="card ll-sec"><div class="card-h"><h3>Tournament Leaders</h3>
      <span class="see">top 3 per stat · World Cup ${curSeason}</span></div>
    <div class="ll-grid">${d.leaders.map(card).join('')}</div></section>`;
}

const SUBS = [['bracket', 'Bracket', loadBracket],
  ['results', 'Results', loadResults], ['standings', 'Standings', loadStandings],
  ['leaders', 'Stat Leaders', loadLeaders]];
const runSub = () => (SUBS.find(s => s[0] === curSub)[2])();

async function renderChampion() {
  const el = document.getElementById('wcChampion');
  const d = await wc();
  el.innerHTML = d.champion ? `<div class="ucl-champ">
      <span class="ucl-trophy">🏆</span>
      <span class="ucl-champ-x"><small>${curSeason} World Cup winners</small>
        <span class="wc-champ-nm"><span class="wc-flag">${wcFlag(d.champion.team, d.champion.cc)}</span><b>${d.champion.team}</b></span></span>
    </div>` : '';
}

function render() {
  const opts = SEASONS.map(s =>
    `<option value="${s.value}"${s.value === curSeason ? ' selected' : ''}>${s.label}</option>`).join('');
  document.getElementById('wcBody').innerHTML =
    `<div class="lg-bar">
       <div class="tabs lg-subtabs" id="wcSub">${SUBS.map(([k, label]) =>
         `<span class="tab ${k === curSub ? 'active' : ''}" data-k="${k}">${label}</span>`).join('')}</div>
       <label class="lg-season">Edition <select id="wcSeason" class="season-sel">${opts}</select></label>
     </div>
     <div id="wcContent"><section class="card"><div class="placeholder-note">Loading…</div></section></div>`;
  const bar = document.getElementById('wcSub');
  bar.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    bar.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    curSub = t.dataset.k; runSub();
  });
  document.getElementById('wcSeason').onchange = (e) => {
    curSeason = e.target.value; render();        // keeps the current sub-tab
  };
  renderChampion();
  runSub();
}

// during the group stage the bracket is mostly placeholders, so open on Standings;
// once a knockout result exists (or the edition is finished), open on the Bracket.
async function pickDefaultTab() {
  const d = await wc();
  const koPlayed = (d.bracket || []).some(r => r.ties.some(t => t.winner || t.live));
  curSub = koPlayed ? 'bracket' : 'standings';
}

(async () => {
  SEASONS = await api('/api/wc_seasons');
  curSeason = (SEASONS[0] || {}).value || '';
  const params = new URLSearchParams(location.search);
  if (params.get('season') && SEASONS.some(s => s.value === params.get('season'))) curSeason = params.get('season');
  await pickDefaultTab();
  if (params.get('tab') && SUBS.some(s => s[0] === params.get('tab'))) curSub = params.get('tab');
  render();
})();

// topbar search = the global player/team/match dropdown (same as Home)
attachSearchDropdown(document.getElementById('searchBox'));
