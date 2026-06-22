renderSidebar('Player Cards');
attachSearchDropdown(document.getElementById('searchBox'));

const params = new URLSearchParams(location.search);
const canvas = document.getElementById('card');
const ctx = canvas.getContext('2d');
const S = 2;                       // retina scale
const W = canvas.width / S, H = canvas.height / S;
let current = null;                // last card data (for download/share)

// ---- rating tier -> palette ----
function tier(r) {
  if (r >= 89) return { a: '#f5e3a3', a2: '#d9b24a', frame: '#ecca6b', glow: 'rgba(236,202,107,.55)', tint: '#2a2410', label: 'ELITE' };
  if (r >= 83) return { a: '#f2d27a', a2: '#caa23f', frame: '#dcb854', glow: 'rgba(220,184,84,.45)', tint: '#241f10', label: 'GOLD' };
  if (r >= 76) return { a: '#dbe1ec', a2: '#9aa6ba', frame: '#c4ccd9', glow: 'rgba(196,204,217,.4)', tint: '#1b2330', label: 'SILVER' };
  return { a: '#ecc196', a2: '#bd8455', frame: '#d69b69', glow: 'rgba(214,155,105,.4)', tint: '#2a2016', label: 'BRONZE' };
}

function rr(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function fitFont(text, max, start, weight = '800') {
  let s = start;
  ctx.font = `${weight} ${s}px Inter`;
  while (ctx.measureText(text).width > max && s > 10) { s -= 1; ctx.font = `${weight} ${s}px Inter`; }
  return s;
}
const cardInitials = (n) => n.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();

function draw(d, img) {
  const t = tier(d.rating || 60);
  ctx.setTransform(S, 0, 0, S, 0, 0);
  ctx.clearRect(0, 0, W, H);

  // base + clip
  rr(0, 0, W, H, 22);
  ctx.save();
  ctx.clip();
  let g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, '#19212f'); g.addColorStop(.55, '#10151f'); g.addColorStop(1, '#0a0e16');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  // tier sheen top
  g = ctx.createRadialGradient(W * 0.3, 70, 10, W * 0.3, 70, 260);
  g.addColorStop(0, t.glow); g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, 240);

  // photo (cover, faded into card)
  const px = 150, py = 16, pw = W - px - 16, ph = 206;
  if (img) {
    ctx.save();
    rr(px, py, pw, ph, 12); ctx.clip();
    const ar = img.width / img.height, tr = pw / ph;
    let dw = pw, dh = ph, ox = 0, oy = 0;
    if (ar > tr) { dh = ph; dw = ph * ar; ox = (pw - dw) / 2; } else { dw = pw; dh = pw / ar; oy = (ph - dh) / 2; }
    ctx.drawImage(img, px + ox, py + oy, dw, dh);
    const fg = ctx.createLinearGradient(0, py + ph - 70, 0, py + ph);
    fg.addColorStop(0, 'rgba(16,21,31,0)'); fg.addColorStop(1, '#10151f');
    ctx.fillStyle = fg; ctx.fillRect(px, py + ph - 70, pw, 70);
    ctx.restore();
  } else {
    ctx.fillStyle = '#1c2433';
    ctx.beginPath(); ctx.arc(px + pw / 2, py + ph / 2 - 6, 52, 0, 7); ctx.fill();
    ctx.fillStyle = '#5a6781'; ctx.font = '800 40px Inter'; ctx.textAlign = 'center';
    ctx.fillText(cardInitials(d.name), px + pw / 2, py + ph / 2 + 8);
  }

  // rating + position (top-left)
  ctx.textAlign = 'left';
  ctx.fillStyle = t.a; ctx.font = '900 60px Inter';
  ctx.fillText(d.rating ?? '–', 24, 80);
  ctx.fillStyle = '#fff'; ctx.font = '800 20px Inter';
  ctx.fillText((d.position || '').toUpperCase(), 28, 106);
  ctx.fillStyle = t.a; ctx.font = '800 11px Inter';
  ctx.fillText(t.label, 29, 126);
  // accent divider under rating block
  ctx.strokeStyle = t.frame; ctx.globalAlpha = .5; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(28, 136); ctx.lineTo(108, 136); ctx.stroke(); ctx.globalAlpha = 1;

  // name
  ctx.textAlign = 'center';
  const nm = d.name.toUpperCase();
  const ns = fitFont(nm, W - 44, 28, '900');
  ctx.fillStyle = '#fff'; ctx.font = `900 ${ns}px Inter`;
  ctx.fillText(nm, W / 2, 250);
  // club · nation
  ctx.fillStyle = '#aeb6c6'; ctx.font = '600 13px Inter';
  ctx.fillText([d.team, d.nationality].filter(Boolean).join('  ·  '), W / 2, 272);

  // archetype badge
  if (d.archetype) {
    const txt = d.fit ? `${d.archetype} · ${d.fit}%` : d.archetype;
    ctx.font = '700 12px Inter';
    const bw = Math.min(W - 48, ctx.measureText(txt).width + 30), bx = (W - bw) / 2, by = 286;
    rr(bx, by, bw, 26, 13);
    ctx.fillStyle = 'rgba(255,255,255,.05)'; ctx.fill();
    ctx.strokeStyle = t.frame; ctx.globalAlpha = .55; ctx.lineWidth = 1.2; ctx.stroke(); ctx.globalAlpha = 1;
    ctx.fillStyle = t.a; ctx.fillText(txt, W / 2, by + 17);
  }

  // stat rows
  let y = 336;
  const x0 = 36, x1 = W - 36, trkA = 92, trkB = W - 92;
  for (const s of d.stats) {
    ctx.textAlign = 'left'; ctx.fillStyle = '#cfd6e6'; ctx.font = '800 13px Inter';
    ctx.fillText(s.code, x0, y + 4);
    ctx.textAlign = 'right'; ctx.fillStyle = t.a; ctx.font = '800 15px Inter';
    ctx.fillText(s.value, x1, y + 5);
    // track + fill
    rr(trkA, y - 3, trkB - trkA, 6, 3); ctx.fillStyle = 'rgba(255,255,255,.09)'; ctx.fill();
    const fw = Math.max(4, (s.value / 99) * (trkB - trkA));
    const fg = ctx.createLinearGradient(trkA, 0, trkA + fw, 0);
    fg.addColorStop(0, t.a2); fg.addColorStop(1, t.a);
    rr(trkA, y - 3, fw, 6, 3); ctx.fillStyle = fg; ctx.fill();
    y += 28;
  }

  // footer brand
  ctx.textAlign = 'left';
  ctx.strokeStyle = '#5570f0'; ctx.lineWidth = 2.4; ctx.lineJoin = 'round';
  ctx.beginPath(); ctx.moveTo(26, H - 18); ctx.lineTo(40, H - 38); ctx.lineTo(54, H - 18); ctx.closePath(); ctx.stroke();
  ctx.fillStyle = '#e9edf6'; ctx.font = '900 14px Inter';
  ctx.fillText('ATLASTRA', 62, H - 22);
  ctx.textAlign = 'right'; ctx.fillStyle = '#7c879b'; ctx.font = '700 12px Inter';
  ctx.fillText(seasonLabel(d.season), W - 26, H - 22);

  ctx.restore();
  // frame
  rr(1.2, 1.2, W - 2.4, H - 2.4, 21);
  ctx.strokeStyle = t.frame; ctx.lineWidth = 2.4; ctx.stroke();
  ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.lineWidth = 1; rr(4, 4, W - 8, H - 8, 18); ctx.stroke();
}

