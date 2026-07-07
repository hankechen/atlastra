renderSidebar('Live Matches');
attachSearchDropdown(document.getElementById('searchBox'));

const STATES = [
  ['today', 'Today', "All of today's matches — live, upcoming and finished, by kickoff time."],
  ['live', 'Live', 'Matches in play right now.'],
  ['upcoming', 'Upcoming', 'Scheduled fixtures, soonest first.'],
  ['recent', 'Results', 'Recently finished matches.'],
];
let data = { today: [], live: [], upcoming: [], recent: [] };
let active = null;

const _isToday = (ts) => {
  const d = new Date(ts * 1000), n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
};

// SofaScore files the qualifying rounds under the main competition id, so a
// qualifier's `round` reads like "Qualification Round 1" while its competition is
// just "UEFA Champions League". Split those into a "… Qualification" section so it's
// clear they're not the group/league phase proper. Match only "qualif" (not
// "play-off") so the main-competition knockout play-off rounds stay put.
function compLabel(m) {
  return /qualif/i.test(String(m.round || '')) ? `${m.competition} Qualification` : m.competition;
}

// Competition importance (highest first) — the section order in every tab.
// Unlisted competitions ("the rest") share the last rank and keep their
// first-seen order. UCL proper ranks high; UCL Qualification drops near the end.
function compRank(label) {
  const l = label.toLowerCase();
  const qual = /qualif/i.test(label);
  if (l.includes('world cup')) return 0;
  if (/\beuro\b/.test(l)) return 1;                 // "UEFA EURO", not "Europa League"
  if (l.includes('copa')) return 2;                 // Copa América
  if (l.includes('champions league')) return qual ? 9 : 3;
  if (l.includes('premier league')) return 4;
  if (l.includes('la liga') || l.includes('laliga')) return 5;
  if (l.includes('serie a')) return 6;
  if (l.includes('ligue 1')) return 7;
  if (l.includes('bundesliga')) return 8;
  return 10;                                         // the rest
}

// group a flat match list by competition, then order the sections by importance
// (ties keep first-seen order — Array.sort is stable)
function byCompetition(list) {
  const groups = [];
  const idx = {};
  for (const m of list) {
    const c = compLabel(m);
    if (!(c in idx)) { idx[c] = groups.length; groups.push([c, []]); }
    groups[idx[c]][1].push(m);
  }
  return groups.sort((a, b) => compRank(a[0]) - compRank(b[0]));
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
    // "Today" = every match kicking off today (live + scheduled + finished), by time
    data.today = [...(data.live || []), ...(data.upcoming || []), ...(data.recent || [])]
      .filter(m => _isToday(m.kickoff_ts))
      .sort((a, b) => a.kickoff_ts - b.kickoff_ts);
    // default tab: Today if it has matches, else live > upcoming > results
    if (!active || !data[active].length)
      active = STATES.map(([k]) => k).find(k => data[k].length) || 'today';
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
