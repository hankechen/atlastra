renderSidebar('Compare');
Chart.defaults.color = '#7f8aa3';
Chart.defaults.font.family = 'Inter';

// per-player overlay colours (rgb so we can vary alpha)
const COLORS = [[85, 112, 240], [125, 92, 245], [46, 200, 150]];
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

// stats the user can pick instead of the position defaults
const STAT_OPTS = [
  ['goals', 'Goals'], ['assists', 'Assists'], ['ga_per90', 'G+A per 90'],
  ['xg', 'xG'], ['xa', 'xA'], ['chances_created', 'Chances Created'],
  ['big_chances_created', 'Big Chances Created'], ['dribbles_completed', 'Dribbles'],
  ['dribble_success_pct', 'Dribble %'], ['passes_completed', 'Passes Completed'],
  ['pass_accuracy_pct', 'Pass Accuracy %'], ['duels_won', 'Duels Won'],
  ['duels_won_pct', 'Duels Won %'], ['tackles', 'Tackles'],
  ['interceptions', 'Interceptions'], ['recoveries', 'Recoveries'],
];

const params = new URLSearchParams(location.search);
let names = params.getAll('name');
if (!names.length) names = ['Pedri', 'Jude Bellingham'];   // sensible demo default
let addedStats = params.getAll('stat');                     // user-chosen, ADDED on top of defaults
let radarChart;
const STAT_LABEL = Object.fromEntries(STAT_OPTS);

// "+ Add a stat" dropdown — each pick appends to the table (defaults always stay)
const picker = document.getElementById('statPicker');
function refreshPicker() {
  picker.innerHTML = '<option value="">+ Add a stat…</option>' +
    STAT_OPTS.filter(([k]) => !addedStats.includes(k))
      .map(([k, l]) => `<option value="${k}">${l}</option>`).join('');
  picker.value = '';
}
picker.onchange = () => {
  const k = picker.value;
  if (k && !addedStats.includes(k)) { addedStats.push(k); syncUrl(); render(); }
};
function removeStat(k) { addedStats = addedStats.filter(s => s !== k); syncUrl(); render(); }

function syncUrl() {
  const qs = names.map(n => 'name=' + encodeURIComponent(n))
    .concat(addedStats.map(s => 'stat=' + encodeURIComponent(s))).join('&');
  history.replaceState(null, '', '/compare.html' + (qs ? '?' + qs : ''));
}

function renderChips() {
  document.getElementById('chips').innerHTML = names.map((n, i) =>
    `<span class="cmp-chip" style="border-color:${rgba(COLORS[i % 3], .9)}">
       <span class="dot" style="background:${rgba(COLORS[i % 3], 1)}"></span>${n}
       <button data-i="${i}" title="Remove">✕</button></span>`).join('') +
    (names.length < 3 ? '<span class="cmp-hint">+ add up to 3 (search above)</span>' : '');
  document.querySelectorAll('.cmp-chip button').forEach(b => b.onclick = () => {
    names.splice(+b.dataset.i, 1); syncUrl(); render();
  });
}

function addPlayer(name) {
  name = name.trim();
  if (!name || names.length >= 3) return;
  if (names.some(n => n.toLowerCase() === name.toLowerCase())) return;
  names.push(name); syncUrl(); render();
}

async function render() {
  renderChips();
  refreshPicker();
  const board = document.getElementById('board'), empty = document.getElementById('empty');
  if (names.length < 2) {
    board.style.display = 'none'; empty.style.display = '';
    empty.textContent = 'Add at least two players to compare.';
    return;
  }
  const qs = names.map(n => 'name=' + encodeURIComponent(n))
    .concat(addedStats.map(s => 'stat=' + encodeURIComponent(s))).join('&');
  const d = await api('/api/compare?' + qs);
  document.getElementById('seasonNote').textContent = d.season ? `Season ${d.season}.` : '';

  if (!d.players || d.players.length < 2) {
    board.style.display = 'none'; empty.style.display = '';
    empty.textContent = 'Couldn’t resolve at least two of those players. Check the spelling.';
    return;
  }
  empty.style.display = 'none'; board.style.display = '';
  drawTable(d);
  drawRadar(d);
}