function seasonLabel(s) {
  return s && s.length === 4 ? `20${s.slice(0, 2)}/${s.slice(2)}` : (s || '');
}

async function load(name) {
  if (!name) return;
  document.getElementById('crumb').textContent = name;
  document.getElementById('profileLink').href = '/player.html?name=' + encodeURIComponent(name);
  let d;
  try { d = await api('/api/card?name=' + encodeURIComponent(name)); } catch { return; }
  if (!d.available) return;
  current = d;
  history.replaceState(null, '', '?name=' + encodeURIComponent(name));
  if (d.photo) {
    const img = new Image();
    img.onload = () => draw(d, img);
    img.onerror = () => draw(d, null);
    img.src = '/api/img?u=' + encodeURIComponent(d.photo);
  } else {
    draw(d, null);
  }
}

document.getElementById('dl').onclick = () => {
  if (!current) return;
  canvas.toBlob((b) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = `${current.name.replace(/\s+/g, '_')}_atlastra_card.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }, 'image/png');
};

document.getElementById('sh').onclick = () => {
  if (!current) return;
  canvas.toBlob(async (b) => {
    const file = new File([b], `${current.name}_atlastra.png`, { type: 'image/png' });
    const data = { files: [file], title: `${current.name} — Atlastra card`, text: `${current.name} · ${current.rating} · ${current.archetype || ''}` };
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share(data); return; } catch { /* fall through */ }
    }
    document.getElementById('dl').click();
  }, 'image/png');
};

// inline player search for the card
(function cardSearch() {
  const input = document.getElementById('cardSearch'), dd = document.getElementById('cardDD');
  let t;
  const hide = () => { dd.style.display = 'none'; };
  input.oninput = () => {
    clearTimeout(t);
    const q = input.value.trim();
    if (q.length < 2) { hide(); return; }
    t = setTimeout(async () => {
      let r; try { r = await api('/api/search?q=' + encodeURIComponent(q)); } catch { return; }
      const players = (r.players || []).slice(0, 7);
      if (!players.length) { hide(); return; }
      dd.innerHTML = players.map(p =>
        `<div class="card-dd-it" data-n="${(p.name || p.player || '').replace(/"/g, '&quot;')}">
          <b>${p.name || p.player}</b><span>${[p.team, p.position].filter(Boolean).join(' · ')}</span></div>`).join('');
      dd.style.display = 'block';
      dd.querySelectorAll('.card-dd-it').forEach(el => el.onclick = () => {
        input.value = ''; hide(); load(el.dataset.n);
      });
    }, 200);
  };
  document.addEventListener('click', (e) => { if (!input.contains(e.target) && !dd.contains(e.target)) hide(); });
})();

(async function init() {
  let name = params.get('name');
  if (!name) {
    try { const top = await api('/api/players?limit=1'); name = (top[0] || {}).player || (top[0] || {}).name; } catch { /* */ }
  }
  load(name || 'Pedri');
})();
