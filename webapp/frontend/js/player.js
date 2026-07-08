renderSidebar('Players');
Chart.defaults.color = '#7f8aa3';
Chart.defaults.borderColor = 'rgba(150,158,178,.22)';
Chart.defaults.font.family = 'Inter';
let radarChart, careerChart;
let curSeason = null;                                // selected season (raw code)
const setText = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };

// stat tiles read from a scope object {games,minutes,goals,...,pass_accuracy_pct}.
// kind: 'count' shown as-is, 'per90' = v/min*90, 'pct' = v%, 'dec' = 1 decimal total.
// A tile def is [icon,key,label,kind]; a nested [[def],[def]] renders one grouped
// (double-wide) tile — goals+xG, assists+xA and duels+duels% are paired.
const TOTAL_DEFS = [
  ['👕', 'games', 'Apps', 'count'],
  [['⚽', 'goals', 'Goals', 'count'], ['◎', 'xg', 'xG', 'dec']],
  [['🅰', 'assists', 'Assists', 'count'], ['⚲', 'xa', 'xA', 'dec']],
  ['💡', 'chances_created', 'Chances', 'count'],
  ['★', 'big_chances_created', 'Big Chances', 'count'],
  ['⚡', 'dribbles_completed', 'Dribbles', 'count'],
  [['💪', 'duels_won', 'Duels Won', 'count'], ['％', 'duels_won_pct', 'Duels %', 'pct']],
  ['🛡', 'tackles', 'Tackles', 'count'], ['✋', 'interceptions', 'Interceptions', 'count'],
  ['◉', 'pass_accuracy_pct', 'Pass Acc', 'pct'],
  [['↗', 'progressive_passes_total', 'Prog. Passes', 'count'],
   ['🏃', 'progressive_carries_total', 'Prog. Carries', 'count']],
];
const PER90_DEFS = [
  ['👕', 'games', 'Apps', 'count'],
  [['⚽', 'goals', 'Goals / 90', 'per90'], ['◎', 'xg', 'xG / 90', 'per90']],
  [['🅰', 'assists', 'Assists / 90', 'per90'], ['⚲', 'xa', 'xA / 90', 'per90']],
  ['💡', 'chances_created', 'Chances / 90', 'per90'],
  ['★', 'big_chances_created', 'Big Ch. / 90', 'per90'],
  ['⚡', 'dribbles_completed', 'Dribbles / 90', 'per90'],
  [['💪', 'duels_won', 'Duels / 90', 'per90'], ['％', 'duels_won_pct', 'Duels %', 'pct']],
  ['🛡', 'tackles', 'Tackles / 90', 'per90'], ['◉', 'pass_accuracy_pct', 'Pass Acc', 'pct'],
  [['↗', 'progressive_passes', 'Prog. Passes / 90', 'dec'],
   ['🏃', 'progressive_carries', 'Prog. Carries / 90', 'dec']],
];
const SCOPES = [['league', 'League'], ['ucl', 'UCL'], ['combined', 'Combined'], ['worldcup', 'World Cup']];
let statScopes = {}, scopeTotals = 'combined', scopePer90 = 'combined';
let tilePct = {};                                   // per-stat percentile vs position peers
let wcTilePct = {};                                 // per-stat percentile vs the WC field (WC scope)

