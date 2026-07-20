// Blog article reader — renders a post's structured blocks. Blocks come from our own
// blog.py (trusted), so `p` blocks may carry inline HTML; text blocks are escaped.
renderSidebar('Blog');

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fmtDate = (iso) => {
  const d = new Date(iso + 'T00:00:00');
  return isNaN(d) ? iso : d.toLocaleDateString([], { year: 'numeric', month: 'long', day: 'numeric' });
};

function block(b) {
  switch (b.t) {
    case 'h2': return `<h2>${esc(b.text)}</h2>`;
    case 'p': return `<p>${b.html || esc(b.text || '')}</p>`;
    case 'quote': return `<blockquote>${esc(b.text)}</blockquote>`;
    case 'list': return `<ul class="blog-ul">${(b.items || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
    case 'stat':
      return `<div class="blog-stats">${(b.items || []).map((s) =>
        `<div class="blog-stat"><b>${esc(s.v)}</b><span>${esc(s.k)}</span></div>`).join('')}</div>`;
    case 'table':
      return `<div class="blog-tblwrap"><table class="blog-tbl">
        <thead><tr>${(b.head || []).map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${(b.rows || []).map((r) => `<tr>${r.map((c, i) =>
          `<td${i === 0 ? ' class="tl"' : ''}>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    default: return '';
  }
}

// Small live callout linking to the player the article is about (photo + ratings).
async function playerCallout(name) {
  try {
    const p = await api('/api/player?name=' + encodeURIComponent(name));
    if (!p || !p.name) return '';
    const rr = p.ratings || {};
    const gauge = (lbl, o) => o && o.rating != null
      ? `<div class="bpc-r"><b>${o.rating}</b><span>${lbl}</span></div>` : '';
    const photo = p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy">` : '';
    return `<a class="bp-callout" href="/player.html?name=${encodeURIComponent(p.name)}">
        <div class="bpc-ph">${photo}</div>
        <div class="bpc-body">
          <span class="bpc-k">Player profile</span>
          <b class="bpc-name">${esc(p.name)}</b>
          <span class="bpc-team">${esc(p.team || '')}${p.detailed_position ? ' · ' + esc(p.detailed_position) : ''}</span>
        </div>
        <div class="bpc-rs">${gauge('League', rr.league)}${gauge('World Cup', rr.worldcup)}${gauge('UCL', rr.ucl)}</div>
        <span class="bpc-go">View →</span>
      </a>`;
  } catch { return ''; }
}

(async function () {
  const el = document.getElementById('post');
  const slug = new URLSearchParams(location.search).get('slug') || '';
  let r;
  try { r = await api('/api/blog?slug=' + encodeURIComponent(slug)); } catch { r = null; }
  if (!r || !r.available || !r.post) {
    el.innerHTML = '<div class="empty-state">Post not found. <a href="/blog.html">Back to the blog</a>.</div>';
    return;
  }
  const p = r.post;
  document.title = 'Atlastra — ' + p.title;
  document.getElementById('crumb').textContent = p.title;
  const tags = (p.tags || []).map((t) => `<span class="blog-tag">${esc(t)}</span>`).join('');
  const callout = p.player ? await playerCallout(p.player) : '';
  const figure = p.image
    ? `<figure class="blog-figure"><img src="${esc(p.image)}" alt="${esc(p.player || p.title)}"></figure>` : '';
  el.innerHTML = `
    ${figure}
    <header class="blog-hero">
      <div class="blog-tags">${tags}</div>
      <h1>${esc(p.title)}</h1>
      <p class="blog-lede">${esc(p.subtitle || '')}</p>
      <div class="blog-byline"><span class="blog-emoji sm">${esc(p.emoji || '📝')}</span>
        <span>${esc(p.author || 'Atlastra')}</span><span>·</span><span>${fmtDate(p.date)}</span>
        <span>·</span><span>${p.read_min || 3} min read</span></div>
    </header>
    ${callout}
    <div class="blog-body">${(p.body || []).map(block).join('')}</div>
    <div class="blog-foot"><a href="/blog.html">← All posts</a></div>`;
})();
