renderSidebar('Big Game Index');
attachSearchDropdown(document.getElementById('searchBox'));

(function () {
  const MAX90 = 1.2;                       // bar scale cap for G+A per 90
  const w = (v) => Math.min(100, v / MAX90 * 100);
  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

  function row(r, i) {
    const ph = r.photo ? `<img src="${r.photo}" alt="" onerror="this.remove()">` : '';
    const sign = r.delta > 0 ? '+' : '';
    return `<a class="bg-row" href="/player.html?name=${encodeURIComponent(r.player)}">
      <span class="bg-rank">${i + 1}</span>
      <span class="bg-ph">${ph}<span class="ini">${initials(r.player)}</span></span>
      <span class="bg-id"><b>${esc(r.player)}</b><span>${esc([r.position, r.team].filter(Boolean).join(' · '))}</span></span>
      <span class="bg-split">
        <span class="bg-sb"><label>Big games <i>${r.big.ga90.toFixed(2)}</i></label><span class="bg-bar"><i class="big" style="width:${w(r.big.ga90)}%"></i></span></span>
        <span class="bg-sb"><label>Weak <i>${r.weak.ga90.toFixed(2)}</i></label><span class="bg-bar"><i class="weak" style="width:${w(r.weak.ga90)}%"></i></span></span>
      </span>
      <span class="bg-delta ${r.delta >= 0 ? 'up' : 'down'}">${sign}${r.delta.toFixed(2)}</span>
    </a>`;
  }

  async function load() {
    const out = document.getElementById('bg-out');
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Crunching matches by opponent strength…</p></div></section>`;
    let d;
    try { d = await api('/api/big_game_board'); }
    catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not load the index.</p></div></section>'; return; }
    if (!d.available) {
      out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">📊</div><p>${esc(d.error || 'Unavailable.')}</p></div></section>`;
      return;
    }
    const col = (title, sub, items, cls) => `<section class="card bg-col">
      <div class="card-h"><h3 class="${cls}">${title}</h3><span class="muted" style="font-size:12px">${sub}</span></div>
      ${items.length ? items.map(row).join('') : '<div class="muted" style="padding:16px">Not enough qualifying players yet.</div>'}
    </section>`;
    out.innerHTML = `
      <div class="bg-grid">
        ${col('⭐ Big-Game Players', 'Step up vs top-half sides', d.clutch, 'ok')}
        ${col('🛑 Flat-Track Bullies', 'Feast on weak sides, fade vs strong', d.bully, 'bad')}
      </div>
      <p class="bg-note">Goal involvements (G+A) per 90, ${d.season ? '20' + d.season.slice(0, 2) + '/' + d.season.slice(2) + ' · ' : ''}top-5 leagues. "Big games" = opponents that finished in the top half of their league. Minimum ~4 full matches in each split. Δ is the per-90 difference (big-game minus weak).</p>`;
  }

  load();
})();
