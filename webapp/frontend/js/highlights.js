// Top Highlights — best clips of the day/week, aggregated from finished matches
// (FotMob). Ranked server-side by competition importance + goals + recency.
renderSidebar('Highlights');
attachSearchDropdown(document.getElementById('searchBox'));

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const grid = document.getElementById('grid');
let PERIOD = 'day';

const crest = (id, cc) => cc ? `<span class="hl-flag">${flagISO2(cc) || ''}</span>`
  : (id ? `<img class="hl-crest" src="https://images.fotmob.com/image_resources/logo/teamlogo/${id}.png" alt="" loading="lazy">` : '');

const when = (ts) => {
  if (!ts) return '';
  const d = new Date(ts * 1000), now = new Date();
  const days = Math.floor((now.setHours(0, 0, 0, 0) - new Date(ts * 1000).setHours(0, 0, 0, 0)) / 864e5);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
};

function card(c, i) {
  const badge = [esc(c.competition), c.round ? esc(c.round) : ''].filter(Boolean).join(' · ');
  const thumb = c.thumbnail
    ? `<img class="hl-thumb" src="${esc(c.thumbnail)}" alt="" loading="lazy">`
    : '<div class="hl-thumb hl-noimg"></div>';
  const src = c.embed ? 'Watch clip' : `Watch on ${esc(c.source || 'source')}`;
  return `<button class="hl-card" data-i="${i}">
      <div class="hl-media">${thumb}<span class="hl-play">▶</span>
        <span class="hl-rank">${i + 1}</span>
        <span class="hl-src">${src}</span></div>
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

let CLIPS = [];
function openClip(i) {
  const c = CLIPS[i];
  if (!c) return;
  if (c.embed) {
    document.getElementById('hlPlayer').innerHTML =
      `<iframe src="${esc(c.embed)}?autoplay=1" title="Highlight" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>`;
    document.getElementById('hlCap').innerHTML =
      `<b>${esc(c.home)} ${c.home_score ?? ''}–${c.away_score ?? ''} ${esc(c.away)}</b> · ${esc(c.competition)}`;
    document.getElementById('hlModal').hidden = false;
  } else {                                   // non-embeddable (FIFA.com etc.) → new tab
    window.open(c.url, '_blank', 'noopener');
  }
}
function closeModal() {
  document.getElementById('hlModal').hidden = true;
  document.getElementById('hlPlayer').innerHTML = '';   // stop playback
}
document.getElementById('hlClose').onclick = closeModal;
document.getElementById('hlModal').onclick = (e) => { if (e.target.id === 'hlModal') closeModal(); };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

async function load() {
  grid.innerHTML = '<div class="hl-skel"></div>'.repeat(6);
  let r;
  try { r = await api('/api/highlights?period=' + PERIOD); } catch { r = null; }
  CLIPS = (r && r.clips) || [];
  if (!CLIPS.length) {
    grid.innerHTML = '<div class="placeholder-note">No highlights available for this window yet — check back after more matches finish.</div>';
    return;
  }
  grid.innerHTML = CLIPS.map(card).join('');
  grid.querySelectorAll('.hl-card').forEach(el => el.onclick = () => openClip(+el.dataset.i));
}

document.querySelectorAll('#periodTabs .seg-b').forEach(b => b.onclick = () => {
  document.querySelectorAll('#periodTabs .seg-b').forEach(x => x.classList.toggle('on', x === b));
  PERIOD = b.dataset.p;
  load();
});

load();
