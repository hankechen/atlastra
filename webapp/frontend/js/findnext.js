renderSidebar('Find the Next…');
attachSearchDropdown(document.getElementById('searchBox'));

let LEGENDS = [];
let current = null;
let radarChart = null;
const params = new URLSearchParams(location.search);

const fmtVal = (v) => v == null ? '—' : v >= 1e6 ? '€' + (v / 1e6).toFixed(0) + 'M' : '€' + Math.round(v / 1e3) + 'K';
const simColor = (s) => s >= 92 ? '#2fbf71' : s >= 85 ? '#7d9f3a' : s >= 75 ? '#c9a227' : '#c97a27';

function renderLegends() {
  document.getElementById('legends').innerHTML = LEGENDS.map(l =>
    `<button class="fn-leg ${current === l.key ? 'active' : ''}" data-k="${l.key}">
      <b>${l.name}</b><span>${l.pos}</span><small>${l.club} · ${l.era}</small>
    </button>`).join('');
  document.querySelectorAll('.fn-leg').forEach(b => b.onclick = () => select(b.dataset.k));
}

function ring(s) {
  return `<div class="fn-ring" style="background:conic-gradient(${simColor(s)} ${s * 3.6}deg, var(--border2) 0)">
    <span>${s}<small>%</small></span></div>`;
}

function matchRow(m, i) {
  const ph = m.photo ? `style="background-image:url('${m.photo}')"` : '';
  return `<a class="fn-match ${i === 0 ? 'top' : ''}" href="/player.html?name=${encodeURIComponent(m.player)}">
    <div class="fn-rank">${i + 1}</div>
    <div class="fn-mphoto" ${ph}></div>
    <div class="fn-minfo"><b>${m.player}</b><span>${m.position} · ${m.team || ''}</span></div>
    <div class="fn-mrat" title="Atlastra rating">${m.rating ?? '—'}</div>
    <div class="fn-mval">${fmtVal(m.market_value_eur)}</div>
    ${ring(m.similarity)}
  </a>`;
}

function drawRadar(axesOrder, legend, match) {
  const el = document.getElementById('fnRadar');
  if (!el) return;
  if (radarChart) radarChart.destroy();
  const lvals = axesOrder.map(ax => (legend.axes.find(a => a.axis === ax) || {}).value || 0);
  const mvals = axesOrder.map(ax => (match.axes.find(a => a.axis === ax) || {}).value || 0);
  radarChart = new Chart(el, {
    type: 'radar',
    data: {
      labels: axesOrder,
      datasets: [
        { label: legend.name, data: lvals, borderColor: '#7d5cf5', backgroundColor: 'rgba(125,92,245,.18)', borderWidth: 2, pointRadius: 2 },
        { label: match.player, data: mvals, borderColor: '#2fbf71', backgroundColor: 'rgba(47,191,113,.16)', borderWidth: 2, pointRadius: 2 },
      ],
    },
    options: {
      plugins: { legend: { labels: { color: '#cfd6e6', boxWidth: 12, font: { size: 11 } } } },
      scales: { r: {
        min: 0, max: 100, ticks: { display: false, stepSize: 25 },
        grid: { color: 'rgba(255,255,255,.08)' }, angleLines: { color: 'rgba(255,255,255,.08)' },
        pointLabels: { color: '#9aa3b8', font: { size: 10 } } } },
    },
  });
}

async function select(key) {
  current = key;
  renderLegends();
  history.replaceState(null, '', '?legend=' + key);
  const out = document.getElementById('fn-out');
  out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Scanning active players…</p></div></section>`;
  let d;
  try { d = await api('/api/find_next?legend=' + encodeURIComponent(key)); }
  catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not run the match.</p></div></section>'; return; }
  if (!d.available || !d.matches.length) {
    out.innerHTML = `<section class="card"><div class="sr-empty"><p>${(d.error || 'No matches found.').replace(/</g, '&lt;')}</p></div></section>`;
    return;
  }
  const lg = d.legend, top = d.matches[0];
  out.innerHTML = `
    <section class="card fn-hero">
      <div class="fn-hero-l">
        <div class="fn-hero-kick">The new ${lg.name}</div>
        <div class="fn-hero-name">${top.player}</div>
        <div class="fn-hero-meta">${top.position} · ${top.team || ''} · ${top.similarity}% style match</div>
        <p class="fn-hero-blurb">${lg.name} (${lg.club}, ${lg.era}) — ${lg.blurb}</p>
      </div>
      <div class="fn-hero-r"><canvas id="fnRadar" height="230"></canvas></div>
    </section>
    <section class="card"><div class="card-h"><h3>Closest active matches to ${lg.name}</h3>
      <span class="muted" style="font-size:12px">by statistical style · this season</span></div>
      <div class="fn-matches">${d.matches.map(matchRow).join('')}</div></section>`;
  drawRadar(d.axes_order, lg, top);
}

(async function init() {
  LEGENDS = await api('/api/legends');
  renderLegends();
  const want = params.get('legend');
  select(want && LEGENDS.some(l => l.key === want) ? want : LEGENDS[0].key);
})();
