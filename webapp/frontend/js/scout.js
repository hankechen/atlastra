renderSidebar('Scout');
attachSearchDropdown(document.getElementById('searchBox'));

const $ = (id) => document.getElementById(id);
const fmtMV = (v) => v == null ? '—' : '€' + (v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M' : Math.round(v / 1e3) + 'K');
const fmtMetric = (v, fmt) => v == null ? '—' : fmt === 'pct' ? v + '%' : v;

let filled = false;        // selects populated from first response
let timer = null;          // debounce slider drags

function readParams() {
  const value = +$('fValue').value, age = +$('fAge').value;
  const minutes = +$('fMin').value, rating = +$('fRating').value;
  $('vValue').textContent = value ? '≤ €' + value + 'M' : 'Any';
  $('vAge').textContent = age >= 40 ? 'Any' : '≤ ' + age;
  $('vMin').textContent = minutes;
  $('vRating').textContent = rating ? '≥ ' + rating : 'Any';
  return new URLSearchParams({
    pos: $('fPos').value || 'all',
    metric: $('fMetric').value || 'rating',
    max_value: value,
    max_age: age >= 40 ? 0 : age,
    min_minutes: minutes,
    min_rating: rating,
    limit: 60,
  });
}

async function run() {
  const d = await api('/api/scout?' + readParams().toString());
  if (!filled) {
    $('fPos').innerHTML = d.groups.map(g => `<option value="${g.key}">${g.label}</option>`).join('');
    $('fMetric').innerHTML = d.metrics.map(m => `<option value="${m.key}">${m.label}</option>`).join('');
    $('fMetric').value = d.metric;
    filled = true;
  }
  $('cnt').innerHTML = `<b>${d.count}</b> player${d.count === 1 ? '' : 's'} · ranked by <b>${d.metric_label}</b>`;
  const ml = d.metric_label;
  $('results').innerHTML = `<table class="stbl"><thead><tr>
      <th class="l">#</th><th class="l">Player</th><th class="l">Pos</th>
      <th>Age</th><th>Mins</th><th>Value</th><th>Rating</th><th class="m">${ml}</th>
    </tr></thead><tbody>${d.players.map((p, i) => `
      <tr onclick="location.href='${pHref(p.player)}'">
        <td class="l rk">${i + 1}</td>
        <td class="l"><div class="pcell">
          <span class="pic">${avatarHTML(p.photo, p.player)}</span>
          <span><div class="nm">${p.player}</div>
            <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team || ''}</div></span>
        </div></td>
        <td class="l">${p.position || '—'}</td>
        <td>${p.age ?? '—'}</td>
        <td>${p.minutes ?? '—'}</td>
        <td>${fmtMV(p.market_value_eur)}</td>
        <td class="rtg">${p.rating ?? '—'}</td>
        <td class="mcol">${fmtMetric(p.metric_val, d.metric_fmt)}</td>
      </tr>`).join('')}</tbody></table>
    ${d.players.length ? '' : '<div class="empty">No players match these filters — loosen them a little.</div>'}`;
}

function debounced() { clearTimeout(timer); timer = setTimeout(run, 200); }

['fPos', 'fMetric'].forEach(id => $(id).addEventListener('change', run));
['fValue', 'fAge', 'fMin', 'fRating'].forEach(id => {
  $(id).addEventListener('input', () => { readParams(); debounced(); });
});

run();