function fmtTile(def, s) {
  const [, key, , kind] = def, v = s ? s[key] : null;
  if (v == null) return '—';
  if (kind === 'count') return Math.round(v).toLocaleString();
  if (kind === 'dec') return v.toFixed(1);
  if (kind === 'pct') return Math.round(v) + '%';
  const m = s.minutes || 0;                       // per90
  return m ? (v / m * 90).toFixed(2) : '—';
}
const pctColor = (p) => p >= 80 ? '#2fbf71' : p >= 60 ? '#7d9f3a' : p >= 40 ? '#c9a227' : '#c97a27';
const ordinal = (n) => { const v = n % 100, s = ['th', 'st', 'nd', 'rd']; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
// percentile bar + number shown under a stat (skip Apps — no peer percentile)
function pctBar(key, pctMap, peer) {
  const p = (pctMap || tilePct)[key];
  if (p == null || key === 'games') return '';
  return `<div class="tpctw" title="${ordinal(p)} percentile vs ${peer || 'same position across all top-5 leagues'} (100 = best in position)">
    <div class="tpct"><i style="width:${p}%;background:${pctColor(p)}"></i></div>
    <span class="tpctn" style="color:${pctColor(p)}">${ordinal(p)}</span></div>`;
}
// The WC scope ranks against the WORLD CUP field (wcTilePct); every other scope uses
// the top-5-league percentiles (tilePct).
const oneTile = (def, s, scope) =>
  `<div class="ic">${def[0]}</div><b>${fmtTile(def, s)}</b><span>${def[2]}</span>${
    scope === 'worldcup'
      ? pctBar(def[1], wcTilePct, 'the World Cup field, same position')
      : pctBar(def[1], tilePct)}`;

function renderTiles(elId, defs, scope) {
  const s = statScopes[scope];
  document.getElementById(elId).innerHTML = defs.map(d => {
    if (Array.isArray(d[0])) {                      // grouped (double-wide) tile
      return `<div class="tile pair">${d.map(sub => `<div class="tsub">${oneTile(sub, s, scope)}</div>`).join('')}</div>`;
    }
    return `<div class="tile">${oneTile(d, s, scope)}</div>`;
  }).join('');
}
// Build a League/UCL/Combined toggle once; a delegated listener on the container
// survives tile re-renders, and we only flip the .active class + redraw on click.
function setupScopeTog(togId, tilesId, defs, getScope, setScope) {
  const tog = document.getElementById(togId);
  tog.innerHTML = SCOPES.map(([k, lab]) =>
    `<button class="sct" data-k="${k}"${statScopes[k] ? '' : ' disabled title="no minutes"'}>${lab}</button>`).join('');
  const update = () => {
    const sc = getScope();
    tog.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.k === sc));
    renderTiles(tilesId, defs, sc);
  };
  tog.onclick = (e) => {
    const b = e.target.closest('button');
    if (b && !b.disabled) { setScope(b.dataset.k); update(); }
  };
  update();
}
const PLAYSTYLE = { MID: ['Deep-Lying Playmaker', 'Progressive Passer', 'Press Resistant', 'Tempo Controller', 'Space Creator'],
  FWD: ['Advanced Forward', 'Poacher', 'Pressing Forward', 'Box Threat'],
  DEF: ['Ball-Playing Defender', 'Stopper', 'Aerial Dominator', 'Progressive Carrier'], GK: ['Sweeper Keeper', 'Shot Stopper'] };
const TECH = [['La Pausa', 24], ['Body Feint', 18], ['Outside Foot Pass', 15], ['Third-Man Combination', 12], ['Half Turn', 9]];

function drawGauge(canvasId, rating, w = 124, h = 78) {
  const c = document.getElementById(canvasId);
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h - 8, rad = w * 0.42;
  const frac = rating ? Math.max(0, Math.min(1, rating / 99)) : 0;
  const g = ctx.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, '#5570f0'); g.addColorStop(1, '#7d5cf5');
  for (const [col, a0, a1] of [['rgba(150,158,178,.22)', Math.PI, 2 * Math.PI], [g, Math.PI, Math.PI + Math.PI * frac]]) {
    ctx.beginPath(); ctx.lineWidth = Math.round(w * 0.073); ctx.lineCap = 'round';
    ctx.strokeStyle = col; ctx.arc(cx, cy, rad, a0, a1); ctx.stroke();
  }
}

