renderSidebar('Match Preview');
attachSearchDropdown(document.getElementById('searchBox'));

(function () {
  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const formPill = (f) => `<span class="pv-fp ${f === 'W' ? 'w' : f === 'L' ? 'l' : 'd'}">${f}</span>`;
  const natFlag = (name, code) =>
    (HOME_NATION[name] ? flagEmoji(HOME_NATION[name]) : flagISO2(code)) || '<span class="crest"></span>';
  let FIXTURES = [];

  function kickoff(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
      + ' · ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function renderList(active) {
    document.getElementById('pvCount').textContent = FIXTURES.length + ' fixtures';
    document.getElementById('pvFixtures').innerHTML = FIXTURES.map(m => `
      <div class="pv-fix ${m.event_id === active ? 'active' : ''}" data-id="${m.event_id}">
        <div class="pv-fix-teams"><span class="nflag">${natFlag(m.home, m.home_country)}</span>${esc(m.home)}
          <span class="pv-fix-v">v</span>${esc(m.away)}<span class="nflag">${natFlag(m.away, m.away_country)}</span></div>
        <div class="pv-fix-meta">${esc(m.competition)} · ${kickoff(m.kickoff_ts)}</div>
      </div>`).join('');
    document.querySelectorAll('.pv-fix').forEach(el => el.onclick = () => select(+el.dataset.id));
  }

  const keyCol = (team) => {
    const players = team.key || [];
    const row = (p) => `<a class="pv-kpl" href="/player.html?name=${encodeURIComponent(p.player)}">
      <span class="pv-kpl-ph">${avatarHTML(p.photo, p.player)}</span>
      <span class="pv-kpl-tx"><b>${esc(p.player)}</b><span>${esc(p.position)}${p.club ? ' · ' + esc(p.club) : ''}</span></span>
      <span class="pv-kpl-rat">${p.rating}</span></a>`;
    return `<div class="pv-keycol"><h5>${esc(team.name)}</h5>
      ${players.length ? players.map(row).join('') : '<div class="muted" style="font-size:12px;padding:6px 0">No top-5-league players in the squad.</div>'}</div>`;
  };

  const recentList = (team) => `<div class="pv-recent">
    <div class="pv-form-pills">${team.recent.map(r => formPill(r.result)).join('') || '<span class="muted" style="font-size:12px">No recent matches</span>'}</div>
    ${team.recent.slice(0, 5).map(r => `<div class="pv-rec-row"><span class="pv-fp ${r.result === 'W' ? 'w' : r.result === 'L' ? 'l' : 'd'}">${r.result}</span>
      <b>${r.gf}–${r.ga}</b><span>vs ${esc(r.opponent)}</span></div>`).join('')}</div>`;

  async function select(eid) {
    renderList(eid);
    history.replaceState(null, '', '?id=' + eid);
    const out = document.getElementById('pv-out');
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Building the preview…</p></div></section>`;
    let d;
    try { d = await api('/api/fixture_preview?id=' + eid); }
    catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not build the preview.</p></div></section>'; return; }
    if (!d.available) { out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">⚖️</div><p>${esc(d.error || 'Unavailable.')}</p></div></section>`; return; }

    const H = d.home, A = d.away, p = d.prediction, h2h = d.h2h;
    out.innerHTML = `
      <section class="card pv-hero">
        <div class="pv-team"><div class="pv-crest">${natFlag(H.name, H.country)}</div><h3>${esc(H.name)}</h3></div>
        <div class="pv-vs"><span>VS</span><small>${esc(d.competition || '')}${d.round ? ' · ' + esc(d.round) : ''}</small><small>${kickoff(d.kickoff_ts)}</small></div>
        <div class="pv-team away"><div class="pv-crest">${natFlag(A.name, A.country)}</div><h3>${esc(A.name)}</h3></div>
      </section>

      ${p ? `<section class="card pv-pred">
        <div class="card-h"><h3>Projection</h3><span class="muted" style="font-size:12px">bookmaker consensus</span></div>
        <div class="pv-bar"><i class="h" style="width:${p.home}%">${p.home}%</i><i class="d" style="width:${p.draw}%">${p.draw}%</i><i class="a" style="width:${p.away}%">${p.away}%</i></div>
        <div class="pv-bar-leg"><span><i class="dot h"></i>${esc(H.name)}</span><span><i class="dot d"></i>Draw</span><span><i class="dot a"></i>${esc(A.name)}</span></div>
      </section>` : ''}

      <section class="card pv-form">
        <div class="card-h"><h3>Recent form</h3></div>
        <div class="pv-keycols">${recentList(H)}${recentList(A)}</div>
      </section>

      <section class="card">
        <div class="card-h"><h3>Key players</h3><span class="muted" style="font-size:12px">by Atlastra rating</span></div>
        <div class="pv-keycols">${keyCol(H)}${keyCol(A)}</div>
      </section>

      <section class="card">
        <div class="card-h"><h3>Head-to-head</h3></div>
        ${h2h ? `<div class="pv-h2h-tally">
            <div class="pv-h2h-t"><b>${h2h.home_wins ?? 0}</b><span>${esc(H.name)} wins</span></div>
            <div class="pv-h2h-t"><b>${h2h.draws ?? 0}</b><span>Draws</span></div>
            <div class="pv-h2h-t"><b>${h2h.away_wins ?? 0}</b><span>${esc(A.name)} wins</span></div>
          </div>` : '<div class="muted" style="padding:8px">No previous meetings on record.</div>'}
      </section>`;
  }

  (async function init() {
    let live;
    try { live = await api('/api/live?upcoming=60&recent=0'); } catch { return; }
    FIXTURES = (live.upcoming || []).sort((a, b) => (a.kickoff_ts || 0) - (b.kickoff_ts || 0));
    if (!FIXTURES.length) {
      document.getElementById('pvFixtures').innerHTML = '';
      document.getElementById('pv-out').innerHTML = '<section class="card"><div class="sr-empty"><div class="sr-empty-ic">📅</div><p>No upcoming fixtures in the feed right now.</p></div></section>';
      return;
    }
    const want = +new URLSearchParams(location.search).get('id');
    const start = FIXTURES.find(m => m.event_id === want) || FIXTURES[0];
    renderList(start.event_id);
    select(start.event_id);
  })();
})();
