renderSidebar('Teams');

const formPills = (form) => (form || []).map(f => `<span class="form-pill form-${f}">${f}</span>`).join('');
const teamHref = (name) => '/team.html?name=' + encodeURIComponent(name);
const _content = () => document.getElementById('leagueContent');
let SEASONS = [], curSeason = '', curKey = null, curSub = 'standings';
const _seasonQS = () => curSeason ? '&season=' + encodeURIComponent(curSeason) : '';

// promotion/relegation tint by position (top 4 = UCL-ish, bottom 3 = drop)
function posClass(pos, n) {
  if (pos <= 4) return 'ucl';
  if (pos > n - 3) return 'rel';
  return '';
}

// ---- per-league sub-tab: Standings ----
async function loadStandings(key) {
  const rows = await api('/api/league_table?league=' + encodeURIComponent(key) + _seasonQS());
  const n = rows.length;
  _content().innerHTML = `<section class="card"><div class="ltbl-wrap">
    <table class="ltbl"><thead><tr>
      <th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th>
      <th>GF</th><th>GA</th><th>GD</th><th>xG</th><th>xGA</th><th>xPts</th><th>Pts</th><th>Form</th>
    </tr></thead><tbody>${rows.map(t => `
      <tr class="ltbl-row ${posClass(t.pos, n)}" onclick="location.href='${teamHref(t.team)}'">
        <td class="pos">${t.pos}</td>
        <td class="tcell"><span class="crest-w">${crestHTML(t.team_logo, 'crest-md')}</span><span class="tn">${t.team}</span></td>
        <td>${t.p}</td><td>${t.w}</td><td>${t.d}</td><td>${t.l}</td>
        <td>${t.gf}</td><td>${t.ga}</td><td class="${t.gd > 0 ? 'pos-n' : t.gd < 0 ? 'neg-n' : ''}">${t.gd > 0 ? '+' : ''}${t.gd}</td>
        <td class="muted">${t.xg_for ?? '—'}</td><td class="muted">${t.xg_against ?? '—'}</td>
        <td class="muted">${t.xpts ?? '—'}</td>
        <td><b>${t.pts}</b></td>
        <td class="formcell">${formPills(t.form)}</td>
      </tr>`).join('')}</tbody></table></div></section>`;
}

// ---- per-league sub-tab: Fixtures & Results ----
async function loadFixtures(key) {
  const d = await api('/api/league_fixtures?league=' + encodeURIComponent(key) + _seasonQS());
  const el = _content();
  if (!d.available || !d.matches.length) { el.innerHTML = '<div class="empty">No fixtures for this league.</div>'; return; }
  let html = '', last = null;
  for (const m of d.matches) {
    const dt = new Date(m.date).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
    if (dt !== last) { html += `<div class="fx-date">${m.is_result ? '' : 'Upcoming · '}${dt}</div>`; last = dt; }
    const score = m.home_goals != null ? `${m.home_goals}<span class="fx-dash">-</span>${m.away_goals}` : '<span class="muted">vs</span>';
    html += `<div class="fx-row">
      <a class="fx-t home" href="${teamHref(m.home)}"><span class="tn">${m.home}</span>${crestHTML(m.home_logo, 'crest-sm')}</a>
      <span class="fx-sc">${score}</span>
      <a class="fx-t away" href="${teamHref(m.away)}">${crestHTML(m.away_logo, 'crest-sm')}<span class="tn">${m.away}</span></a>
      <span class="fx-xg muted">${m.home_xg != null ? 'xG ' + m.home_xg + ' – ' + m.away_xg : ''}</span></div>`;
  }
  el.innerHTML = `<section class="card"><div class="card-h"><h3>Fixtures &amp; Results</h3>
      <span class="see">${d.matches.length} matches · ${seasonLabel()}</span></div>
    <div class="fx-list">${html}</div></section>`;
}

// ---- per-league sub-tab: League Leaders (top players in every stat) ----
async function loadLeaders(key) {
  const d = await api('/api/league_leaders?league=' + encodeURIComponent(key) + _seasonQS());
  const el = _content();
  if (!d.available || !d.leaders.length) { el.innerHTML = '<div class="empty">No leader data for this league.</div>'; return; }
  const ph = (p) => `/player.html?name=${encodeURIComponent(p.player)}`;
  const card = (b) => {
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
  };
  el.innerHTML = `<section class="card ll-sec"><div class="card-h"><h3>League Leaders</h3>
      <span class="see">top 3 per stat · ${seasonLabel()}</span></div>
    <div class="ll-grid">${d.leaders.map(card).join('')}</div></section>`;
}