async function load(name, careerStat = 'xa', season = null) {
  let url = '/api/player?name=' + encodeURIComponent(name) + '&career_stat=' + careerStat;
  if (season) url += '&season=' + encodeURIComponent(season);
  const p = await api(url);
  if (!p.name) { document.getElementById('crumb').textContent = 'not found'; return; }
  document.getElementById('crumb').textContent = p.name;
  document.getElementById('pname').innerHTML = p.name + ' <span class="verified">✔</span>';

  // fan comment thread (mount once per page; keyed by canonical player name)
  if (window.mountComments && !window._cmtsMounted) {
    window._cmtsMounted = true;
    mountComments('player:' + p.name, document.getElementById('comments'),
      { title: 'Fan Comments', subject: p.name });
  }

  // season selector + pinned-analysis labelling. The stat tiles, League/UCL
  // gauges and avg rating follow the chosen season; the radar / SWOT / archetype
  // / signature actions / heatmap only exist for the pinned (latest) season.
  curSeason = p.season;
  const seasons = p.seasons || [];
  const selLabel = (seasons.find(s => s.value === p.season) || {}).label || '';
  document.getElementById('seasonSel').innerHTML = seasons.map(s =>
    `<option value="${s.value}"${s.value === p.season ? ' selected' : ''}>${s.label}</option>`).join('');
  const banner = document.getElementById('pinnedBanner');
  // hist_level: what the radar/SWOT/heatmap reflect for the chosen season —
  // 'current' (full datamb), 'reduced' (per-season, Understat+FotMob), 'none'.
  if (p.hist_level === 'current') {
    banner.hidden = true;
    setText('radarNote', 'Compared to same position in Top-5 leagues · ' + selLabel);
    setText('simNote', 'By statistical profile · ' + selLabel);
    setText('ratingNote', 'Common-metric rating · combined stats below');
  } else {
    banner.hidden = false;
    setText('ratingNote', 'Common-metric rating · ' + selLabel);
    setText('simNote', 'By statistical profile · ' + p.pinned_season + ' (latest)');
    if (p.hist_level === 'reduced') {
      banner.innerHTML = `Showing <b>${selLabel}</b>. Stats, League/UCL ratings, radar, ` +
        `strengths &amp; weaknesses and heatmap are for this season (radar uses a reduced ` +
        `metric set). Composite rating, archetype &amp; signature actions reflect ` +
        `<b>${p.pinned_season}</b> (latest).`;
      setText('radarNote', 'Same position · ' + selLabel + ' · reduced metric set');
    } else {                                   // 'none' (pre-2020/21)
      banner.innerHTML = `Showing <b>${selLabel}</b> statistics &amp; League/UCL ratings. ` +
        `Radar, strengths/weaknesses &amp; heatmap aren't available this far back; ` +
        `archetype &amp; signature actions reflect <b>${p.pinned_season}</b> (latest).`;
      setText('radarNote', 'Not available for ' + selLabel);
    }
  }
  const photoEl = document.querySelector('.ph .photo');
  if (photoEl) photoEl.innerHTML = avatarHTML(p.photo, p.name);
  const credEl = document.getElementById('photoCredit');
  if (credEl) {
    const c = p.photo_credit;
    credEl.innerHTML = c
      ? `<a href="${c.page || '#'}" target="_blank" rel="noopener" title="${c.credit || ''} — ${c.license || ''}">📷 ${c.credit || 'Wikimedia'}${c.license ? ' · ' + c.license : ''}</a>`
      : '';
  }
  document.getElementById('pteam').innerHTML = crestHTML(p.team_logo, 'crest-sm') + (p.team || '');
  document.getElementById('ppos').textContent = p.detailed_position || p.position_group;
  document.getElementById('page').textContent = p.age ?? '—';
  document.getElementById('pnat').textContent =
    (p.country_code ? flagEmoji(p.country_code) + ' ' : '') + (p.nationality || '—');
  document.getElementById('pmv').textContent = eurM(p.market_value_eur);
  const av = document.getElementById('pavg');
  av.textContent = p.avg_rating == null ? '—' : (+p.avg_rating).toFixed(1);
  av.style.color = p.avg_rating == null ? '' : ratingColor(p.avg_rating);
  document.getElementById('compareLink').href = '/compare.html?name=' + encodeURIComponent(p.name);
  document.getElementById('scoutLink').href = '/scoutreport.html?name=' + encodeURIComponent(p.name) +
    (curSeason ? '&season=' + curSeason : '');
  document.getElementById('cardLink').href = '/card.html?name=' + encodeURIComponent(p.name);

  // follow / watchlist (localStorage via Store)
  const item = { id: p.name, name: p.name, team: p.team,
    position: p.detailed_position || p.position_group, rating: p.rating, photo: p.photo };
  const fb = document.getElementById('followBtn'), wb = document.getElementById('watchBtn');
  const syncF = () => { const on = Store.has('players', p.name); fb.classList.toggle('on', on); fb.textContent = on ? '✓ Following' : '★ Follow'; };
  const syncW = () => { const on = Store.has('watchlist', p.name); wb.classList.toggle('on', on); wb.textContent = on ? '🔖 On watchlist' : '🔖 Watch'; };
  fb.onclick = () => { Store.toggle('players', item); syncF(); };
  wb.onclick = () => { Store.toggle('watchlist', item); syncW(); };
  syncF(); syncW();
  wireFavBtn(document.getElementById('favBtn'), 'favPlayers', { name: p.name, photo: p.photo });

  // Big Game Index card (only if match-log data is available for this player)
  api('/api/big_game?name=' + encodeURIComponent(p.name)).then((b) => {
    if (!b || !b.available) return;
    const badge = b.badge === 'Big-Game Player' ? '<span class="bgp-badge big">⭐ Big-Game Player</span>'
      : b.badge === 'Flat-Track Bully' ? '<span class="bgp-badge bully">🛑 Flat-Track Bully</span>'
        : '<span class="bgp-badge neutral">Consistent across opposition</span>';
    const MAX = Math.max(0.4, b.big.ga90, b.weak.ga90);
    const bar = (v, cls) => `<div class="bgp-bar"><i class="${cls}" style="width:${Math.min(100, v / MAX * 100)}%"></i></div>`;
    document.getElementById('bigGame').innerHTML = `<div class="bgp">${badge}
      <div class="bgp-split">
        <div class="bgp-row"><label>vs Top-half · ${b.big.apps} apps <b>${b.big.ga90.toFixed(2)} G+A/90</b></label>${bar(b.big.ga90, 'big')}</div>
        <div class="bgp-row"><label>vs Bottom-half · ${b.weak.apps} apps <b>${b.weak.ga90.toFixed(2)} G+A/90</b></label>${bar(b.weak.ga90, 'weak')}</div>
      </div></div>`;
    document.getElementById('bigGameCard').style.display = '';
  }).catch(() => {});

  // dual ratings (League + UCL, common-metric)
  const lg = p.ratings?.league, ucl = p.ratings?.ucl;
  document.getElementById('rLeague').textContent = lg?.rating ?? '—';
  document.getElementById('cLeague').textContent = lg ? lg.classification : 'not rated';
  drawGauge('gaugeLeague', lg?.rating);
  document.getElementById('rUcl').textContent = ucl?.rating ?? '—';
  document.getElementById('cUcl').textContent = ucl ? ucl.classification : 'no UCL minutes';
  drawGauge('gaugeUcl', ucl?.rating);
  // World Cup gauge — only for seasons that had a World Cup the player featured in
  const wc = p.ratings?.worldcup, wcBox = document.getElementById('rgaugeWc');
  if (wc) {
    wcBox.style.display = '';
    document.getElementById('rWc').textContent = wc.rating;
    document.getElementById('cWc').textContent = `${wc.classification} · ${wc.apps} app${wc.apps === 1 ? '' : 's'}`;
    drawGauge('gaugeWc', wc.rating);
  } else { wcBox.style.display = 'none'; }

  // total + per-90 stat tiles, each with its own League/UCL/Combined scope toggle
  statScopes = p.stats_scopes || {};
  tilePct = p.tile_pct || {};
  wcTilePct = p.wc_tile_pct || {};
  const dflt = statScopes.combined ? 'combined' : Object.keys(statScopes)[0];
  if (!statScopes[scopeTotals]) scopeTotals = dflt;
  if (!statScopes[scopePer90]) scopePer90 = dflt;
  setupScopeTog('togTotals', 'totalTiles', TOTAL_DEFS, () => scopeTotals, k => { scopeTotals = k; });
  setupScopeTog('togPer90', 'tiles', PER90_DEFS, () => scopePer90, k => { scopePer90 = k; });

  // strengths / weaknesses
  document.getElementById('strengths').innerHTML = p.strengths.map(s => `<li class="ok">✔ ${s}</li>`).join('') || '<li class="muted">—</li>';
  document.getElementById('weaknesses').innerHTML = p.weaknesses.map(s => `<li class="bad">✘ ${s}</li>`).join('') || '<li class="muted">—</li>';

  // archetype + similar players (use case 10)
  renderArchetype(p.archetype);

  // signature actions (use case 9): real, from the player's standout per-90 actions
  document.getElementById('tech').innerHTML = (p.signature_actions || []).map((a, i) =>
    `<div class="t"><span class="rk">${i + 1}</span><span class="tn" style="width:150px">${a.name}</span>
      <span class="bar" title="${a.percentile}th percentile"><i style="width:${a.percentile}%"></i></span>
      <b>${a.value}<span class="per90">/90</span></b></div>`).join('')
    || '<div class="muted">Not enough on-ball data.</div>';

  drawRadar(p.radar);
  drawCareer(p.career, careerStat);
  drawHeatmap(p.heatmap);
  renderWorldCups(p.worldcups, p.country_code);
}

