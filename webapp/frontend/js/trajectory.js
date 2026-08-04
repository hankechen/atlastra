renderSidebar('Risers & Fallers');
attachSearchDropdown(document.getElementById('searchBox'));

(function () {
  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(1);

  // one player row, mirroring the Value Finder board so the two read as a pair
  function row(r, i, mode) {
    const ph = r.photo ? `<img src="${r.photo}" alt="" onerror="this.remove()">` : '';
    const meta = [r.position, r.team, r.age ? Math.round(r.age) : null]
      .filter(Boolean).join(' · ');
    const right = mode === 'risk'
      ? `<span class="tj-delta ${r.p_present < 0.5 ? 'down' : ''}">${Math.round(r.p_present * 100)}%</span>`
      : `<span class="tj-delta ${r.delta >= 0 ? 'up' : 'down'}">${sign(r.delta)}</span>`;
    return `<a class="vf-row" href="/player.html?name=${encodeURIComponent(r.player)}">
      <span class="vf-rank">${i + 1}</span>
      <span class="vf-ph">${ph}<span class="ini">${initials(r.player)}</span></span>
      <span class="vf-id"><b>${esc(r.player)}</b><span>${esc(meta)}</span></span>
      <span class="vf-vals"><span class="a">${r.rating_now}</span> → <span class="p">${r.projected.toFixed(1)}</span></span>
      ${right}
    </a>`;
  }

  // The measured aging curve — data, not a projection.
  // Two series only, and they do different jobs: the all-players arc is a
  // recessive reference, the chosen position is the subject. Eight lines at once
  // — the first version of this chart — was unreadable spaghetti, and the story
  // is one shape anyway, so this highlights one and grays the baseline.
  const BASE_COLOUR = '#8a93a6';        // reference: deliberately gray
  const PICK_COLOUR = '#7d5cf5';        // subject (ΔE 19.4 protan vs the baseline)
  const GROUP_LABELS = {
    ALL: 'All players', CB: 'Centre-backs', FB: 'Full-backs', DM: 'Defensive mids',
    CM: 'Central mids', AM: 'Attacking mids', W: 'Wingers', ST: 'Strikers',
  };
  let curveState = null;

  // The stored curve is a per-season *change*; a reader wants the career arc, so
  // integrate it. Anchored at a shared start age, both lines begin at 0 and the
  // height at any age is "rating points gained or lost since then".
  function cumulative(pts, fromAge) {
    let acc = 0;
    return pts.filter((p) => p.age >= fromAge).map((p) => {
      const at = { age: p.age, y: acc, n: p.n };
      acc += p.delta;
      return at;
    }).concat((() => {
      const last = pts[pts.length - 1];
      return last && last.age >= fromAge ? [{ age: last.age + 1, y: acc, n: last.n }] : [];
    })());
  }

  function curveChart(curves, pick) {
    const base = curves.ALL || [];
    if (base.length < 6) return '';
    const sel = pick && pick !== 'ALL' && (curves[pick] || []).length >= 6 ? pick : null;
    const fromAge = Math.max(base[0].age, sel ? curves[sel][0].age : -99);
    const series = [{ key: 'ALL', pts: cumulative(base, fromAge), colour: BASE_COLOUR }];
    if (sel) series.push({ key: sel, pts: cumulative(curves[sel], fromAge), colour: PICK_COLOUR });

    const W = 720, H = 210, PL = 36, PR = 96, PT = 14, PB = 24;
    const flat = series.flatMap((s) => s.pts);
    const a0 = Math.min(...flat.map((p) => p.age)), a1 = Math.max(...flat.map((p) => p.age));
    const ys = flat.map((p) => p.y);
    const y0 = Math.min(...ys, 0) - 0.6, y1 = Math.max(...ys, 0) + 0.6;
    const sx = (a) => PL + (a - a0) / Math.max(1, a1 - a0) * (W - PL - PR);
    const sy = (v) => PT + (y1 - v) / Math.max(0.1, y1 - y0) * (H - PT - PB);

    const paths = series.map((s) => {
      const d = s.pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.age).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
      return `<path d="${d}" class="tj-cline" stroke="${s.colour}"
                    stroke-width="${s.key === 'ALL' && sel ? 2 : 2.5}"
                    ${s.key === 'ALL' && sel ? 'opacity="0.7"' : ''}/>`;
    }).join('');
    // peak marker: the age at which each arc stops climbing
    const peaks = series.map((s) => {
      const top = s.pts.reduce((a, b) => (b.y > a.y ? b : a), s.pts[0]);
      return `<circle cx="${sx(top.age).toFixed(1)}" cy="${sy(top.y).toFixed(1)}" r="3.5"
                      fill="${s.colour}" class="tj-cpeak"/>`;
    }).join('');

    // every label lives in HTML: the plot is stretched to the card width, which
    // would squash text drawn inside the SVG. Percentages come from the same scale.
    const xticks = [];
    for (let a = Math.ceil(a0 / 2) * 2; a <= a1; a += 2) {
      xticks.push(`<span style="left:${(sx(a) / W * 100).toFixed(2)}%">${a}</span>`);
    }
    const yticks = [y1, 0, y0].map((v) =>
      `<span style="top:${(sy(v) / H * 100).toFixed(2)}%">${v > 0 ? '+' : ''}${v.toFixed(1)}</span>`).join('');
    // direct labels at the end of each line, so identity is never colour alone
    const ends = series.map((s) => {
      const last = s.pts[s.pts.length - 1];
      return `<span class="tj-cend" style="left:${(sx(last.age) / W * 100).toFixed(2)}%;
                    top:${(sy(last.y) / H * 100).toFixed(2)}%;color:${s.colour}">${GROUP_LABELS[s.key] || s.key}</span>`;
    }).join('');

    curveState = { series, a0, a1, PL, PR, W };
    const chips = ['ALL', 'CB', 'FB', 'DM', 'CM', 'AM', 'W', 'ST']
      .filter((g) => g === 'ALL' || (curves[g] || []).length >= 6)
      .map((g) => `<button class="tj-chip${(sel || 'ALL') === g ? ' on' : ''}" data-g="${g}">
          ${g === 'ALL' ? 'All players' : g}</button>`).join('');

    return `<div class="tj-chips">${chips}</div>
      <div class="tj-cwrap" id="tjCurve">
        <svg class="tj-curve" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
             aria-label="Cumulative change in Atlastra rating by age, from age ${a0}">
          <line x1="${PL}" y1="${sy(0).toFixed(1)}" x2="${W - PR}" y2="${sy(0).toFixed(1)}" class="tj-czero"/>
          ${paths}${peaks}
          <line class="tj-cross" x1="0" y1="${PT}" x2="0" y2="${H - PB}" style="display:none"/>
        </svg>
        <div class="tj-cy">${yticks}</div>
        <div class="tj-cx">${xticks.join('')}</div>
        <div class="tj-cends">${ends}</div>
        <div class="tj-ctip" style="display:none"></div>
      </div>`;
  }

  // crosshair + readout. The SVG is stretched, so positions are read as fractions
  // of the wrapper rather than from SVG user units.
  function attachCurveHover() {
    const wrap = document.getElementById('tjCurve');
    if (!wrap || !curveState) return;
    const { series, a0, a1, PL, PR, W } = curveState;
    const svg = wrap.querySelector('svg');
    const cross = wrap.querySelector('.tj-cross');
    const tip = wrap.querySelector('.tj-ctip');
    const plotL = PL / W, plotR = (W - PR) / W;

    wrap.addEventListener('mousemove', (e) => {
      const r = wrap.getBoundingClientRect();
      const f = (e.clientX - r.left) / r.width;
      if (f < plotL - 0.02 || f > plotR + 0.02) { cross.style.display = 'none'; tip.style.display = 'none'; return; }
      const t = Math.min(1, Math.max(0, (f - plotL) / (plotR - plotL)));
      const age = Math.round(a0 + t * (a1 - a0));
      const rows = series.map((s) => {
        const p = s.pts.find((q) => q.age === age);
        if (!p) return '';
        return `<div><i style="background:${s.colour}"></i>${GROUP_LABELS[s.key] || s.key}
                <b>${p.y > 0 ? '+' : ''}${p.y.toFixed(1)}</b></div>`;
      }).join('');
      if (!rows) { cross.style.display = 'none'; tip.style.display = 'none'; return; }
      const px = (PL + (age - a0) / Math.max(1, a1 - a0) * (W - PL - PR));
      cross.setAttribute('x1', px); cross.setAttribute('x2', px);
      cross.style.display = '';
      tip.innerHTML = `<h6>Age ${age}</h6>${rows}`;
      tip.style.display = '';
      tip.style.left = `${Math.min(78, (px / W) * 100)}%`;
      svg.style.cursor = 'crosshair';
    });
    wrap.addEventListener('mouseleave', () => {
      cross.style.display = 'none'; tip.style.display = 'none';
    });
  }

  async function load() {
    const out = document.getElementById('tj-out');
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Projecting next season…</p></div></section>`;
    let d, c;
    try { [d, c] = await Promise.all([api('/api/trajectory_board?limit=15'), api('/api/aging_curves')]); }
    catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not load the model.</p></div></section>'; return; }
    if (!d.available) {
      out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">📈</div><p>${esc(d.error || 'Model not trained yet.')}</p></div></section>`;
      return;
    }

    const col = (title, sub, items, cls, mode) => `<section class="card">
      <div class="card-h"><h3 class="${cls || ''}">${title}</h3><span class="muted" style="font-size:12px">${sub}</span></div>
      ${items && items.length ? items.map((r, i) => row(r, i, mode)).join('')
        : '<div class="muted" style="padding:16px">None.</div>'}
    </section>`;

    const m = d.model;
    const tgt = m ? m.target_label : 'next season';
    const note = m
      ? `Gradient-boosting model trained on ${m.n_train} season-to-season transitions up to 2021/22, then scored
         blind on ${m.test_seasons} — ${m.n_test} projections it had never seen. Average error
         <b>${m.mae.toFixed(1)}</b> rating points against <b>${m.base_mae.toFixed(1)}</b> for the persistence
         baseline (assume no change at all), a ${m.skill_pct.toFixed(0)}% improvement, with the direction
         right ${Math.round(m.direction_acc * 100)}% of the time on players who actually moved. The
         availability model separates ${Math.round(m.avail_auc * 100) / 100} AUC. `
      : '';

    // The boards are made of big calls, so say how big calls have actually fared
    // rather than leaving the reader to assume. Both numbers come from the blind split.
    const sgn = (v) => (v > 0 ? '+' : '') + v.toFixed(1);
    const bigNote = m && m.big_up && (m.big_up.n || m.big_dn.n)
      ? `The two lists above are mostly the model correcting a season that sat far from a player's own
         multi-season level — so it is fair to ask whether those corrections land. On the held-out
         seasons it made ${m.big_up.n} rises and ${m.big_dn.n} falls of ${m.big_move.toFixed(0)} points
         or more: the rises averaged <b>${sgn(m.big_up.pred)}</b> predicted against
         <b>${sgn(m.big_up.real)}</b> observed, the falls <b>${sgn(m.big_dn.pred)}</b> against
         <b>${sgn(m.big_dn.real)}</b>. Big calls are not where this model is weakest; the crowded
         middle is. `
      : '';

    out.innerHTML = `
      <div class="tj-grid">
        ${col('📈 Biggest risers', `Projected to climb most by ${tgt}`, d.risers, 'ok')}
        ${col('📉 Biggest fallers', `Projected to slip most by ${tgt}`, d.fallers, 'bad')}
        ${col('🌱 Breakout candidates', 'Under 23, not yet elite, projected highest', d.breakouts, 'ok')}
        ${col('⚠️ Least secure', 'Least likely to still be a top-5 regular', d.at_risk, 'bad', 'risk')}
      </div>
      <section class="card" style="margin-top:16px" id="tjCurveCard">
        <div class="card-h"><h3>The aging curve</h3><span class="muted" style="font-size:12px">Measured, not modelled</span></div>
        <p class="muted" style="font-size:12.5px;margin:0 0 10px">Rating points gained or lost since the
          youngest age shown, accumulated season by season straight from the panel — so the peak of a line
          is the age a group stops gaining ground. Ratings are percentile-based, so this is movement
          <b>relative to peers</b>, not the age a player stops being good. Pick a position to compare it
          against everyone.</p>
        <div id="tjCurveHost">${c && c.available ? curveChart(c.curves, 'ALL') : '<div class="muted">No curve yet.</div>'}</div>
      </section>
      <p class="bg-note">${bigNote}${note}It projects the rating and nothing else: a transfer, a new manager,
        a serious injury or a change of role are all invisible to it, and all of them move careers.
        Goalkeepers are included, but their ratings swing harder than anyone's — 13 points a season on
        average against about 9 for a midfielder, because a keeper's save percentage is partly his
        defence's — so their ranges come out wider. Treat the error bar as the honest part of the number.</p>`;

    // chips swap which position is drawn against the all-players baseline
    if (c && c.available) {
      const host = document.getElementById('tjCurveHost');
      const draw = (g) => { host.innerHTML = curveChart(c.curves, g); attachCurveHover(); };
      host.addEventListener('click', (e) => {
        const b = e.target.closest('.tj-chip');
        if (b) draw(b.dataset.g);
      });
      attachCurveHover();
    }
  }

  load();
})();
