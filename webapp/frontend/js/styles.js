renderSidebar('Team Styles');
attachSearchDropdown(document.getElementById('searchBox'));
Chart.defaults.color = '#7f8aa3';
Chart.defaults.font.family = 'Inter';

const COLORS = [[85, 112, 240], [46, 200, 150]];
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const css = (c) => rgba(c, 1);
let radarChart;

// raw-value suffix per axis (ppda/penetration are per-match counts; control is %)
const RAW_SUFFIX = { ctrl: '%', press: ' ppda', pen: ' deep' };

function optionsHTML(opts, sel) {
  let html = '', lastLeague = null;
  for (const o of opts) {
    if (o.league !== lastLeague) {
      if (lastLeague !== null) html += '</optgroup>';
      html += `<optgroup label="${o.league}">`; lastLeague = o.league;
    }
    html += `<option value="${o.team}"${o.team === sel ? ' selected' : ''}>${o.team}</option>`;
  }
  return html + '</optgroup>';
}

function drawRadar(teams) {
  const axes = teams[0].axes.map(a => a.label);
  const datasets = teams.map((t, i) => ({
    label: t.team,
    data: t.axes.map(a => a.score ?? 0),
    fill: true,
    backgroundColor: rgba(COLORS[i], .18),
    borderColor: rgba(COLORS[i], .95),
    pointBackgroundColor: rgba(COLORS[i], 1), pointRadius: 3,
  }));
  if (radarChart) radarChart.destroy();
  radarChart = new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: { labels: axes, datasets },
    options: {
      plugins: { legend: { display: false } },
      scales: { r: {
        min: 0, max: 100, ticks: { display: false, stepSize: 25 },
        grid: { color: '#1b2236' }, angleLines: { color: '#1b2236' },
        pointLabels: { color: '#cdd4e6', font: { size: 12, weight: '600' } },
      } },
    },
  });
  document.getElementById('legend').innerHTML = teams.map((t, i) =>
    `<span class="lg"><span class="dot" style="background:${css(COLORS[i])}"></span>${t.team}</span>`).join('');
}

function drawTable(teams) {
  const head = `<tr><td></td>${teams.map((t, i) =>
    `<td class="sc" style="color:${css(COLORS[i])}">${t.team.split(' ')[0]}</td>`).join('')}</tr>`;
  const rows = teams[0].axes.map((_, ai) => {
    const label = teams[0].axes[ai].label;
    const cells = teams.map((t, i) => {
      const a = t.axes[ai];
      const suf = RAW_SUFFIX[a.key] || '';
      return `<td class="sc" style="color:${css(COLORS[i])}">${a.score ?? '—'}
        <div class="rw">${a.raw ?? '—'}${suf}</div></td>`;
    }).join('');
    return `<tr><td>${label}</td>${cells}</tr>`;
  }).join('');
  document.getElementById('axisTbl').innerHTML = head + rows;
}

async function load() {
  const a = document.getElementById('teamA').value, b = document.getElementById('teamB').value;
  history.replaceState(null, '', `/styles.html?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  const teams = (await api(`/api/team_style?name=${encodeURIComponent(a)}&name=${encodeURIComponent(b)}`))
    .filter(t => t && t.axes);
  if (!teams.length) return;
  drawRadar(teams); drawTable(teams);
}

(async () => {
  const opts = await api('/api/team_options');
  const params = new URLSearchParams(location.search);
  const has = (n) => opts.some(o => o.team === n);
  const pick = (want, idx) => has(want) ? want : opts[idx].team;
  const a = params.get('a') && has(params.get('a')) ? params.get('a') : pick('Manchester City', 0);
  let b = params.get('b') && has(params.get('b')) ? params.get('b') : pick('Liverpool', 1);
  if (b === a) b = opts.find(o => o.team !== a).team;
  document.getElementById('teamA').innerHTML = optionsHTML(opts, a);
  document.getElementById('teamB').innerHTML = optionsHTML(opts, b);
  document.getElementById('dotA').style.background = css(COLORS[0]);
  document.getElementById('dotB').style.background = css(COLORS[1]);
  document.getElementById('teamA').addEventListener('change', load);
  document.getElementById('teamB').addEventListener('change', load);
  load();
})();