// ---- per-league sub-tab: Season Sim (Monte Carlo title/top-4/relegation odds) ----
function simAge(s) {
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  return Math.floor(s / 3600) + 'h ago';
}
const simBar = (pct, cls) => `<div class="ss-bar"><div class="ss-bar-fill ${cls}" style="width:${Math.max(pct, pct > 0 ? 2 : 0)}%"></div><span class="ss-bar-lbl">${pct}%</span></div>`;
async function loadSeasonSim(key) {
  const el = _content();
  const d = await api('/api/season_sim?league=' + encodeURIComponent(key));
  if (!d.available) {
    el.innerHTML = `<div class="empty">${d.error || 'Season simulation not available for this league.'}</div>`;
    return;
  }
  const n = d.table.length;
  el.innerHTML = `<section class="card"><div class="card-h"><h3>Season Simulation</h3>
      <span class="see">${d.n_trials.toLocaleString()} trials · ${d.n_fixtures} fixtures left · updated ${simAge(d.age_seconds)}</span></div>
    <div class="ltbl-wrap"><table class="ltbl ss-tbl"><thead><tr>
      <th>#</th><th>Team</th><th>Pts</th><th>Title</th><th>Top 4</th><th>Relegation</th><th>Proj. Pts</th>
    </tr></thead><tbody>${d.table.map(t => `
      <tr class="ltbl-row ${posClass(Math.round(t.median_position), n)}" onclick="location.href='${teamHref(t.team)}'">
        <td class="pos">${Math.round(t.median_position)}</td>
        <td class="tcell"><span class="tn">${t.team}</span></td>
        <td><b>${t.current_points}</b></td>
        <td>${simBar(t.title_pct, 'ss-title')}</td>
        <td>${simBar(t.top4_pct, 'ss-top4')}</td>
        <td>${simBar(t.relegation_pct, 'ss-rel')}</td>
        <td class="muted">${t.median_points} <small>(${t.p10_points}–${t.p90_points})</small></td>
      </tr>`).join('')}</tbody></table></div>
    <div class="placeholder-note" style="margin:14px">Monte Carlo: the remaining fixtures are played out ${d.n_trials.toLocaleString()} times, each
      match drawn from Atlastra's own blended match model (the same 50/50 fitted-squad + Elo/form ensemble live on the match
      Odds tab) with a scoreline sampled from the fitted xG grid for realistic goal difference. Odds are the share of trials
      each team finished there — not a single predicted table. Proj. Pts shows the median and 10th–90th percentile range.</div>
    </section>`;
}

// open a league: render its sub-tabs (Standings / Fixtures / League Leaders)
const LEAGUE_SUBS = [['standings', 'Standings', loadStandings],
  ['fixtures', 'Fixtures', loadFixtures], ['leaders', 'League Leaders', loadLeaders],
  ['sim', 'Season Sim', loadSeasonSim]];
const seasonLabel = () => (SEASONS.find(s => s.value === curSeason) || {}).label || 'this season';
const runSub = () => (LEAGUE_SUBS.find(s => s[0] === curSub)[2])(curKey);
function openLeague(key, sub = 'standings') {
  curKey = key; curSub = sub;
  const opts = SEASONS.map(s =>
    `<option value="${s.value}"${s.value === curSeason ? ' selected' : ''}>${s.label}</option>`).join('');
  document.getElementById('teamsBody').innerHTML =
    `<div class="lg-bar">
       <div class="tabs lg-subtabs" id="leagueSub">${LEAGUE_SUBS.map(([k, label]) =>
         `<span class="tab ${k === sub ? 'active' : ''}" data-k="${k}">${label}</span>`).join('')}</div>
       <label class="lg-season">Season <select id="leagueSeason" class="season-sel">${opts}</select></label>
     </div>
     <div id="leagueContent"><section class="card"><div class="placeholder-note">Loading…</div></section></div>`;
  const bar = document.getElementById('leagueSub');
  bar.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    bar.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    curSub = t.dataset.k; runSub();
  });
  document.getElementById('leagueSeason').onchange = (e) => { curSeason = e.target.value; runSub(); };
  runSub();
}

// National teams (international feed): a grid of flag cards with recent record/form
async function loadNational() {
  const rows = await api('/api/national_teams');
  const card = (t) => {
    const click = t.team_id != null ? ` onclick="location.href='/nat.html?id=${t.team_id}'" style="cursor:pointer"` : '';
    const rec = t.played ? `${t.w}-${t.d}-${t.l} · GF ${t.gf}/GA ${t.ga}` : 'no recent matches';
    const flag = (HOME_NATION[t.team] ? flagEmoji(HOME_NATION[t.team]) : flagISO2(t.country_code)) || '🏳';
    return `<div class="nt-card"${click}>
      <span class="nt-flag">${flag}</span>
      <span class="nt-x"><div class="nt-nm">${t.team}${rankBadge(t.fifa_rank)}</div><div class="nt-sub">${rec}</div></span>
      <span class="nt-form">${formPills(t.form)}</span></div>`;
  };
  document.getElementById('teamsBody').innerHTML = rows.length
    ? `<section class="card"><div class="card-h"><h3>National Teams</h3><span class="see">${rows.length} teams · international feed</span></div>
       <div class="nt-grid">${rows.map(card).join('')}</div></section>`
    : '<div class="empty">No national teams in the current window.</div>';
}

(async () => {
  const [leagues, seasons] = await Promise.all([api('/api/leagues'), api('/api/seasons')]);
  SEASONS = seasons; curSeason = (SEASONS[0] || {}).value || '';
  const tabsEl = document.getElementById('leagueTabs');
  const tabs = [...leagues.map(l => [l.key, l.name]), ['__national', 'National Teams']];
  // open the requested tab: ?tab=national, ?league=<key|name>, else first league
  const params = new URLSearchParams(location.search);
  let activeKey = leagues[0].key;
  if (params.get('tab') === 'national') {
    activeKey = '__national';
  } else if (params.get('league')) {
    const want = params.get('league').toLowerCase();
    const m = tabs.find(([k, name]) => k.toLowerCase() === want || name.toLowerCase() === want);
    if (m) activeKey = m[0];
  }
  tabsEl.innerHTML = tabs.map(([k, name]) =>
    `<span class="tab ${k === activeKey ? 'active' : ''}" data-k="${k}">${name}</span>`).join('');
  tabsEl.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    tabsEl.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    if (t.dataset.k === '__national') loadNational(); else openLeague(t.dataset.k);
  });
  if (activeKey === '__national') loadNational(); else openLeague(activeKey);
})();

// search -> jump to a team page on Enter
// topbar search = the global player/team/match dropdown (same as Home)
attachSearchDropdown(document.getElementById('searchBox'));