function drawTable(d) {
  const head = `<div class="cmp-row cmp-head"><div class="cmp-stat"></div>` +
    d.players.map((p, i) => `<div class="cmp-cell">
        <span class="cmp-photo pic" style="box-shadow:0 0 0 2px ${rgba(COLORS[i % 3], 1)}">${avatarHTML(p.photo, p.name)}</span>
        <div class="cmp-pname"><span class="dot" style="background:${rgba(COLORS[i % 3], 1)}"></span>${p.name}</div>
        <div class="cmp-sub">${(p.country_code ? flagEmoji(p.country_code) + ' ' : '')}${crestHTML(p.team_logo, 'crest-sm')}${p.team || ''} · ${p.position || ''}</div>
      </div>`).join('') + `</div>`;

  // a "header" stat block: rating, classification, market value
  const meta = [
    ['Rating', d.players.map(p => p.rating ?? '—'), true],
    ['Classification', d.players.map(p => p.classification ?? '—'), false],
    ['Market Value', d.players.map(p => eurM(p.market_value_eur)), false],
  ].map(([label, vals]) => statRow(label, vals.map(v => ({ v })), null)).join('');

  const row = (s) => statRow(
    s.label,
    s.values.map(v => ({ v: v == null ? '—' : v })),
    s.best_index,
    s.added ? s.key : null);

  const defaults = d.stats.filter(s => !s.added).map(row).join('');
  const extras = d.stats.filter(s => s.added);
  const extraHtml = extras.length
    ? `<div class="cmp-group"><div class="cmp-grouplabel">Added stats</div>${extras.map(row).join('')}</div>`
    : '';

  document.getElementById('table').innerHTML = head +
    `<div class="cmp-group">${meta}</div><div class="cmp-group">${defaults}</div>${extraHtml}`;

  document.querySelectorAll('.cmp-stat .rm').forEach(b => b.onclick = () => removeStat(b.dataset.k));
}

function statRow(label, cells, bestIndex, removeKey) {
  const rm = removeKey ? ` <button class="rm" data-k="${removeKey}" title="Remove">✕</button>` : '';
  return `<div class="cmp-row"><div class="cmp-stat">${label}${rm}</div>` +
    cells.map((c, i) =>
      `<div class="cmp-cell ${i === bestIndex ? 'best' : ''}">${c.v}${i === bestIndex ? ' <span class="lead">▲</span>' : ''}</div>`
    ).join('') + `</div>`;
}

function drawRadar(d) {
  const datasets = d.players.map((p, i) => ({
    label: p.name,
    data: p.radar.map(v => v ?? 50),       // axis not measured for this position -> neutral
    fill: true,
    backgroundColor: rgba(COLORS[i % 3], .18),
    borderColor: rgba(COLORS[i % 3], .95),
    pointBackgroundColor: rgba(COLORS[i % 3], 1), pointRadius: 3,
  }));
  if (radarChart) radarChart.destroy();
  radarChart = new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: { labels: d.radar_axes, datasets },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        r: {
          min: 0, max: 100, ticks: { display: false, stepSize: 25 },
          grid: { color: '#1b2236' }, angleLines: { color: '#1b2236' },
          pointLabels: { color: '#cdd4e6', font: { size: 11 } },
        },
      },
    },
  });
  document.getElementById('legend').innerHTML = d.players.map((p, i) =>
    `<span class="lg"><span class="dot" style="background:${rgba(COLORS[i % 3], 1)}"></span>${p.name}</span>`).join('');
}

document.getElementById('searchBox').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) { addPlayer(e.target.value); e.target.value = ''; }
});

render();
