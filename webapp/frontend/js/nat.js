renderSidebar('Teams');
attachSearchDropdown(document.getElementById('searchBox'));

const ID = new URLSearchParams(location.search).get('id');
const POS = [['G', 'Goalkeepers'], ['D', 'Defenders'], ['M', 'Midfielders'], ['F', 'Forwards']];

const matchDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString([], { day: 'numeric', month: 'short', year: '2-digit' }) : '';

function eventRow(r, upcoming) {
  const played = r.home_score != null;
  const score = played ? `${r.home_score}<span class="dash">-</span>${r.away_score}`
    : `<span class="vs">${matchDate(r.ts)}</span>`;
  const click = r.event_id != null ? ` onclick="location.href='/match.html?id=${r.event_id}'" style="cursor:pointer"` : '';
  return `<div class="nat-ev"${click}>
      <span class="ne-home">${esc(r.home)}</span>
      <span class="ne-sc">${score}</span>
      <span class="ne-away">${esc(r.away)}</span>
      <span class="ne-comp">${esc(r.competition || '')}${played ? ' · ' + matchDate(r.ts) : ''}</span>
    </div>`;
}

const _surname = (n) => { const p = String(n || '').trim().split(' '); return p.length > 1 ? p[p.length - 1] : n; };

// latest starting XI as a single-team formation pitch (GK at bottom, attack up)
function renderLatestXI(x) {
  const el = document.getElementById('latestXI');
  if (!x || !x.starting_xi || !x.starting_xi.length) { el.innerHTML = ''; return; }
  const parts = String(x.formation || '').split('-').map(n => parseInt(n, 10)).filter(n => n > 0);
  const rows = parts.length && parts.reduce((a, b) => a + b, 0) + 1 === x.starting_xi.length ? [1, ...parts] : null;
  const score = x.home_score != null ? ` (${x.home_score}-${x.away_score})` : '';
  const head = `vs ${esc(x.opponent || '')}${score} · ${esc(x.formation || '')}`;
  if (!rows) {   // can't lay out -> simple list fallback
    el.innerHTML = `<section class="card"><div class="card-h"><h3>Latest Starting XI</h3><span class="see">${head}</span></div>
      <div class="muted">${x.starting_xi.map(p => esc(p.name)).join(' · ')}</div></section>`;
    return;
  }
  const chips = [];
  let idx = 0;
  for (let ri = 0; ri < rows.length; ri++) {
    const k = rows[ri], t = rows.length === 1 ? 0 : ri / (rows.length - 1), y = 0.91 - t * 0.80;
    for (let j = 0; j < k; j++) {
      const slot = k - 1 - j;                 // team attacks up -> left on viewer's left
      const cx = 0.10 + 0.80 * ((slot + 0.5) / k), p = x.starting_xi[idx++];
      const rt = p.rating != null ? `<i class="luc-rt" style="background:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</i>` : '';
      const cap = p.captain ? '<i class="luc-cap">C</i>' : '';
      chips.push(`<div class="luc h" style="left:${(cx * 100).toFixed(1)}%;top:${(y * 100).toFixed(1)}%"
        onclick="location.href='/player.html?name=${encodeURIComponent(p.name)}'" title="${esc(p.name)}">
        <span class="luc-dot">${p.number ?? ''}${rt}${cap}</span><span class="luc-nm">${esc(_surname(p.name))}</span></div>`);
    }
  }
  el.innerHTML = `<section class="card"><div class="card-h"><h3>Latest Starting XI</h3><span class="see">${head}</span></div>
    <div class="lineup-pitch nat-pitch"><span class="lp-mid"></span><span class="lp-circle"></span><span class="lp-spot"></span>
      <span class="lp-box top"></span><span class="lp-box bot"></span>${chips.join('')}</div></section>`;
}

function renderSquad(squad) {
  document.getElementById('squadCount').textContent = squad.length ? squad.length + ' players' : '';
  document.getElementById('squad').innerHTML = POS.map(([code, label]) => {
    const ps = squad.filter(p => p.position === code);
    if (!ps.length) return '';
    return `<div class="sq-group"><h5>${label}</h5><div class="sq-list">${ps.map(p =>
      `<div class="sq-p" onclick="location.href='/player.html?name=${encodeURIComponent(p.name)}'">
        <span class="sq-no">${p.number ?? ''}</span><span class="sq-nm">${esc(p.name)}</span></div>`).join('')}</div></div>`;
  }).join('') || '<div class="muted">No squad data.</div>';
}

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

function render(d) {
  const flag = (typeof HOME_NATION !== 'undefined' && HOME_NATION[d.name]
    ? flagEmoji(HOME_NATION[d.name]) : flagISO2(d.country_code)) || '';
  document.getElementById('crumb').textContent = d.name;
  document.getElementById('natName').innerHTML = `${flag} ${esc(d.name)}`;
  document.getElementById('natSub').textContent = d.manager ? `👔 ${d.manager}` : '';
  renderLatestXI(d.latest_xi);
  document.getElementById('results').innerHTML =
    d.results.length ? d.results.map(r => eventRow(r, false)).join('') : '<div class="muted">No recent results.</div>';
  document.getElementById('fixtures').innerHTML =
    d.fixtures.length ? d.fixtures.map(r => eventRow(r, true)).join('') : '<div class="muted">No upcoming fixtures.</div>';
  renderSquad(d.squad);
}

(async () => {
  if (!ID) { document.getElementById('natName').textContent = 'No team selected'; return; }
  // On the deployed server this is relay-fetched from SofaScore on demand: the header
  // arrives first, then squad/results/fixtures, then the latest XI -- each a relay
  // cycle apart. Wait for availability, then keep refreshing (re-rendering) until the
  // sub-data is all in.
  const url = '/api/national_team?id=' + encodeURIComponent(ID);
  document.getElementById('natName').textContent = 'Loading…';
  let d = await api(url);
  for (let i = 0; i < 8 && d && d.available === false && d.pending; i++) {
    await new Promise(r => setTimeout(r, 3000));
    d = await api(url);
  }
  if (!d.available) { document.getElementById('natName').textContent = 'Team not found'; return; }
  render(d);
  for (let i = 0; i < 8 && (!d.squad.length || !d.latest_xi); i++) {
    await new Promise(r => setTimeout(r, 3500));
    const n = await api(url).catch(() => null);
    if (n && n.available) { d = n; render(d); }
  }
})();
