renderSidebar('Compare');
Chart.defaults.color = '#7f8aa3';
Chart.defaults.borderColor = 'rgba(150,158,178,.22)';
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
// per-player season, aligned to `names` by index ('' = let the server pick latest).
// arrives as index-tagged "<i>:<code>" params so a blank one can be omitted safely.
let seasons = names.map(() => '');
params.getAll('season').forEach(t => {
  const [i, code] = t.split(':');
  if (code && +i < seasons.length) seasons[+i] = code;
});
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

// shared query string: names, added stats, and index-tagged per-player seasons
function buildQS() {
  return names.map(n => 'name=' + encodeURIComponent(n))
    .concat(seasons.map((s, i) => s ? 'season=' + encodeURIComponent(i + ':' + s) : null).filter(Boolean))
    .concat(addedStats.map(s => 'stat=' + encodeURIComponent(s))).join('&');
}

function syncUrl() {
  const qs = buildQS();
  history.replaceState(null, '', '/compare.html' + (qs ? '?' + qs : ''));
}

function renderChips() {
  document.getElementById('chips').innerHTML = names.map((n, i) =>
    `<span class="cmp-chip" style="border-color:${rgba(COLORS[i % 3], .9)}">
       <span class="dot" style="background:${rgba(COLORS[i % 3], 1)}"></span>${n}
       <button data-i="${i}" title="Remove">✕</button></span>`).join('') +
    (names.length < 3 ? '<span class="cmp-hint">+ add up to 3 (search above)</span>' : '');
  document.querySelectorAll('.cmp-chip button').forEach(b => b.onclick = () => {
    const i = +b.dataset.i;
    names.splice(i, 1); seasons.splice(i, 1); syncUrl(); render();
  });
}

function addPlayer(name) {
  name = name.trim();
  if (!name || names.length >= 3) return;
  if (names.some(n => n.toLowerCase() === name.toLowerCase())) return;
  names.push(name); seasons.push(''); syncUrl(); render();
}

// user picked a season for one player (matched back to `names` by query text)
function setSeason(query, code) {
  const i = names.findIndex(n => n.toLowerCase() === query.toLowerCase());
  if (i >= 0) { seasons[i] = code; syncUrl(); render(); }
}

async function render() {
  renderChips();
  refreshPicker();
  const board = document.getElementById('board'), empty = document.getElementById('empty');
  document.getElementById('saveCmp').style.display = names.length >= 2 ? '' : 'none';
  if (names.length < 2) {
    board.style.display = 'none'; empty.style.display = '';
    empty.textContent = 'Add at least two players to compare.';
    return;
  }
  syncSaveBtn();
  const d = await api('/api/compare?' + buildQS());
  document.getElementById('seasonNote').textContent = d.season
    ? `Season ${d.season}.`
    : (d.players && d.players.length ? 'Comparing across different seasons.' : '');

  if (!d.players || d.players.length < 2) {
    board.style.display = 'none'; empty.style.display = '';
    empty.textContent = 'Couldn’t resolve at least two of those players. Check the spelling.';
    return;
  }
  // reflect the seasons the server actually used back into the URL (so a blank
  // default becomes concrete and bookmarks stay stable) — no re-render needed
  let changed = false;
  d.players.forEach(p => {
    const i = names.findIndex(n => n.toLowerCase() === (p.query || '').toLowerCase());
    if (i >= 0 && seasons[i] !== p.season) { seasons[i] = p.season; changed = true; }
  });
  if (changed) syncUrl();

  empty.style.display = 'none'; board.style.display = '';
  drawTable(d);
  drawRadar(d);
}

function drawTable(d) {
  const seasonSel = (p) => {
    const opts = (p.seasons || []).map(s =>
      `<option value="${s.value}"${s.value === p.season ? ' selected' : ''}>${s.label}</option>`).join('');
    return `<select class="cmp-season" data-q="${encodeURIComponent(p.query || p.name)}"
              title="Choose a season for ${p.name}">${opts}</select>`;
  };
  const head = `<div class="cmp-row cmp-head"><div class="cmp-stat"></div>` +
    d.players.map((p, i) => `<div class="cmp-cell">
        <span class="cmp-photo pic" style="box-shadow:0 0 0 2px ${rgba(COLORS[i % 3], 1)}">${avatarHTML(p.photo, p.name)}</span>
        <div class="cmp-pname"><span class="dot" style="background:${rgba(COLORS[i % 3], 1)}"></span>${p.name}</div>
        <div class="cmp-sub">${(p.country_code ? flagEmoji(p.country_code) + ' ' : '')}${crestHTML(p.team_logo, 'crest-sm')}${p.team || ''} · ${p.position || ''}</div>
        <div class="cmp-seasonwrap">${seasonSel(p)}</div>
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
  document.querySelectorAll('.cmp-season').forEach(sel => sel.onchange = () =>
    setSeason(decodeURIComponent(sel.dataset.q), sel.value));
}

function statRow(label, cells, bestIndex, removeKey) {
  const rm = removeKey ? ` <button class="rm" data-k="${removeKey}" title="Remove">✕</button>` : '';
  return `<div class="cmp-row"><div class="cmp-stat">${label}${rm}</div>` +
    cells.map((c, i) =>
      `<div class="cmp-cell ${i === bestIndex ? 'best' : ''}">${c.v}${i === bestIndex ? ' <span class="lead">▲</span>' : ''}</div>`
    ).join('') + `</div>`;
}

function drawRadar(d) {
  // radar percentiles only exist for the latest season, so label the shape by the
  // season it ACTUALLY came from (radar_season), not the player's stat season
  const datasets = d.players.map((p, i) => ({
    label: p.radar_season_label ? `${p.name} (${p.radar_season_label})` : p.name,
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
          grid: { color: 'rgba(150,158,178,.22)' }, angleLines: { color: 'rgba(150,158,178,.22)' },
          pointLabels: { color: '#8a93a6', font: { size: 11 } },
        },
      },
    },
  });
  document.getElementById('legend').innerHTML = d.players.map((p, i) =>
    `<span class="lg"><span class="dot" style="background:${rgba(COLORS[i % 3], 1)}"></span>${p.name}` +
    `${p.radar_season_label ? ` <span class="muted">· ${p.radar_season_label}</span>` : ''}</span>`).join('');

  // if any player's radar isn't from the season they were picked in, say so plainly
  const sub = document.getElementById('radarSub');
  if (sub) sub.textContent = d.players.some(p => p.radar_is_current)
    ? 'Percentile vs same position · latest-form only (earlier seasons unavailable)'
    : 'Percentile vs same position';
}

// same live typeahead as the global search, but players-only and picking one
// ADDS it to the comparison instead of opening their profile
attachSearchDropdown(document.getElementById('searchBox'), {
  playersOnly: true,
  onPick: (p) => addPlayer(p.player),
});

// save / unsave the current comparison (localStorage via Store)
const cmpId = () => names.slice().sort().join(' vs ');
function syncSaveBtn() {
  const b = document.getElementById('saveCmp');
  const on = Store.has('comparisons', cmpId());
  b.classList.toggle('on', on);
  b.textContent = on ? '✓ Saved' : '★ Save comparison';
}
document.getElementById('saveCmp').onclick = () => {
  if (names.length < 2) return;
  Store.toggle('comparisons', { id: cmpId(), names: names.slice(), seasons: seasons.slice(), stats: addedStats.slice(), label: names.join('  vs  ') });
  syncSaveBtn();
};

render();
