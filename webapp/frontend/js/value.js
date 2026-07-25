renderSidebar('Value Finder');
attachSearchDropdown(document.getElementById('searchBox'));

(function () {
  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const eurM = (e) => '€' + (e / 1e6).toFixed(e >= 1e8 ? 0 : 1) + 'm';

  function row(r, i) {
    const ph = r.photo ? `<img src="${r.photo}" alt="" onerror="this.remove()">` : '';
    const mult = r.ratio;                 // model / actual
    const up = mult >= 1;
    const multTxt = up ? mult.toFixed(2) + '×' : mult.toFixed(2) + '×';
    return `<a class="vf-row" href="/player.html?name=${encodeURIComponent(r.player)}">
      <span class="vf-rank">${i + 1}</span>
      <span class="vf-ph">${ph}<span class="ini">${initials(r.player)}</span></span>
      <span class="vf-id"><b>${esc(r.player)}</b><span>${esc([r.position, r.team].filter(Boolean).join(' · '))}</span></span>
      <span class="vf-vals"><span class="a">${eurM(r.actual_eur)}</span> → <span class="p">${eurM(r.predicted_eur)}</span></span>
      <span class="vf-mult ${up ? 'up' : 'down'}">${multTxt}</span>
    </a>`;
  }

  async function load() {
    const out = document.getElementById('vf-out');
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div><p>Running the value model…</p></div></section>`;
    let d;
    try { d = await api('/api/value_board?limit=15'); }
    catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not load the model.</p></div></section>'; return; }
    if (!d.available) {
      out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">💰</div><p>${esc(d.error || 'Model not trained yet.')}</p></div></section>`;
      return;
    }
    const col = (title, sub, items, cls) => `<section class="card">
      <div class="card-h"><h3 class="${cls}">${title}</h3><span class="muted" style="font-size:12px">${sub}</span></div>
      ${items.length ? items.map(row).join('') : '<div class="muted" style="padding:16px">None.</div>'}
    </section>`;
    const m = d.model;
    const note = m ? `Gradient-boosting model · cross-validated R²≈${m.r2.toFixed(2)}, average error ≈€${Math.round(m.mae_m)}m across ${m.n} valued players (top-5 leagues, ${d.season ? '20' + d.season.slice(0, 2) + '/' + d.season.slice(2) : ''}). ` : '';
    out.innerHTML = `
      <div class="vf-grid">
        ${col('📈 Undervalued', 'Model estimate well above market price', d.undervalued, 'ok')}
        ${col('📉 Overvalued', 'Market price well above the model estimate', d.overvalued, 'bad')}
      </div>
      <p class="bg-note">${note}The multiplier (×) is model estimate ÷ Transfermarkt value. The model reads only measurable inputs — age, rating, per-90 output and league — so it cannot see contract length, hype, reputation or a big fee. Expensive young prospects therefore often surface as "overvalued": the market is paying for potential the current numbers don't yet show.</p>`;
  }

  load();
})();
