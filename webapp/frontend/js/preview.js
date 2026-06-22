renderSidebar('Match Preview');
attachSearchDropdown(document.getElementById('searchBox'));

(function () {
  const params = new URLSearchParams(location.search);
  let home = params.get('home') || '';
  let away = params.get('away') || '';
  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const AREA_LABEL = { ATT: 'Attack', MID: 'Midfield', DEF: 'Defence', GK: 'Goalkeeper' };
  const formPill = (f) => `<span class="pv-fp ${f === 'W' ? 'w' : f === 'L' ? 'l' : 'd'}">${f}</span>`;

  function teamPicker(inputId, ddId, set) {
    const input = document.getElementById(inputId), dd = document.getElementById(ddId);
    if (set) input.value = set;
    const hide = () => { dd.style.display = 'none'; };
    input.oninput = () => {
      const q = input.value.trim();
      if (q.length < 2) { hide(); return; }
      api('/api/search?q=' + encodeURIComponent(q)).then(r => {
        const rows = (r.teams || []).slice(0, 7);
        if (!rows.length) { hide(); return; }
        dd.innerHTML = rows.map(t => `<div class="card-dd-it" data-n="${esc(t.team)}">
          <b>${esc(t.team)}</b><span>${esc(t.league || t.country || '')}</span></div>`).join('');
        dd.style.display = 'block';
        dd.querySelectorAll('.card-dd-it').forEach(el => el.onclick = () => {
          input.value = el.dataset.n; hide();
          if (inputId === 'homeSearch') home = el.dataset.n; else away = el.dataset.n;
          if (home && away) load();
        });
      }).catch(() => {});
    };
    document.addEventListener('click', (e) => { if (!input.contains(e.target) && !dd.contains(e.target)) hide(); });
  }

  function statRow(label, hv, av, fmt) {
    const hn = +hv || 0, an = +av || 0, tot = (hn + an) || 1;
    const f = fmt || ((x) => x == null ? '—' : x);
    return `<div class="pv-stat"><b>${f(hv)}</b>
      <div class="pv-mid"><span>${label}</span>
        <div class="pv-sbar"><i class="h" style="width:${hn / tot * 100}%"></i><i class="a" style="width:${an / tot * 100}%"></i></div></div>
      <b>${f(av)}</b></div>`;
  }

  function keyRow(area, H, A) {
    const cell = (p, side) => p ? `<a class="pv-kp ${side}" href="/player.html?name=${encodeURIComponent(p.player)}">
      <span class="pv-kp-ph">${avatarHTML(p.photo, p.player)}</span>
      <span class="pv-kp-tx"><b>${esc(p.player)}</b><span>${p.position} · ${p.rating} rtg · ${p.goals}G ${p.assists}A</span></span></a>`
      : `<div class="pv-kp ${side} empty">—</div>`;
    return `<div class="pv-keyrow">${cell(H, 'h')}<span class="pv-keylbl">${AREA_LABEL[area] || area}</span>${cell(A, 'a')}</div>`;
  }

  async function load() {
    const out = document.getElementById('pv-out');
    history.replaceState(null, '', `?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`);
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Building the preview…</p></div></section>`;
    let d;
    try { d = await api(`/api/preview?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`); }
    catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not build the preview.</p></div></section>'; return; }
    if (!d.available) { out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">⚖️</div><p>${esc(d.error || 'Unavailable.')}</p></div></section>`; return; }

    const H = d.home, A = d.away, p = d.prediction, h2h = d.h2h;
    const crest = (t) => crestHTML(t.logo, 'crest') || '🛡️';
    const areas = ['ATT', 'MID', 'DEF', 'GK'].filter(a => H.key[a] || A.key[a]);
    const meetings = (h2h.recent || []).map(m =>
      `<div class="pv-h2h-row"><span>${(m.date || '').slice(0, 10)}</span>
        <b>${esc(m.home)}</b><span class="pv-h2h-sc">${m.home_goals}–${m.away_goals}</span><b>${esc(m.away)}</b></div>`).join('');

    out.innerHTML = `
      <section class="card pv-hero">
        <a class="pv-team" href="/team.html?name=${encodeURIComponent(H.name)}"><div class="pv-crest">${crest(H)}</div>
          <h3>${esc(H.name)}</h3><span>${esc(H.league || '')}${H.position ? ' · #' + H.position : ''}</span></a>
        <div class="pv-vs"><span>VS</span><small>${esc(H.league === A.league ? H.league : 'Match Preview')}</small></div>
        <a class="pv-team away" href="/team.html?name=${encodeURIComponent(A.name)}"><div class="pv-crest">${crest(A)}</div>
          <h3>${esc(A.name)}</h3><span>${esc(A.league || '')}${A.position ? ' · #' + A.position : ''}</span></a>
      </section>

      <section class="card pv-pred">
        <div class="card-h"><h3>Model projection</h3><span class="muted" style="font-size:12px">xG-based · home advantage applied</span></div>
        <div class="pv-bar"><i class="h" style="width:${p.home_win}%">${p.home_win}%</i><i class="d" style="width:${p.draw}%">${p.draw}%</i><i class="a" style="width:${p.away_win}%">${p.away_win}%</i></div>
        <div class="pv-bar-leg"><span><i class="dot h"></i>${esc(H.name)} win</span><span><i class="dot d"></i>Draw</span><span><i class="dot a"></i>${esc(A.name)} win</span></div>
        <div class="pv-score">Most likely scoreline <b>${esc(H.name)} ${p.scoreline.replace('-', '–')} ${esc(A.name)}</b> · expected goals <b>${p.xg_home} – ${p.xg_away}</b></div>
      </section>

      <section class="card pv-form">
        <div class="card-h"><h3>Form &amp; season profile</h3></div>
        <div class="pv-form-top">
          <div class="pv-form-pills">${(H.form || []).map(formPill).join('')}</div>
          <span class="pv-form-lbl">Last ${(H.form || []).length || 5}</span>
          <div class="pv-form-pills away">${(A.form || []).map(formPill).join('')}</div>
        </div>
        ${statRow('League position', H.position, A.position, x => x ? '#' + x : '—')}
        ${statRow('Points', H.points, A.points)}
        ${statRow('Goals scored', H.gf, A.gf)}
        ${statRow('Goals conceded', H.ga, A.ga)}
        ${statRow('xG / game', H.xgf_pg, A.xgf_pg, x => x == null ? '—' : x.toFixed(2))}
        ${statRow('xG conceded / game', H.xga_pg, A.xga_pg, x => x == null ? '—' : x.toFixed(2))}
        ${statRow('Recent xG (last 6)', H.rec_xgf, A.rec_xgf, x => x == null ? '—' : x.toFixed(2))}
      </section>

      <section class="card">
        <div class="card-h"><h3>Key matchups</h3></div>
        ${areas.length ? areas.map(a => keyRow(a, H.key[a], A.key[a])).join('') : '<div class="muted" style="padding:8px">No rated players available.</div>'}
      </section>

      <section class="card">
        <div class="card-h"><h3>Head-to-head</h3><span class="muted" style="font-size:12px">${h2h.played} meeting${h2h.played === 1 ? '' : 's'} in the dataset</span></div>
        ${h2h.played ? `<div class="pv-h2h-tally">
            <div class="pv-h2h-t"><b>${h2h.home_wins}</b><span>${esc(H.name)} wins</span></div>
            <div class="pv-h2h-t"><b>${h2h.draws}</b><span>Draws</span></div>
            <div class="pv-h2h-t"><b>${h2h.away_wins}</b><span>${esc(A.name)} wins</span></div>
          </div><div class="pv-h2h-list">${meetings}</div>`
          : '<div class="muted" style="padding:8px">No recent meetings in the dataset.</div>'}
      </section>`;
  }

  teamPicker('homeSearch', 'homeDD', home);
  teamPicker('awaySearch', 'awayDD', away);
  if (home && away) load();
  else document.getElementById('pv-out').innerHTML =
    '<section class="card"><div class="sr-empty"><div class="sr-empty-ic">⚖️</div><p>Choose a home and away team above to generate the preview.</p></div></section>';
})();
