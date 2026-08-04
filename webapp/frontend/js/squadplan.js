renderSidebar('Squad Planner');
attachSearchDropdown(document.getElementById('searchBox'));

(function () {
  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const sgn = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(1);
  const eurM = (e) => '€' + (e / 1e6).toFixed(e >= 1e8 ? 0 : 1) + 'm';
  const qs = new URLSearchParams(location.search);

  // A unit's health is one number with a direction: how far below a strong club's
  // level its best option is, now and at the end of the horizon. Colour follows
  // severity, which is a status scale, not a series identity.
  function severity(gap) {
    if (gap <= 0) return 'ok';
    if (gap <= 5) return 'warn';
    return 'bad';
  }

  function playerRow(p) {
    const ph = p.photo ? `<img src="${p.photo}" alt="" onerror="this.remove()">` : '';
    // a covering player shows his own rating; the discount he is counted at in
    // THIS unit sits in the tooltip, so the number never misstates the player
    const cov = p.cover_from
      ? `<span class="sp-cov" title="A ${esc(p.cover_from)} covering here — counts as ${p.eff_rating.toFixed(0)} in this unit">${esc(p.cover_from)}</span>`
      : '';
    const proj = p.projected != null
      ? `<span class="sp-proj">${p.rating} → <b>${p.projected.toFixed(1)}</b></span>`
      : `<span class="sp-proj">${p.rating}</span>`;
    const vd = p.verdict && !p.cover_from
      ? `<span class="fin-badge ${p.verdict_class} sp-vd">${esc(p.verdict)}</span>` : '';
    return `<a class="vf-row" href="/player.html?name=${encodeURIComponent(p.player)}">
      <span class="vf-ph">${ph}<span class="ini">${initials(p.player)}</span></span>
      <span class="vf-id"><b>${esc(p.player)} ${cov}</b>
        <span>${p.age != null ? Math.round(p.age) + ' yrs' : ''}${p.minutes ? ' · ' + p.minutes + ' mins' : ''}</span></span>
      ${vd}${proj}
    </a>`;
  }

  function targetRow(t, i) {
    const ph = t.photo ? `<img src="${t.photo}" alt="" onerror="this.remove()">` : '';
    const val = t.value_eur ? eurM(t.value_eur)
      : '<span class="muted" title="Only the ~500 most valuable players are priced">—</span>';
    return `<a class="vf-row" href="/player.html?name=${encodeURIComponent(t.player)}">
      <span class="vf-rank">${i + 1}</span>
      <span class="vf-ph">${ph}<span class="ini">${initials(t.player)}</span></span>
      <span class="vf-id"><b>${esc(t.player)}</b>
        <span>${esc(t.team)}${t.age != null ? ' · ' + Math.round(t.age) : ''}</span></span>
      <span class="sp-proj">${t.rating} → <b>${t.projected.toFixed(1)}</b></span>
      <span class="sp-val">${val}</span>
    </a>`;
  }

  function unitCard(u, horizon, targets) {
    const sev = severity(u.gap_horizon);
    const bars = (() => {
      if (u.best_now == null) return '';
      // one scale for all three bars so the shrink is readable as a shrink
      const scale = Math.max(u.benchmark, u.best_now, u.best_next || 0, u.best_horizon || 0) || 1;
      const bar = (v, lbl, cls) => v == null ? '' : `<div class="sp-bar-row">
        <label>${lbl}<b>${v.toFixed ? v.toFixed(1) : v}</b></label>
        <div class="sp-bar"><i class="${cls}" style="width:${Math.max(2, v / scale * 100)}%"></i></div></div>`;
      return `<div class="sp-bars">
        ${bar(u.benchmark, 'A strong club has', 'bench')}
        ${bar(u.best_now, 'Best now', 'now')}
        ${bar(u.best_horizon, `Best in ${horizon} seasons`, sev)}
      </div>`;
    })();
    const tlist = (targets || []).length ? `
      <h5 class="val-dh" style="margin-top:14px">Who would raise it</h5>
      ${targets.map(targetRow).join('')}` : '';
    return `<section class="card sp-unit">
      <div class="card-h">
        <h3>${esc(u.label)}</h3>
        <span class="sp-gap ${sev}">${u.gap_horizon > 0
          ? sgn(-u.gap_horizon) + ' vs a strong club' : 'covered'}</span>
      </div>
      <div class="sp-meta">${u.depth} specialist${u.depth === 1 ? '' : 's'}${
        u.cover_depth ? ` · ${u.cover_depth} covering` : ''} · typical club has ${u.target_depth}${
        u.mean_age != null ? ` · minutes average ${Math.round(u.mean_age)} yrs` : ''}</div>
      ${bars}
      <ul class="sp-reasons">${u.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
      ${u.players.length ? `<h5 class="val-dh" style="margin-top:12px">In the squad</h5>
        ${u.players.slice(0, 6).map(playerRow).join('')}` : ''}
      ${tlist}
    </section>`;
  }

  async function load(team, horizon) {
    const out = document.getElementById('sp-out');
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Planning ${esc(team)}…</p></div></section>`;
    let d;
    try {
      d = await api(`/api/squad_plan?team=${encodeURIComponent(team)}&horizon=${horizon}`);
    } catch {
      out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not load the planner.</p></div></section>';
      return;
    }
    if (!d.available) {
      out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">🧭</div><p>${esc(d.error || 'No plan available.')}</p></div></section>`;
      return;
    }

    const needs = d.units.filter((u) => d.needs.includes(u.group));
    const rest = d.units.filter((u) => !d.needs.includes(u.group));
    const m = d.model;
    const note = m
      ? `Next season is the trajectory model's own projection (average error ${m.mae.toFixed(1)} rating
         points on ${m.n_test} held-out cases, against ${m.base_mae.toFixed(1)} for assuming no change).
         Everything beyond that is the <b>measured aging curve</b> for the position applied to each
         player — a cohort average, not a personal forecast, and it says nothing about who a club
         actually signs or sells. `
      : '';

    out.innerHTML = `
      <div class="sp-head">
        ${d.team_logo ? `<img class="sp-crest" src="${d.team_logo}" alt="" onerror="this.remove()">` : ''}
        <div><h3>${esc(d.team)}</h3><span class="muted">${esc(d.league)} · ${esc(d.season)}
          · looking ${d.horizon} seasons ahead</span></div>
      </div>
      <h4 class="sp-sect">Priorities</h4>
      <div class="tj-grid">${needs.map((u) => unitCard(u, d.horizon, d.targets[u.group])).join('')}</div>
      <h4 class="sp-sect">The rest of the squad</h4>
      <div class="tj-grid">${rest.map((u) => unitCard(u, d.horizon, null)).join('')}</div>
      <section class="card" id="ptCard" style="margin-top:16px;display:none">
        <div class="card-h"><h3>If nobody moves</h3>
          <span class="muted" style="font-size:12px" id="ptNote"></span></div>
        <div id="ptBody"></div>
      </section>
      <p class="bg-note">${note}Positions cover for one another — a defensive midfielder counts toward
        central midfield at a discount, and is labelled as covering rather than counted as a specialist.
        "A strong club has" is the 80th percentile of every top-5 club's best player in that position
        this season, so it is measured rather than chosen.</p>`;

    // Next season's table on the "nobody moves" assumption. Loaded after the plan
    // so it never delays it, with the club being planned highlighted.
    api(`/api/projected_table?league=${encodeURIComponent(d.league_key)}`).then((t) => {
      if (!t || !t.available) return;
      const rows = t.tables[d.league_key] || [];
      if (!rows.length) return;
      // Drift is negative for nearly every strong squad, because a percentile
      // rating mean-reverts. Shown against the league median rather than raw, or
      // every good club reads as falling apart.
      const sorted = rows.map((r) => r.strength_drift).sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)];
      document.getElementById('ptBody').innerHTML = `
        <p class="muted" style="font-size:12.5px;margin:0 0 10px">Where ${esc(d.league)} finishes in
          ${esc(t.target_label)} if every squad stays exactly as it is — the counterfactual worth
          having before deciding whether to sign anyone.</p>
        <div class="pt-table">${rows.map((r) => {
          const me = r.team === d.team;
          const mv = r.move > 0 ? `<span class="ok">▲${r.move}</span>`
            : r.move < 0 ? `<span class="bad">▼${-r.move}</span>` : '<span class="muted">–</span>';
          const rel = r.strength_drift - median;
          const dr = rel >= 1 ? 'ok' : rel <= -1 ? 'bad' : 'muted';
          return `<div class="pt-row${me ? ' me' : ''}">
            <span class="pt-pos">${r.pos}</span>
            <span class="pt-team">${esc(r.team)}</span>
            <span class="pt-mv">${mv}</span>
            <span class="pt-pts">${r.projected_points}</span>
            <span class="pt-dr ${dr}">${rel >= 0 ? '+' : ''}${rel.toFixed(1)}</span>
          </div>`;
        }).join('')}</div>
        <div class="muted" style="font-size:11.5px;margin-top:10px">Points come from this season's
          table and measured squad strength, fitted to real final tables: held out it lands within
          <b>${t.model.mae}</b> points against <b>${t.model.mae_persistence}</b> for assuming this
          season repeats — better, but not by much, so read a position as a range rather than a
          prediction. The last column is each squad's projected drift against the league median;
          a percentile rating mean-reverts, so drift only means something relative to everyone else.</div>`;
      document.getElementById('ptNote').textContent = t.target_label;
      document.getElementById('ptCard').style.display = '';
    }).catch(() => {});
  }

  async function boot() {
    const sel = document.getElementById('spTeam');
    const hz = document.getElementById('spHorizon');
    let teams = [];
    try { teams = await api('/api/team_options'); } catch { teams = []; }
    const start = qs.get('team') || 'Manchester United';
    sel.innerHTML = teams.map((t) =>
      `<option value="${esc(t.team)}"${t.team === start ? ' selected' : ''}>${esc(t.team)}</option>`).join('')
      || `<option>${esc(start)}</option>`;
    const go = () => {
      const u = new URL(location);
      u.searchParams.set('team', sel.value);
      history.replaceState(null, '', u);
      load(sel.value, hz.value);
    };
    sel.addEventListener('change', go);
    hz.addEventListener('change', go);
    load(sel.value || start, hz.value);
  }

  boot();
})();
