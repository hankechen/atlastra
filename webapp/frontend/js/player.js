renderSidebar('Players');
Chart.defaults.color = '#7f8aa3';
Chart.defaults.font.family = 'Inter';
let radarChart, careerChart;

const TILE_DEFS = [
  ['👕', 'apps', 'Apps'], ['⚽', 'goals', 'Goals / 90'], ['🅰', 'assists', 'Assists / 90'],
  ['◎', 'xg', 'xG / 90'], ['⚲', 'xa', 'xA / 90'], ['💡', 'chances_created', 'Chances / 90'],
  ['★', 'big_chances_created', 'Big Chances / 90'], ['⚡', 'dribbles_per90', 'Dribbles / 90'],
  ['◉', 'pass_accuracy', 'Pass Accuracy'],
];
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
  document.getElementById('pteam').textContent = p.team || '';
  document.getElementById('ppos').textContent = p.detailed_position || p.position_group;
  document.getElementById('page').textContent = p.age ?? '—';
  document.getElementById('pnat').textContent =
    (p.country_code ? flagEmoji(p.country_code) + ' ' : '') + (p.nationality || '—');
  document.getElementById('pmv').textContent = eurM(p.market_value_eur);

  // dual ratings (League + UCL, common-metric)
  const lg = p.ratings?.league, ucl = p.ratings?.ucl;
  document.getElementById('rLeague').textContent = lg?.rating ?? '—';
  document.getElementById('cLeague').textContent = lg ? lg.classification : 'not rated';
  drawGauge('gaugeLeague', lg?.rating);
  document.getElementById('rUcl').textContent = ucl?.rating ?? '—';
  document.getElementById('cUcl').textContent = ucl ? ucl.classification : 'no UCL minutes';
  drawGauge('gaugeUcl', ucl?.rating);

  // stat tiles (combined: domestic + UCL)
  document.getElementById('tiles').innerHTML = TILE_DEFS.map(([ic, k, lab]) => {
    let v = p.tiles[k]; v = v == null ? '—' : (k === 'pass_accuracy' ? v + '%' : v);
    return `<div class="tile"><div class="ic">${ic}</div><b>${v}</b><span>${lab}</span></div>`;
  }).join('');

  // strengths / weaknesses
  document.getElementById('strengths').innerHTML = p.strengths.map(s => `<li class="ok">✔ ${s}</li>`).join('') || '<li class="muted">—</li>';
  document.getElementById('weaknesses').innerHTML = p.weaknesses.map(s => `<li class="bad">✘ ${s}</li>`).join('') || '<li class="muted">—</li>';

  // play style + technique placeholders by position
  document.getElementById('playstyle').innerHTML = (PLAYSTYLE[p.position_group] || []).map(s => `<span class="chip">${s}</span>`).join('');
  document.getElementById('tech').innerHTML = TECH.map(([n, pc], i) =>
    `<div class="t"><span class="rk">${i + 1}</span><span style="width:140px">${n}</span><span class="bar"><i style="width:${pc * 4}%"></i></span><b>${pc}%</b></div>`).join('');

  drawRadar(p.radar);
  drawCareer(p.career, careerStat);
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
  document.getElementById('top10').innerHTML = ranks.map(p => `<div class="prow">
    <span class="rk">${p.rank}</span><span class="nm" style="flex:1">${p.player}</span>
    <span class="flag"></span><b style="color:var(--accent2)">${p.rating}</b></div>`).join('');
})();
