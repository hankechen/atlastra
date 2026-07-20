// Top Highlights — best clips of the day/week from finished matches (FotMob).
//   • Match Reels: the full match highlight (FIFA.com etc.) — ranked by comp + goals
//   • Top Goals:   individual goals ranked by comp + how spectacular (low xG),
//                  each with an embeddable YouTube clip
renderSidebar('Highlights');
attachSearchDropdown(document.getElementById('searchBox'));

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const grid = document.getElementById('grid');
let PERIOD = 'day';
let MODE = 'reels';
let CLIPS = [];

const crest = (id, cc) => cc ? `<span class="hl-flag">${flagISO2(cc) || ''}</span>`
  : (id ? `<img class="hl-crest" src="https://images.fotmob.com/image_resources/logo/teamlogo/${id}.png" alt="" loading="lazy">` : '');

const when = (ts) => {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const days = Math.floor((new Date().setHours(0, 0, 0, 0) - new Date(ts * 1000).setHours(0, 0, 0, 0)) / 864e5);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
};

// Compact view/like counters shown under every YouTube-sourced clip. (YouTube removed
// public DISLIKE counts in 2021, so there's no real dislike number to show.)
function fmtNum(n) {
  if (n == null) return null;
  return n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M'
       : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n);
}
function statRow(c) {
  const v = fmtNum(c.views), l = fmtNum(c.likes), out = [];
  if (v != null) out.push(`<span class="hl-stat" title="${(c.views || 0).toLocaleString()} views">▶ ${v}</span>`);
  if (l != null) out.push(`<span class="hl-stat" title="${(c.likes || 0).toLocaleString()} likes">👍 ${l}</span>`);
  return out.length ? `<div class="hl-stats">${out.join('')}</div>` : '';
}

function reelCard(c, i) {
  const badge = [esc(c.competition), c.round ? esc(c.round) : ''].filter(Boolean).join(' · ');
  const thumb = c.thumbnail ? `<img class="hl-thumb" src="${esc(c.thumbnail)}" alt="" loading="lazy">` : '<div class="hl-thumb hl-noimg"></div>';
  const src = c.embed ? 'Watch clip' : `Watch on ${esc(c.source || 'source')}`;
  return `<button class="hl-card" data-i="${i}">
      <div class="hl-media">${thumb}<span class="hl-play">▶</span>
        <span class="hl-rank">${i + 1}</span><span class="hl-src">${src}</span></div>
      <div class="hl-body">
        <div class="hl-badge">${badge}<span class="hl-when">${when(c.kickoff_ts)}</span></div>
        <div class="hl-match">
          <span class="hl-team">${crest(c.home_id, c.home_cc)}<b>${esc(c.home)}</b></span>
          <span class="hl-score">${c.home_score ?? ''}<i>–</i>${c.away_score ?? ''}</span>
          <span class="hl-team hl-away"><b>${esc(c.away)}</b>${crest(c.away_id, c.away_cc)}</span>
        </div>
      </div>
    </button>`;
}

function goalCard(c, i) {
  const thumb = c.thumbnail ? `<img class="hl-thumb" src="${esc(c.thumbnail)}" alt="" loading="lazy">` : '<div class="hl-thumb hl-noimg"></div>';
  const tag = c.worldie ? '<span class="hl-tag">🔥 Worldie</span>' : (c.penalty ? '<span class="hl-tag pen">PEN</span>' : '');
  const xg = c.xg != null ? `<span class="hl-src">xG ${c.xg}</span>` : '';
  const scorer = c.scorer
    ? `<a class="hl-scorer" href="/player.html?name=${encodeURIComponent(c.scorer)}" onclick="event.stopPropagation()">${esc(c.scorer)}</a>`
    : `<span class="hl-scorer">${esc(c.scorer)}</span>`;
  return `<button class="hl-card" data-i="${i}">
      <div class="hl-media">${thumb}<span class="hl-play">▶</span>
        <span class="hl-rank">${i + 1}</span>${tag}${xg}</div>
      <div class="hl-body">
        <div class="hl-badge">${esc(c.competition)}<span class="hl-when">${esc(c.minute)}'</span></div>
        <div class="hl-goal">
          ${crest(null, c.team_cc)}${scorer}
          <span class="hl-vs">${esc(c.team)} vs ${esc(c.opponent)}${c.assist ? ` · 🅰 ${esc(c.assist)}` : ''}</span>
        </div>
        ${statRow(c)}
      </div>
    </button>`;
}