// World Cup record card: one row per edition the player featured in (newest first).
// Hidden entirely when the player has no World Cup appearances.
function renderWorldCups(wcs, cc) {
  const card = document.getElementById('worldCupCard');
  if (!wcs || !wcs.length) { card.style.display = 'none'; return; }
  card.style.display = '';
  const flag = flagEmoji(cc) || '';   // country_code is a FIFA 3-letter code (ESP/NOR/…)
  const cell = (v, l) => `<div class="wc-s"><b>${v == null ? '—' : v}</b><span>${l}</span></div>`;
  document.getElementById('worldCups').innerHTML = wcs.map(w => `
    <div class="wc-row">
      <div class="wc-ed"><span class="wc-yr">${w.edition}</span><span class="wc-lbl">World Cup</span></div>
      <div class="wc-team">${flag} <span>${w.team || ''}</span><small>${w.position || ''}</small></div>
      <div class="wc-stats">
        ${cell(w.apps, 'Apps')}${cell(w.minutes != null ? w.minutes + "'" : null, 'Minutes')}
        ${cell(w.goals, 'Goals')}${cell(w.assists, 'Assists')}
        ${cell(w.sofa_rating != null ? w.sofa_rating.toFixed(2) : null, 'Avg Rating')}
      </div>
      <div class="wc-atlas" style="--wc-c:${pctColor(w.atlas_rating || 0)}">
        <b>${w.atlas_rating == null ? '—' : w.atlas_rating}</b><span>${w.atlas_class || ''}</span></div>
    </div>`).join('');
}

