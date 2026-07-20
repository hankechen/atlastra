// Blog index — cards linking to each article.
renderSidebar('Blog');

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fmtDate = (iso) => {
  const d = new Date(iso + 'T00:00:00');
  return isNaN(d) ? iso : d.toLocaleDateString([], { year: 'numeric', month: 'long', day: 'numeric' });
};

function card(p) {
  const tags = (p.tags || []).map((t) => `<span class="blog-tag">${esc(t)}</span>`).join('');
  const cover = p.image
    ? `<img class="blog-cimg" src="${esc(p.image)}" alt="" loading="lazy">`
    : `<span class="blog-emoji">${esc(p.emoji || '📝')}</span>`;
  return `<a class="blog-card" href="/blogpost.html?slug=${encodeURIComponent(p.slug)}">
      <div class="blog-cover">${cover}</div>
      <div class="blog-cbody">
        <div class="blog-tags">${tags}</div>
        <h3>${esc(p.title)}</h3>
        <p class="blog-sub">${esc(p.subtitle || '')}</p>
        <div class="blog-meta"><span>${esc(p.author || 'Atlastra')}</span><span>·</span>
          <span>${fmtDate(p.date)}</span><span>·</span><span>${p.read_min || 3} min read</span></div>
      </div>
    </a>`;
}

(async function () {
  const box = document.getElementById('blogList');
  let r;
  try { r = await api('/api/blog'); } catch { r = null; }
  const posts = (r && r.posts) || [];
  if (!posts.length) { box.innerHTML = '<div class="empty-state">No posts yet — check back soon.</div>'; return; }
  box.innerHTML = posts.map(card).join('');
})();