function trendCard(c, i) {
  const thumb = c.thumbnail ? `<img class="hl-thumb" src="${esc(c.thumbnail)}" alt="" loading="lazy">` : '<div class="hl-thumb hl-noimg"></div>';
  const len = c.length ? `<span class="hl-len">${esc(c.length)}</span>` : '';
  return `<button class="hl-card" data-i="${i}">
      <div class="hl-media">${thumb}<span class="hl-play">▶</span>
        <span class="hl-rank">${i + 1}</span>${len}</div>
      <div class="hl-body">
        <div class="hl-badge">${esc(c.channel || 'YouTube')}<span class="hl-when">${esc(c.age)}</span></div>
        <div class="hl-title">${esc(c.title)}</div>
        ${statRow(c)}
      </div>
    </button>`;
}

function shortCard(c, i) {
  const thumb = c.thumbnail ? `<img class="hl-thumb" src="${esc(c.thumbnail)}" alt="" loading="lazy">` : '<div class="hl-thumb hl-noimg"></div>';
  return `<button class="hl-card sh-card" data-i="${i}">
      <div class="hl-media sh-media">${thumb}<span class="hl-play">▶</span>
        <span class="hl-rank">${i + 1}</span><span class="sh-badge">Short</span></div>
      <div class="hl-body"><div class="hl-title">${esc(c.title)}</div>${statRow(c)}</div>
    </button>`;
}

function starCard(c, i) {
  const thumb = c.thumbnail ? `<img class="hl-thumb" src="${esc(c.thumbnail)}" alt="" loading="lazy">` : '<div class="hl-thumb hl-noimg"></div>';
  return `<button class="hl-card" data-i="${i}">
      <div class="hl-media">${thumb}<span class="hl-play">▶</span>
        <span class="hl-rank st-rank">${c.rank}</span></div>
      <div class="hl-body">
        <a class="hl-scorer" href="/player.html?name=${encodeURIComponent(c.player)}" onclick="event.stopPropagation()">${esc(c.player)}</a>
        <div class="hl-vs st-vs">${esc([c.club, c.position].filter(Boolean).join(' · '))}</div>
        ${statRow(c)}
      </div>
    </button>`;
}

