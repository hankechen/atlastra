renderSidebar('Players');
Chart.defaults.color = '#7f8aa3';
Chart.defaults.font.family = 'Inter';
let radarChart, careerChart;

// stat tiles read from a scope object {games,minutes,goals,...,pass_accuracy_pct}.
// kind: 'count' shown as-is, 'per90' = v/min*90, 'pct' = v%, 'dec' = 1 decimal total.
const TOTAL_DEFS = [
  ['👕', 'games', 'Apps', 'count'], ['⚽', 'goals', 'Goals', 'count'], ['🅰', 'assists', 'Assists', 'count'],
  ['◎', 'xg', 'xG', 'dec'], ['⚲', 'xa', 'xA', 'dec'], ['💡', 'chances_created', 'Chances', 'count'],
  ['★', 'big_chances_created', 'Big Chances', 'count'], ['⚡', 'dribbles_completed', 'Dribbles', 'count'],
  ['💪', 'duels_won', 'Duels Won', 'count'], ['％', 'duels_won_pct', 'Duels %', 'pct'],
  ['🛡', 'tackles', 'Tackles', 'count'], ['✋', 'interceptions', 'Interceptions', 'count'],
  ['◉', 'pass_accuracy_pct', 'Pass Acc', 'pct'],
];
const PER90_DEFS = [
  ['👕', 'games', 'Apps', 'count'], ['⚽', 'goals', 'Goals / 90', 'per90'], ['🅰', 'assists', 'Assists / 90', 'per90'],
  ['◎', 'xg', 'xG / 90', 'per90'], ['⚲', 'xa', 'xA / 90', 'per90'], ['💡', 'chances_created', 'Chances / 90', 'per90'],
  ['★', 'big_chances_created', 'Big Ch. / 90', 'per90'], ['⚡', 'dribbles_completed', 'Dribbles / 90', 'per90'],
  ['💪', 'duels_won', 'Duels / 90', 'per90'], ['％', 'duels_won_pct', 'Duels %', 'pct'],
  ['🛡', 'tackles', 'Tackles / 90', 'per90'], ['◉', 'pass_accuracy_pct', 'Pass Acc', 'pct'],
];
const SCOPES = [['league', 'League'], ['ucl', 'UCL'], ['combined', 'Combined']];
let statScopes = {}, scopeTotals = 'combined', scopePer90 = 'combined';

function fmtTile(def, s) {
  const [, key, , kind] = def, v = s ? s[key] : null;
  if (v == null) return '—';
  if (kind === 'count') return Math.round(v).toLocaleString();
  if (kind === 'dec') return v.toFixed(1);
  if (kind === 'pct') return Math.round(v) + '%';
  const m = s.minutes || 0;                       // per90
  return m ? (v / m * 90).toFixed(2) : '—';
}
function renderTiles(elId, defs, scope) {
  const s = statScopes[scope];
  document.getElementById(elId).innerHTML = defs.map(d =>
    `<div class="tile"><div class="ic">${d[0]}</div><b>${fmtTile(d, s)}</b><span>${d[2]}</span></div>`).join('');
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

function drawGauge(canvasId, rating, w = 150, h = 92) {
  const c = document.getElementById(canvasId);
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h - 10, rad = w * 0.42;
  const frac = rating ? Math.max(0, Math.min(1, rating / 99)) : 0;
  const g = ctx.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, '#5570f0'); g.addColorStop(1, '#7d5cf5');
  for (const [col, a0, a1] of [['#1b2236', Math.PI, 2 * Math.PI], [g, Math.PI, Math.PI + Math.PI * frac]]) {
    ctx.beginPath(); ctx.lineWidth = 11; ctx.lineCap = 'round';
    ctx.strokeStyle = col; ctx.arc(cx, cy, rad, a0, a1); ctx.stroke();
  }
}

async function load(name, careerStat = 'xa') {
  const p = await api('/api/player?name=' + encodeURIComponent(name) + '&career_stat=' + careerStat);
  if (!p.name) { document.getElementById('crumb').textContent = 'not found'; return; }
  document.getElementById('crumb').textContent = p.name;
  document.getElementById('pname').innerHTML = p.name + ' <span class="verified">✔</span>';
  const photoEl = document.querySelector('.ph .photo');
  if (photoEl) photoEl.innerHTML = avatarHTML(p.photo, p.name);
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

  // dual ratings (League + UCL, common-metric)
  const lg = p.ratings?.league, ucl = p.ratings?.ucl;
  document.getElementById('rLeague').textContent = lg?.rating ?? '—';
  document.getElementById('cLeague').textContent = lg ? lg.classification : 'not rated';
  drawGauge('gaugeLeague', lg?.rating);
  document.getElementById('rUcl').textContent = ucl?.rating ?? '—';
  document.getElementById('cUcl').textContent = ucl ? ucl.classification : 'no UCL minutes';
  drawGauge('gaugeUcl', ucl?.rating);

  // total + per-90 stat tiles, each with its own League/UCL/Combined scope toggle
  statScopes = p.stats_scopes || {};
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
}

// SofaScore season heatmap: blurred density over a pitch (attacks left -> right).
// Conventional football scale: faint green (low) -> yellow -> orange -> red (high),
// transparent at the very low end so the pitch shows through (no blue wash).
function heatColor(v) {
  v = Math.min(1, v);
  const hue = 145 - 145 * Math.min(1, v * 1.15);     // 145 green -> 0 red
  const alpha = Math.max(0, Math.min(0.82, (v - 0.04) * 1.15));
  return `hsla(${hue}, 85%, 50%, ${alpha})`;
}
function drawPitch(ctx, W, H) {
  ctx.strokeStyle = 'rgba(255,255,255,.18)'; ctx.lineWidth = 1.5;
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
    if (v > 0.02) { ctx.fillStyle = heatColor(v); ctx.fillRect(col * cw, r * ch, cw + 1.5, ch + 1.5); }
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
  const labels = radar.map(r => r.axis);
  const data = radar.map(r => r.value ?? 50);     // axis not measured for this position -> neutral
  if (radarChart) radarChart.destroy();
  radarChart = new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: { labels, datasets: [{ data, fill: true, backgroundColor: 'rgba(85,112,240,.35)',
      borderColor: '#7d5cf5', pointBackgroundColor: '#7d5cf5', pointRadius: 3 }] },
    options: { plugins: { legend: { display: false } }, scales: { r: {
      min: 0, max: 100, ticks: { display: false, stepSize: 25 },
      grid: { color: '#1b2236' }, angleLines: { color: '#1b2236' },
      pointLabels: { color: '#cdd4e6', font: { size: 11 },
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
      scales: { x: { grid: { display: false } }, y: { grid: { color: '#1b2236' }, beginAtZero: true } } },
  });
}

// ---- boot ----
const params = new URLSearchParams(location.search);
let current = params.get('name') || 'Pedri';
load(current);
document.getElementById('careerStat').onchange = (e) => load(current, e.target.value);
document.getElementById('searchBox').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) { current = e.target.value.trim(); load(current); }
});

// Atlastra Top 10 rail
(async () => {
  const ranks = await api('/api/rankings?limit=10');
  document.getElementById('top10').innerHTML = ranks.map(p => `<div class="prow" onclick="location.href='${pHref(p.player)}'">
    <span class="rk">${p.rank}</span><span class="pic">${avatarHTML(p.photo, p.player)}</span>
    <span class="nm" style="flex:1">${p.player}</span>
    <b style="color:var(--accent2)">${p.rating}</b></div>`).join('');
})();