// SofaScore season heatmap: blurred density over a pitch (attacks left -> right).
// Conventional football scale: faint green (low) -> yellow -> orange -> red (high),
// transparent at the very low end so the pitch shows through (no blue wash).
function heatColor(v) {
  v = Math.min(1, v);
  const t = Math.min(1, v * 1.4);                     // saturate toward red faster
  const hue = 145 - 145 * t;                          // 145 green -> 0 red
  const light = 50 + 12 * t;                          // brighter at the hot end
  const alpha = Math.max(0, Math.min(0.95, (v - 0.03) * 1.7));
  return `hsla(${hue}, 100%, ${light}%, ${alpha})`;
}
function drawPitch(ctx, W, H) {
  ctx.strokeStyle = 'rgba(150,158,178,.38)'; ctx.lineWidth = 1.5;
  ctx.strokeRect(2, 2, W - 4, H - 4);
  ctx.beginPath(); ctx.moveTo(W / 2, 2); ctx.lineTo(W / 2, H - 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.min(W, H) * 0.13, 0, 2 * Math.PI); ctx.stroke();
  const bw = W * 0.15, bh = H * 0.55;
  ctx.strokeRect(2, (H - bh) / 2, bw, bh); ctx.strokeRect(W - 2 - bw, (H - bh) / 2, bw, bh);
}
function drawHeatmap(grid) {
  const c = document.getElementById('heat'); if (!c) return;
  const ctx = c.getContext('2d'), W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0e1f17'; ctx.fillRect(0, 0, W, H);
  const note = document.getElementById('heatNote');
  if (!grid || !grid.length) {
    if (note) note.textContent = '';
    drawPitch(ctx, W, H);
    ctx.fillStyle = '#7f8aa3'; ctx.font = '13px Inter'; ctx.textAlign = 'center';
    ctx.fillText('No heatmap data', W / 2, H / 2 + 4); ctx.textAlign = 'left';
    return;
  }
  if (note) note.textContent = 'Domestic league · this season';
  const GH = grid.length, GW = grid[0].length, cw = W / GW, ch = H / GH;
  ctx.save(); ctx.filter = 'blur(10px)';
  for (let r = 0; r < GH; r++) for (let col = 0; col < GW; col++) {
    const v = grid[r][col];
    // SofaScore width axis: low-y = player's RIGHT side. Mirror rows so the
    // right flank renders at the bottom on a left->right attacking pitch.
    if (v > 0.02) { ctx.fillStyle = heatColor(v); ctx.fillRect(col * cw, (GH - 1 - r) * ch, cw + 1.5, ch + 1.5); }
  }
  ctx.restore();
  drawPitch(ctx, W, H);
}

