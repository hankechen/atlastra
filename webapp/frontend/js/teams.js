renderSidebar('Teams');

const formPills = (form) => (form || []).map(f => `<span class="form-pill form-${f}">${f}</span>`).join('');
const teamHref = (name) => '/team.html?name=' + encodeURIComponent(name);

// promotion/relegation tint by position (top 4 = UCL-ish, bottom 3 = drop)
function posClass(pos, n) {
  if (pos <= 4) return 'ucl';
  if (pos > n - 3) return 'rel';
  return '';
}

async function loadTable(key) {
  const rows = await api('/api/league_table?league=' + encodeURIComponent(key));
  const n = rows.length;
  document.getElementById('teamsBody').innerHTML = `<section class="card"><div class="ltbl-wrap">
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
  loadLeaders(key);
}

// per-league leaders in every stat: top scorer, assister, creator, dribbler, …
async function loadLeaders(key) {
  let d;
  try { d = await api('/api/league_leaders?league=' + encodeURIComponent(key)); }
  catch { return; }
  if (!d.available || !d.leaders.length) return;
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
  const sec = document.createElement('section');
  sec.className = 'card ll-sec';
  sec.innerHTML = `<div class="card-h"><h3>League Leaders</h3>
      <span class="see">${d.league} · this season · top 3 per stat</span></div>
    <div class="ll-grid">${d.leaders.map(card).join('')}</div>`;
  document.getElementById('teamsBody').appendChild(sec);
}

// National teams (international feed): a grid of flag cards with recent record/form
async function loadNational() {
  const rows = await api('/api/national_teams');
  const card = (t) => {
    const click = t.team_id != null ? ` onclick="location.href='/nat.html?id=${t.team_id}'" style="cursor:pointer"` : '';
    const rec = t.played ? `${t.w}-${t.d}-${t.l} · GF ${t.gf}/GA ${t.ga}` : 'no recent matches';
    // ISO-2 codes from the feed; England/Scotland/Wales use name-based tag flags
    const flag = (HOME_NATION[t.team] ? flagEmoji(HOME_NATION[t.team]) : flagISO2(t.country_code)) || '🏳';
    return `<div class="nt-card"${click}>
      <span class="nt-flag">${flag}</span>
      <span class="nt-x"><div class="nt-nm">${t.team}</div><div class="nt-sub">${rec}</div></span>
      <span class="nt-form">${formPills(t.form)}</span></div>`;
  };
  document.getElementById('teamsBody').innerHTML = rows.length
    ? `<section class="card"><div class="card-h"><h3>National Teams</h3><span class="see">${rows.length} teams · international feed</span></div>
       <div class="nt-grid">${rows.map(card).join('')}</div></section>`
    : '<div class="empty">No national teams in the current window.</div>';
}

(async () => {
  const leagues = await api('/api/leagues');
  const tabsEl = document.getElementById('leagueTabs');
  const tabs = [...leagues.map(l => [l.key, l.name]), ['__national', 'National Teams']];
  tabsEl.innerHTML = tabs.map(([k, name], i) =>
    `<span class="tab ${i ? '' : 'active'}" data-k="${k}">${name}</span>`).join('');
  tabsEl.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    tabsEl.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    if (t.dataset.k === '__national') loadNational(); else loadTable(t.dataset.k);
  });
  // ?tab=national deep-links the National Teams tab
  if (new URLSearchParams(location.search).get('tab') === 'national') {
    tabsEl.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x.dataset.k === '__national'));
    loadNational();
  } else {
    loadTable(leagues[0].key);
  }
})();

// search -> jump to a team page on Enter
document.getElementById('searchBox').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) location.href = teamHref(e.target.value.trim());
});
