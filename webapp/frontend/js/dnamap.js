renderSidebar('Football DNA Map');
attachSearchDropdown(document.getElementById('searchBox'));

// wrapped in an IIFE so none of these identifiers leak into the shared global
// scope (all page scripts share one scope — avoids name clashes with api.js).
(function () {
  const COLORS = {
    CB: '#4f9dff', FB: '#36c5d6', DM: '#7d6cf5', CM: '#9b8cff',
    AM: '#c45cf5', W: '#ff8a3d', ST: '#ff5470',
  };
  const GROUP_ORDER = ['CB', 'FB', 'DM', 'CM', 'AM', 'W', 'ST'];
  const STAR = 88;                       // always-labelled rating threshold
  const stage = document.getElementById('dnaStage');
  const canvas = document.getElementById('dna');
  const ctx = canvas.getContext('2d');
  const tip = document.getElementById('dnaTip');

  let PTS = [], byName = new Map();
  let cam = { scale: 1, tx: 0, ty: 0 };
  let DPR = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  let Wc = 0, Hc = 0;
  let hover = -1, focus = -1, neighbors = [];
  let dim = new Set();                   // groups toggled off via legend

  const w2sx = (x) => x * cam.scale + cam.tx;
  const w2sy = (y) => -y * cam.scale + cam.ty;
  const s2wx = (sx) => (sx - cam.tx) / cam.scale;
  const s2wy = (sy) => -(sy - cam.ty) / cam.scale;
  const radius = (r) => Math.max(2.4, 2.4 + (r - 58) / 7.5);

  function resize() {
    Wc = stage.clientWidth; Hc = stage.clientHeight;
    canvas.width = Wc * DPR; canvas.height = Hc * DPR;
    canvas.style.width = Wc + 'px'; canvas.style.height = Hc + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function fitView() {
    if (!PTS.length) return;
    let xs = PTS.map(p => p.x), ys = PTS.map(p => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    cam.scale = Math.min(Wc / (maxX - minX || 1), Hc / (maxY - minY || 1)) * 0.84;
    cam.tx = Wc / 2 - cx * cam.scale;
    cam.ty = Hc / 2 + cy * cam.scale;
  }

  function draw() {
    ctx.clearRect(0, 0, Wc, Hc);
    // origin axes
    const ox = w2sx(0), oy = w2sy(0);
    ctx.strokeStyle = 'rgba(255,255,255,.07)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, oy); ctx.lineTo(Wc, oy); ctx.moveTo(ox, 0); ctx.lineTo(ox, Hc); ctx.stroke();

    const spot = focus >= 0;
    const nset = new Set(neighbors);
    // spotlight lines
    if (spot) {
      const f = PTS[focus];
      ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1.4;
      for (const j of neighbors) {
        ctx.beginPath(); ctx.moveTo(w2sx(f.x), w2sy(f.y)); ctx.lineTo(w2sx(PTS[j].x), w2sy(PTS[j].y)); ctx.stroke();
      }
    }
    // points
    for (let i = 0; i < PTS.length; i++) {
      const p = PTS[i];
      if (dim.has(p.group)) continue;
      const sx = w2sx(p.x), sy = w2sy(p.y);
      if (sx < -20 || sx > Wc + 20 || sy < -20 || sy > Hc + 20) continue;
      const isFocus = i === focus, isNb = nset.has(i), isHov = i === hover;
      let a = 1;
      if (spot && !isFocus && !isNb) a = 0.18;
      ctx.globalAlpha = a;
      ctx.fillStyle = COLORS[p.group] || '#888';
      ctx.beginPath();
      ctx.arc(sx, sy, radius(p.rating) * (isFocus ? 1.7 : isNb || isHov ? 1.3 : 1), 0, 7);
      ctx.fill();
      if (isFocus || isNb || isHov) {
        ctx.globalAlpha = 1; ctx.lineWidth = 2;
        ctx.strokeStyle = isFocus ? '#fff' : 'rgba(255,255,255,.7)'; ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    // labels: stars (when not spotlighting) + focus + neighbours + hover
    ctx.font = '600 11px Inter'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    const labelIdx = new Set();
    if (!spot) PTS.forEach((p, i) => { if (p.rating >= STAR && !dim.has(p.group)) labelIdx.add(i); });
    else { labelIdx.add(focus); neighbors.forEach(j => labelIdx.add(j)); }
    if (hover >= 0) labelIdx.add(hover);
    for (const i of labelIdx) {
      const p = PTS[i]; if (dim.has(p.group)) continue;
      const sx = w2sx(p.x), sy = w2sy(p.y);
      if (sx < 0 || sx > Wc || sy < 0 || sy > Hc) continue;
      ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(8,12,20,.85)';
      ctx.strokeText(p.name, sx, sy - radius(p.rating) - 3);
      ctx.fillStyle = i === focus ? '#fff' : '#dfe5f0';
      ctx.fillText(p.name, sx, sy - radius(p.rating) - 3);
    }
  }

  let raf = 0;
  const render = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };

  function nearestAt(mx, my) {
    let best = -1, bd = 16 * 16;
    for (let i = 0; i < PTS.length; i++) {
      const p = PTS[i]; if (dim.has(p.group)) continue;
      const dx = w2sx(p.x) - mx, dy = w2sy(p.y) - my, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  function showTip(i, mx, my) {
    const p = PTS[i];
    const ph = p.fpid ? `<img src="/api/img?u=${encodeURIComponent('https://images.fotmob.com/image_resources/playerimages/' + p.fpid + '.png')}">` : '';
    tip.innerHTML = `${ph}<div><b>${p.name}</b><span>${p.group_label} · ${p.team || ''}</span>
      <span>Rating ${p.rating}${p.archetype ? ' · ' + p.archetype : ''}</span></div>`;
    tip.style.display = 'flex';
    const r = tip.getBoundingClientRect();
    let x = mx + 16, y = my + 16;
    if (x + r.width > Wc) x = mx - r.width - 16;
    if (y + r.height > Hc) y = my - r.height - 16;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }

  function spotlight(i) {
    focus = i;
    const f = PTS[i];
    neighbors = PTS.map((p, j) => [j, (p.x - f.x) ** 2 + (p.y - f.y) ** 2])
      .filter(([j]) => j !== i && !dim.has(PTS[j].group))
      .sort((a, b) => a[1] - b[1]).slice(0, 6).map(([j]) => j);
    const foot = document.getElementById('dnaFoot');
    foot.innerHTML = `<b>${f.name}</b>'s closest stylistic matches: ` +
      neighbors.map(j => `<a href="/player.html?name=${encodeURIComponent(PTS[j].name)}">${PTS[j].name}</a>`).join(', ');
    render();
  }

  function centerOn(p, zoom) {
    if (zoom) cam.scale = zoom;
    cam.tx = Wc / 2 - p.x * cam.scale;
    cam.ty = Hc / 2 + p.y * cam.scale;
  }

  // ---- interaction ----
  let drag = null, moved = false;
  canvas.addEventListener('mousedown', (e) => { drag = { x: e.offsetX, y: e.offsetY }; moved = false; });
  window.addEventListener('mouseup', (e) => {
    if (drag && !moved) {
      const i = nearestAt(drag.x, drag.y);
      if (i >= 0) spotlight(i); else { focus = -1; neighbors = []; document.getElementById('dnaFoot').innerHTML = ''; render(); }
    }
    drag = null;
  });
  canvas.addEventListener('mousemove', (e) => {
    if (drag) {
      const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      cam.tx += dx; cam.ty += dy; drag = { x: e.offsetX, y: e.offsetY };
      tip.style.display = 'none'; render(); return;
    }
    const i = nearestAt(e.offsetX, e.offsetY);
    canvas.style.cursor = i >= 0 ? 'pointer' : 'grab';
    if (i !== hover) { hover = i; render(); }
    if (i >= 0) showTip(i, e.offsetX, e.offsetY); else tip.style.display = 'none';
  });
  canvas.addEventListener('mouseleave', () => { hover = -1; tip.style.display = 'none'; render(); });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const wx = s2wx(e.offsetX), wy = s2wy(e.offsetY);
    cam.scale *= e.deltaY < 0 ? 1.12 : 1 / 1.12;
    cam.scale = Math.max(2, Math.min(220, cam.scale));
    cam.tx = e.offsetX - wx * cam.scale; cam.ty = e.offsetY + wy * cam.scale;
    render();
  }, { passive: false });

  document.getElementById('dnaReset').onclick = () => {
    focus = -1; neighbors = []; document.getElementById('dnaFoot').innerHTML = ''; fitView(); render();
  };

  // legend (click to toggle a position on/off)
  function renderLegend() {
    document.getElementById('dnaLegend').innerHTML = GROUP_ORDER.map(g =>
      `<button class="dna-leg ${dim.has(g) ? 'off' : ''}" data-g="${g}">
        <span class="dot" style="background:${COLORS[g]}"></span>${g}</button>`).join('');
    document.querySelectorAll('.dna-leg').forEach(b => b.onclick = () => {
      const g = b.dataset.g; dim.has(g) ? dim.delete(g) : dim.add(g); renderLegend(); render();
    });
  }

  // map search
  (function () {
    const input = document.getElementById('dnaSearch'), dd = document.getElementById('dnaDD');
    const hide = () => { dd.style.display = 'none'; };
    input.oninput = () => {
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) { hide(); return; }
      const hits = PTS.filter(p => p.name.toLowerCase().includes(q)).slice(0, 7);
      if (!hits.length) { hide(); return; }
      dd.innerHTML = hits.map(p => `<div class="card-dd-it" data-n="${p.name.replace(/"/g, '&quot;')}">
        <b>${p.name}</b><span>${p.group_label} · ${p.team || ''}</span></div>`).join('');
      dd.style.display = 'block';
      dd.querySelectorAll('.card-dd-it').forEach(el => el.onclick = () => {
        input.value = ''; hide();
        const p = byName.get(el.dataset.n); if (!p) return;
        centerOn(p, Math.max(cam.scale, 26)); spotlight(byName.get(el.dataset.n)._i);
      });
    };
    document.addEventListener('click', (e) => { if (!input.contains(e.target) && !dd.contains(e.target)) hide(); });
  })();

  window.addEventListener('resize', () => { resize(); render(); });

  (async function init() {
    let d;
    try { d = await api('/api/dna_map'); } catch { return; }
    if (!d || !d.available) { stage.innerHTML = '<p style="padding:30px;color:#9aa3b8">Map unavailable.</p>'; return; }
    PTS = d.points;
    PTS.forEach((p, i) => { p._i = i; byName.set(p.name, p); });
    document.getElementById('axX').textContent = d.axes.x + ' →';
    document.getElementById('axY').textContent = '↑ ' + d.axes.y;
    resize(); renderLegend(); fitView();
    const focusName = new URLSearchParams(location.search).get('focus');
    if (focusName) {
      const p = byName.get(focusName) || PTS.find(x => x.name.toLowerCase().includes(focusName.toLowerCase()));
      if (p) { centerOn(p, 26); spotlight(p._i); }
    }
    render();
  })();
})();