// ---- watch modal --------------------------------------------------------
const modal = document.getElementById('hlModal');
const ytLink = document.getElementById('hlYt');
function openClip(i) {
  const c = CLIPS[i];
  if (!c) return;
  if (c.embed) {                             // embeddable (YouTube) → in-page player
    document.getElementById('hlPlayer').innerHTML =
      `<iframe src="${esc(c.embed)}?autoplay=1&rel=0" title="Highlight" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>`;
    document.getElementById('hlCap').innerHTML = c.scorer
      ? `<b>${esc(c.scorer)} ${esc(c.minute)}'</b> · ${esc(c.team)} vs ${esc(c.opponent)} · ${esc(c.competition)}`
      : `<b>${esc(c.home)} ${c.home_score ?? ''}–${c.away_score ?? ''} ${esc(c.away)}</b> · ${esc(c.competition)}`;
    ytLink.href = c.url; ytLink.hidden = !/youtu/.test(c.url || '');
    modal.hidden = false;
  } else {                                   // non-embeddable (FIFA.com etc.) → new tab
    window.open(c.url, '_blank', 'noopener');
  }
}
function closeModal() {
  modal.hidden = true;
  document.getElementById('hlPlayer').innerHTML = '';   // stop playback
}
document.getElementById('hlClose').onclick = closeModal;
modal.onclick = (e) => { if (e.target.id === 'hlModal') closeModal(); };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// ---- load ---------------------------------------------------------------
const DESC = {
  reels: 'The best clips from finished matches, ranked by competition and drama. Tap a card to watch.',
  goals: 'The best goals of the window — ranked by how spectacular the finish was — with a clip for each. Tap to play.',
  trending: 'The most-viewed football clips on YouTube from this window — highlights, goals and viral edits. Tap to watch.',
  shorts: 'Viral football Shorts — skills, dribbles, nutmegs and edits — ranked by views. Tap to watch.',
  stars: 'The 25 most renowned attackers & midfielders (by reputation, not our ratings) — each with their best skills video. Tap to watch.',
};
const EP = { reels: '/api/highlights', goals: '/api/top_goals', trending: '/api/trending', shorts: '/api/shorts', stars: '/api/top_stars' };
const RENDER = { reels: reelCard, goals: goalCard, trending: trendCard, shorts: shortCard, stars: starCard };
const NO_PERIOD = { shorts: 1, stars: 1 };               // ranked all-time, not by a date window
async function load() {
  document.getElementById('hlDesc').textContent = DESC[MODE];
  document.getElementById('periodTabs').style.visibility = NO_PERIOD[MODE] ? 'hidden' : 'visible';
  grid.classList.toggle('sh-grid', MODE === 'shorts');   // narrower, portrait layout
  grid.innerHTML = '<div class="hl-skel"></div>'.repeat(6);
  let r;
  try { r = await api(EP[MODE] + (NO_PERIOD[MODE] ? '' : '?period=' + PERIOD)); } catch { r = null; }
  CLIPS = (r && r.clips) || [];
  if (!CLIPS.length) {
    grid.innerHTML = `<div class="placeholder-note">Nothing available for this window yet — check back after more matches finish.</div>`;
    return;
  }
  const render = RENDER[MODE];
  grid.innerHTML = CLIPS.map(render).join('');
  grid.querySelectorAll('.hl-card').forEach(el => el.onclick = () => openClip(+el.dataset.i));
}

// ---- AI Week in Review --------------------------------------------------
function mdMini(md) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  let html = '', inList = false;
  const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
  for (const raw of String(md).split('\n')) {
    const line = raw.replace(/\s+$/, '');
    if (/^### /.test(line)) { closeList(); html += '<h4>' + inline(line.slice(4)) + '</h4>'; }
    else if (/^## /.test(line)) { closeList(); html += '<h3>' + inline(line.slice(3)) + '</h3>'; }
    else if (/^# /.test(line)) { closeList(); html += '<h3>' + inline(line.slice(2)) + '</h3>'; }
    else if (/^\s*[-*] /.test(line)) { if (!inList) { html += '<ul>'; inList = true; } html += '<li>' + inline(line.replace(/^\s*[-*] /, '')) + '</li>'; }
    else if (line.trim() === '') { closeList(); }
    else { closeList(); html += '<p>' + inline(line) + '</p>'; }
  }
  closeList();
  return html;
}
(async () => {
  let r;
  try { r = await api('/api/weekly_recap'); } catch { return; }
  if (!r || !r.available || !r.recap) return;
  document.getElementById('recapBody').innerHTML = mdMini(r.recap);
  const ai = r.model && /claude|gemini|gpt/i.test(r.model);
  document.getElementById('recapMeta').textContent =
    (ai ? 'AI-generated' : 'Auto-generated') + (r.week ? ' · ' + r.week.replace('-W', ' · Week ') : '');
  document.getElementById('recapCard').style.display = '';
})();

document.querySelectorAll('#periodTabs .seg-b').forEach(b => b.onclick = () => {
  document.querySelectorAll('#periodTabs .seg-b').forEach(x => x.classList.toggle('on', x === b));
  PERIOD = b.dataset.p; load();
});
document.querySelectorAll('#modeTabs .seg-b').forEach(b => b.onclick = () => {
  document.querySelectorAll('#modeTabs .seg-b').forEach(x => x.classList.toggle('on', x === b));
  MODE = b.dataset.m; load();
});

load();