function renderArchetype(a) {
  const el = document.getElementById('archetype'), sim = document.getElementById('similar');
  if (!a || !a.archetype) {
    el.innerHTML = '<div class="muted">Not enough data to classify.</div>';
    sim.innerHTML = ''; document.getElementById('archMore').style.display = 'none';
    return;
  }
  document.getElementById('archMore').href = '/archetypes.html?role=' + encodeURIComponent(a.archetype);
  el.innerHTML = `
    <div class="arch-head"><div class="arch-name">${a.archetype}<span class="arch-fit">${a.fit ?? '—'}% fit</span></div>
      <div class="arch-grp">${a.group_label}${a.archetype2 ? ` · also ${a.archetype2} (${a.fit2 ?? '—'}%)` : ''}</div></div>
    <p class="arch-blurb">${a.blurb || ''}</p>
    <div class="arch-traits">${(a.traits || []).map(t =>
      `<span class="trait">${t.label}<b>${t.pct}</b></span>`).join('') || '<span class="muted">—</span>'}</div>`;
  sim.innerHTML = (a.similar || []).map(s => `
    <div class="prow" onclick="location.href='${pHref(s.player)}'" style="cursor:pointer">
      <span class="pic">${avatarHTML(s.photo, s.player)}</span>
      <span style="flex:1"><div class="nm">${s.player}</div><div class="sub">${s.team || ''} · ${s.position || ''}</div></span>
      <span class="end"><span class="simpct">${s.similarity ?? ''}%</span>${s.rating != null ? `<b class="ratingchip sm">${s.rating}</b>` : ''}</span>
    </div>`).join('') || '<div class="muted">—</div>';
}

function drawRadar(radar) {
  if (radarChart) radarChart.destroy();
  const cv = document.getElementById('radar');
  if (!radar || !radar.length) {                  // no radar for this season (pre-2020/21)
    cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
    return;
  }
  const labels = radar.map(r => r.axis);
  const data = radar.map(r => r.value ?? 50);     // axis not measured for this position -> neutral
  radarChart = new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: { labels, datasets: [{ data, fill: true, backgroundColor: 'rgba(85,112,240,.35)',
      borderColor: '#7d5cf5', pointBackgroundColor: '#7d5cf5', pointRadius: 3 }] },
    options: { plugins: { legend: { display: false } }, scales: { r: {
      min: 0, max: 100, ticks: { display: false, stepSize: 25 },
      grid: { color: 'rgba(150,158,178,.22)' }, angleLines: { color: 'rgba(150,158,178,.22)' },
      pointLabels: { color: '#8a93a6', font: { size: 11 },
        callback: (l, i) => `${l}  ${data[i]}` } } } },
  });
}

function drawCareer(career, stat) {
  if (careerChart) careerChart.destroy();
  careerChart = new Chart(document.getElementById('career'), {
    type: 'line',
    data: { labels: career.map(c => c.season), datasets: [{ data: career.map(c => c.value),
      borderColor: '#5570f0', backgroundColor: 'rgba(85,112,240,.15)', fill: true, tension: .35,
      pointBackgroundColor: '#5570f0', pointRadius: 4 }] },
    options: { plugins: { legend: { display: false }, tooltip: { enabled: true } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: 'rgba(150,158,178,.22)' }, beginAtZero: true } } },
  });
}

// ---- boot ----
const params = new URLSearchParams(location.search);
let current = params.get('name') || 'Pedri';

// Back button: if we arrived from a match (the match page tags its profile links
// with from=match&eid=…), go straight back to that match; otherwise fall back to
// the browser's history when there is somewhere to return to.
(function () {
  const back = document.getElementById('backBtn');
  if (!back) return;
  if (params.get('from') === 'match' && params.get('eid')) {
    back.textContent = '← Back to match';
    back.href = '/match.html?id=' + encodeURIComponent(params.get('eid'));
    back.style.display = '';
  } else if (history.length > 1) {
    back.textContent = '← Back';
    back.href = '#';
    back.addEventListener('click', (e) => { e.preventDefault(); history.back(); });
    back.style.display = '';
  }
})();
const careerStatVal = () => document.getElementById('careerStat').value;
load(current, 'xa', params.get('season'));      // ?season=2324 deep-links a season
document.getElementById('careerStat').onchange = (e) => load(current, e.target.value, curSeason);
document.getElementById('seasonSel').onchange = (e) => load(current, careerStatVal(), e.target.value);
// topbar search = the global player/team/match dropdown (same as Home), not an
// Enter-to-reload-this-profile box, so search is consistent across the app.
attachSearchDropdown(document.getElementById('searchBox'));

