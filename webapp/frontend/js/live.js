renderSidebar('Live Matches');
attachSearchDropdown(document.getElementById('searchBox'));

const STATES = [
  ['live', 'Live', 'Matches in play right now.'],
  ['upcoming', 'Upcoming', 'Scheduled fixtures, soonest first.'],
  ['recent', 'Results', 'Recently finished matches.'],
];
let data = { live: [], upcoming: [], recent: [] };
let active = null;

// group a flat match list by competition, preserving first-seen order
function byCompetition(list) {
  const groups = [];
  const idx = {};
  for (const m of list) {
    if (!(m.competition in idx)) { idx[m.competition] = groups.length; groups.push([m.competition, []]); }
    groups[idx[m.competition]][1].push(m);
  }
  return groups;
}

function renderTabs() {
  const el = document.getElementById('stateTabs');
  el.innerHTML = STATES.map(([k, label]) =>
    `<span class="tab ${k === active ? 'active' : ''}" data-k="${k}">${label} <b>${data[k].length}</b></span>`).join('');
  el.querySelectorAll('.tab').forEach(t => t.onclick = () => { active = t.dataset.k; render(); });
}

function render() {
  renderTabs();
  const list = data[active];
  const desc = STATES.find(([k]) => k === active)[2];
  const feed = document.getElementById('feed');
  if (!list.length) {
    feed.innerHTML = `<section class="card"><div class="placeholder-note">No ${active === 'recent' ? 'results' : active} matches in the current window.</div></section>`;
    return;
  }
  feed.innerHTML = byCompetition(list).map(([comp, ms]) => `
    <section class="card live-group">
      <div class="card-h"><h3>${comp}</h3><span class="see">${ms.length} match${ms.length > 1 ? 'es' : ''}</span></div>
      ${ms.map(matchRow).join('')}
    </section>`).join('');
}

async function load() {
  const btn = document.getElementById('refresh');
  btn.disabled = true;
  try {
    data = await api('/api/live');
    // default tab: whatever has matches, preferring live > upcoming > results
    if (!active || !data[active].length)
      active = STATES.map(([k]) => k).find(k => data[k].length) || 'live';
    if (data.updated_at)
      document.getElementById('updated').textContent =
        '· updated ' + new Date(data.updated_at.replace(' ', 'T')).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    render();
  } catch {
    document.getElementById('feed').innerHTML =
      '<section class="card"><div class="placeholder-note">Live feed unavailable — run <code>python -m pipeline.load_live</code>.</div></section>';
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('refresh').onclick = load;
load();
// keep live scores fresh while the tab is open (the feed table is rebuilt server-side)
setInterval(() => { if (!document.hidden) load(); }, 30000);
