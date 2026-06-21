renderSidebar('Live Matches');
attachSearchDropdown(document.getElementById('searchBox'));

const EID = new URLSearchParams(location.search).get('id');
const TABS = [['stats', 'Stats'], ['lineups', 'Lineups'], ['shotmap', 'Shot Map'],
              ['timeline', 'Timeline'], ['players', 'Players'], ['heatmaps', 'Heatmaps']];
const TAB_KEYS = TABS.map(([k]) => k);
let head = null, timer = null;
let active = TAB_KEYS.includes(new URLSearchParams(location.search).get('tab'))
  ? new URLSearchParams(location.search).get('tab') : 'stats';
let playersCache = null, playerSort = { key: 'rating', dir: -1 };
const heatCache = {};                       // player_id -> {points}

const A = (p) => api(p + (p.includes('?') ? '&' : '?') + 'id=' + encodeURIComponent(EID));
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const pct = (h, a) => { const t = (+h || 0) + (+a || 0); return t ? Math.round((+h || 0) / t * 100) : 50; };

// ---- hero header ----
function badge(side) {
  return teamBadge({ [side]: head[side], [side + '_country']: head[side + '_country'], [side + '_logo']: null }, side);
}
function heroStatus() {
  if (!head.available) return '';
  if (head.status === 'inprogress') return `<span class="live">● ${head.minute ? head.minute + "'" : (head.status_desc || 'LIVE')}</span>`;
  if (head.status === 'finished') return `<span class="tag">Full time</span>`;
  const ko = new Date(head.start_ts * 1000);
  return `<span class="tag">${ko.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })} · ${ko.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>`;
}
async function loadHeader() {
  head = await A('/api/match');
  const hero = document.getElementById('hero');
  if (!head.available) { hero.innerHTML = '<div class="placeholder-note">Match not found.</div>'; return; }
  const played = head.home_score != null;
  hero.innerHTML = `
    <div class="mh-comp">${esc(head.competition || '')}${head.round ? ' · ' + esc(head.round) : ''}</div>
    <div class="mh-row">
      <div class="mh-team home">${badge('home')}<span class="mh-name">${esc(head.home)}</span></div>
      <div class="mh-score">${played ? `${head.home_score}<span class="dash">-</span>${head.away_score}` : '<span class="vs">vs</span>'}
        <div class="mh-st">${heroStatus()}</div></div>
      <div class="mh-team away"><span class="mh-name">${esc(head.away)}</span>${badge('away')}</div>
    </div>`;
}

// ---- tabs ----
function renderTabs() {
  const el = document.getElementById('matchTabs');
  el.innerHTML = TABS.map(([k, label]) => `<span class="tab ${k === active ? 'active' : ''}" data-k="${k}">${label}</span>`).join('');
  el.querySelectorAll('.tab').forEach(t => t.onclick = () => { active = t.dataset.k; renderTabs(); loadActive(); });
}
const body = () => document.getElementById('tabBody');
const LOADERS = { stats: loadStats, lineups: loadLineups, shotmap: loadShotmap, timeline: loadTimeline, players: loadPlayers, heatmaps: loadHeatmaps };
async function loadActive(refresh) {
  if (!refresh) body().innerHTML = '<section class="card"><div class="placeholder-note">Loading…</div></section>';
  try { await LOADERS[active](); } catch { body().innerHTML = '<section class="card"><div class="placeholder-note">Could not load this section.</div></section>'; }
}

// ---- Stats ----
async function loadStats() {
  const d = await A('/api/match/stats');
  if (!d.available) { body().innerHTML = empty('No match statistics yet — check back once the match kicks off.'); return; }
  body().innerHTML = d.groups.map(g => `
    <section class="card stat-group">
      <div class="card-h"><h3>${esc(g.name)}</h3></div>
      ${g.items.map(it => {
        const hv = it.home_value, av = it.away_value;
        const numeric = typeof hv === 'number' && typeof av === 'number';
        const hp = numeric ? pct(hv, av) : 50;
        // emphasise the leading side, dim the trailing one
        const lead = numeric ? (hv > av ? 'home' : av > hv ? 'away' : '') : '';
        const hcls = lead === 'home' ? 'lead' : lead === 'away' ? 'lo' : '';
        const acls = lead === 'away' ? 'lead' : lead === 'home' ? 'lo' : '';
        return `<div class="stat-row">
          <span class="sv home ${hcls}">${esc(it.home ?? '—')}</span>
          <span class="sl">${esc(it.name)}</span>
          <span class="sv away ${acls}">${esc(it.away ?? '—')}</span>
          <div class="sbar"><i class="h" style="width:${hp}%"></i><i class="a" style="width:${100 - hp}%"></i></div>
        </div>`;
      }).join('')}
    </section>`).join('');
}

// ---- Lineups (formation pitch) ----
const _surname = (n) => { const p = String(n || '').trim().split(' '); return p.length > 1 ? p[p.length - 1] : n; };

// formation "4-2-3-1" + XI length -> row sizes [GK, ...lines] (back -> front), or
// null if it doesn't add up (predicted / exotic -> caller falls back to a list).
function parseFormation(f, xiLen) {
  if (!f) return null;
  const parts = String(f).split('-').map(n => parseInt(n, 10)).filter(n => n > 0);
  if (!parts.length || parts.reduce((a, b) => a + b, 0) + 1 !== xiLen) return null;
  return [1, ...parts];
}
// place each XI player at an {x,y} on a vertical pitch (0..1). Home defends the
// bottom (GK low), away the top (mirrored), both attacking the halfway line.
function placeSide(xi, rows, isHome) {
  const out = [], n = rows.length; let idx = 0;
  for (let ri = 0; ri < n; ri++) {
    const k = rows[ri], t = n === 1 ? 0 : ri / (n - 1);
    let y = 0.93 - t * (0.93 - 0.57);
    if (!isHome) y = 1 - y;
    for (let j = 0; j < k; j++) out.push({ x: 0.09 + 0.82 * ((j + 0.5) / k), y, p: xi[idx++] });
  }
  return out;
}
function chipHTML({ x, y, p }, isHome) {
  const rt = p.rating != null
    ? `<i class="luc-rt" style="background:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</i>` : '';
  const cap = p.captain ? '<i class="luc-cap">C</i>' : '';
  return `<div class="luc ${isHome ? 'h' : 'a'}" style="left:${(x * 100).toFixed(1)}%;top:${(y * 100).toFixed(1)}%"
      onclick="location.href='/player.html?name=${encodeURIComponent(p.name)}'" title="${esc(p.name)}${p.position ? ' · ' + esc(p.position) : ''}">
      <span class="luc-dot">${p.number ?? ''}${rt}${cap}</span>
      <span class="luc-nm">${esc(_surname(p.name))}</span></div>`;
}
// fallback list (used when a formation can't be parsed, e.g. predicted lineups)
function lineupSideList(s, label) {
  if (!s) return '';
  const row = (p) => `<div class="lu-row"><span class="lu-no">${p.number ?? ''}</span>
    <span class="lu-nm">${esc(p.name)}</span><span class="lu-pos">${esc(p.position || '')}</span>
    ${p.rating != null ? `<span class="ratingchip sm" style="border-color:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>` : ''}</div>`;
  return `<section class="card lu-col">
    <div class="card-h"><h3>${esc(label)}</h3><span class="see">${esc(s.formation || '')}</span></div>
    ${(s.starting_xi || []).map(row).join('') || '<div class="placeholder-note">No starting XI.</div>'}
    ${(s.substitutes || []).length ? `<div class="lu-sub-h">Substitutes</div>${s.substitutes.map(row).join('')}` : ''}
  </section>`;
}
function subsCol(s, label) {
  if (!s || !(s.substitutes || []).length) return '';
  const row = (p) => `<div class="lu-row"><span class="lu-no">${p.number ?? ''}</span>
    <span class="lu-nm" onclick="location.href='/player.html?name=${encodeURIComponent(p.name)}'" style="cursor:pointer">${esc(p.name)}</span>
    <span class="lu-pos">${esc(p.position || '')}</span>
    ${p.rating != null ? `<span class="ratingchip sm" style="border-color:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>` : ''}</div>`;
  return `<section class="card lu-col"><div class="card-h"><h3>${esc(label)} — subs</h3></div>${s.substitutes.map(row).join('')}</section>`;
}
async function loadLineups() {
  const d = await A('/api/match/lineups');
  if (!d.available) { body().innerHTML = empty('Lineups not published yet.'); return; }
  const note = d.confirmed ? '' : '<div class="placeholder-note" style="margin-bottom:10px">⚠ Predicted lineup — not yet confirmed.</div>';
  const hx = d.home?.starting_xi || [], ax = d.away?.starting_xi || [];
  const hRows = parseFormation(d.home?.formation, hx.length);
  const aRows = parseFormation(d.away?.formation, ax.length);
  if (hRows && aRows) {
    const chips = placeSide(hx, hRows, true).map(c => chipHTML(c, true)).join('')
                + placeSide(ax, aRows, false).map(c => chipHTML(c, false)).join('');
    body().innerHTML = note + `
      <section class="card">
        <div class="lp-head a"><span>${esc(head?.away || 'Away')}</span><b>${esc(d.away?.formation || '')}</b></div>
        <div class="lineup-pitch">
          <span class="lp-mid"></span><span class="lp-circle"></span><span class="lp-spot"></span>
          <span class="lp-box top"></span><span class="lp-box bot"></span>
          <span class="lp-six top"></span><span class="lp-six bot"></span>
          ${chips}
        </div>
        <div class="lp-head h"><b>${esc(d.home?.formation || '')}</b><span>${esc(head?.home || 'Home')}</span></div>
      </section>
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:16px;margin-top:14px">
        ${subsCol(d.home, head?.home || 'Home')}${subsCol(d.away, head?.away || 'Away')}</div>`;
  } else {
    body().innerHTML = note + `<div class="grid" style="grid-template-columns:1fr 1fr;gap:16px">
      ${lineupSideList(d.home, head?.home || 'Home')}${lineupSideList(d.away, head?.away || 'Away')}</div>`;
  }
}

// ---- Shot map ----
async function loadShotmap() {
  const d = await A('/api/match/shotmap');
  if (!d.available) { body().innerHTML = empty('No shot-map data for this match.'); return; }
  const W = 105, H = 68;
  const dot = (s) => {
    if (s.x == null || s.y == null) return '';
    // SofaScore shot x is measured toward the attacking goal (100 = the goal being
    // shot at). Put home on the left attacking right, away attacking left.
    const cx = (s.is_home ? 100 - s.x : s.x) / 100 * W;
    const cy = s.y / 100 * H;
    const r = 1.1 + (s.xg || 0) * 6;
    const cls = s.is_goal ? 'goal' : s.is_on_target ? 'ontarget' : 'offtarget';
    const tip = `${s.player} · ${s.minute ?? ''}' · ${s.shot_type || ''} · xG ${(s.xg || 0).toFixed(2)} · ${s.body_part || ''}`;
    return `<circle class="shot ${cls}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}"><title>${esc(tip)}</title></circle>`;
  };
  const goals = d.shots.filter(s => s.is_goal).length;
  body().innerHTML = `<section class="card">
    <div class="card-h"><h3>Shot Map</h3><span class="see">${d.shots.length} shots · ${goals} goals</span></div>
    <div class="pitch-wrap"><svg viewBox="0 0 ${W} ${H}" class="pitch-svg">
      <rect x="0.5" y="0.5" width="${W - 1}" height="${H - 1}" class="pl"/>
      <line x1="${W / 2}" y1="0.5" x2="${W / 2}" y2="${H - 0.5}" class="pl"/>
      <circle cx="${W / 2}" cy="${H / 2}" r="9" class="pl"/>
      <rect x="0.5" y="${H / 2 - 20}" width="16" height="40" class="pl"/>
      <rect x="${W - 16.5}" y="${H / 2 - 20}" width="16" height="40" class="pl"/>
      ${d.shots.map(dot).join('')}
    </svg></div>
    <div class="legend"><span><i class="lg goal"></i>Goal</span><span><i class="lg ontarget"></i>On target</span><span><i class="lg offtarget"></i>Off / blocked</span><span class="muted">○ size = xG · ${esc(head?.home || 'home')} → ← ${esc(head?.away || 'away')}</span></div>
  </section>`;
}

// ---- Timeline ----
const TL_ICON = { goal: '⚽', card: '🟨', substitution: '🔄', period: '⏱', var: '📺' };
async function loadTimeline() {
  const d = await A('/api/match/timeline');
  if (!d.available || !d.events.length) { body().innerHTML = empty('Timeline not available for this match.'); return; }
  const rows = d.events.map(e => {
    if (e.type === 'period') return `<div class="tl-period">${esc(e.text || '')}</div>`;
    if (e.type === 'injuryTime') return '';
    const icon = e.type === 'card' ? (/(red)/i.test(e.klass || '') ? '🟥' : '🟨') : (TL_ICON[e.type] || '•');
    let txt;
    if (e.type === 'goal') txt = `<b>${esc(e.player)}</b> <span class="muted">${e.home_score}-${e.away_score}</span>`;
    else if (e.type === 'substitution') txt = `<b>${esc(e.player_in)}</b> <span class="muted">↑ ${esc(e.player_out)} ↓</span>`;
    else if (e.type === 'card') txt = `<b>${esc(e.player)}</b>${e.detail ? ` <span class="muted">${esc(e.detail)}</span>` : ''}`;
    else txt = esc(e.player || e.klass || e.type);
    const mins = (e.minute ?? '') + (e.added_time ? '+' + e.added_time : '');
    return `<div class="tl-row ${e.side || ''}"><span class="tl-min">${mins}'</span>
      <span class="tl-ic">${icon}</span><span class="tl-tx">${txt}</span></div>`;
  }).join('');
  body().innerHTML = `<section class="card"><div class="card-h"><h3>Match Timeline</h3></div><div class="tl">${rows}</div></section>`;
}

// ---- Players (sortable) ----
const PCOLS = [
  ['name', 'Player', 1], ['rating', 'Rating', 0], ['minutes', 'Min', 0], ['goals', 'G', 0],
  ['assists', 'A', 0], ['shots', 'Sh', 0], ['shots_on_target', 'SoT', 0], ['xg', 'xG', 0],
  ['xa', 'xA', 0], ['passes', 'Pass', 0], ['key_passes', 'KeyP', 0], ['tackles', 'Tkl', 0],
  ['duels_won', 'DuelW', 0], ['fouls', 'Fls', 0],
];
function renderPlayers() {
  const rows = [...playersCache].sort((a, b) => {
    const k = playerSort.key, va = a[k] ?? -1, vb = b[k] ?? -1;
    if (typeof va === 'string') return playerSort.dir * va.localeCompare(vb);
    return playerSort.dir * ((va || 0) - (vb || 0));
  });
  const fmt = (v, k) => v == null ? '—' : (k === 'xg' || k === 'xa' || k === 'rating') ? (+v).toFixed(k === 'rating' ? 1 : 2) : v;
  const cards = (p) => (p.yellow ? '🟨'.repeat(p.yellow) : '') + (p.red ? '🟥' : '');
  body().innerHTML = `<section class="card"><div class="card-h"><h3>Player Stats</h3><span class="see">${rows.length} players</span></div>
    <div class="ltbl-wrap"><table class="ltbl pl-tbl">
      <thead><tr>${PCOLS.map(([k, l]) => `<th data-k="${k}" class="${k === playerSort.key ? 'sorted' : ''} ${k === 'name' ? 'tl' : ''}">${l}${k === playerSort.key ? (playerSort.dir < 0 ? ' ▾' : ' ▴') : ''}</th>`).join('')}<th>Cards</th></tr></thead>
      <tbody>${rows.map(p => `<tr class="${p.started ? '' : 'sub-row'}">
        ${PCOLS.map(([k]) => k === 'name'
          ? `<td class="tl"><span class="pl-no">${p.number ?? ''}</span>${esc(p.name)} <span class="muted">${esc(p.position || '')} · ${esc((p.team || '').slice(0, 12))}</span></td>`
          : k === 'rating'
            ? `<td>${p.rating != null ? `<span class="ratingchip sm" style="border-color:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>` : '—'}</td>`
            : `<td>${fmt(p[k], k)}</td>`).join('')}
        <td>${cards(p) || ''}</td></tr>`).join('')}</tbody>
    </table></div></section>`;
  body().querySelectorAll('th[data-k]').forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    playerSort = { key: k, dir: playerSort.key === k ? -playerSort.dir : (k === 'name' ? 1 : -1) };
    renderPlayers();
  });
}
async function loadPlayers() {
  const d = await A('/api/match/player-stats');
  if (!d.available) { body().innerHTML = empty('Per-player stats not available yet.'); return; }
  playersCache = d.players;
  renderPlayers();
}

// ---- Heatmaps ----
function heatColor(v) {
  v = Math.min(1, v); const t = Math.min(1, v * 1.4);
  const hue = 145 - 145 * t, light = 50 + 12 * t, alpha = Math.max(0, Math.min(0.95, (v - 0.04) * 1.8));
  return `hsla(${hue},100%,${light}%,${alpha})`;
}
function drawPitch(ctx, W, H) {
  ctx.strokeStyle = 'rgba(255,255,255,.18)'; ctx.lineWidth = 1.2;
  ctx.strokeRect(2, 2, W - 4, H - 4);
  ctx.beginPath(); ctx.moveTo(W / 2, 2); ctx.lineTo(W / 2, H - 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.min(W, H) * 0.13, 0, 2 * Math.PI); ctx.stroke();
  const bw = W * 0.14, bh = H * 0.5;
  ctx.strokeRect(2, (H - bh) / 2, bw, bh); ctx.strokeRect(W - 2 - bw, (H - bh) / 2, bw, bh);
}
function drawPoints(canvas, points) {
  const ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#0e1f17'; ctx.fillRect(0, 0, W, H);
  const GW = 24, GH = 16, grid = Array.from({ length: GH }, () => new Array(GW).fill(0));
  let max = 0;
  for (const p of points) {
    if (p.x == null || p.y == null) continue;
    const gx = Math.min(GW - 1, Math.floor(p.x / 100 * GW));
    const gy = Math.min(GH - 1, Math.floor(p.y / 100 * GH));
    grid[gy][gx]++; if (grid[gy][gx] > max) max = grid[gy][gx];
  }
  const cw = W / GW, ch = H / GH;
  ctx.save(); ctx.filter = 'blur(8px)';
  for (let r = 0; r < GH; r++) for (let c = 0; c < GW; c++) {
    const v = grid[r][c] / (max || 1);
    if (v > 0.05) { ctx.fillStyle = heatColor(v); ctx.fillRect(c * cw, r * ch, cw + 1.5, ch + 1.5); }
  }
  ctx.restore(); drawPitch(ctx, W, H);
}
async function loadHeatmaps() {
  const lu = await A('/api/match/lineups');
  if (!lu.available) { body().innerHTML = empty('No lineup, so no heatmaps available.'); return; }
  const sides = [['home', head?.home || 'Home', lu.home], ['away', head?.away || 'Away', lu.away]];
  let team = 'home';
  body().innerHTML = `<section class="card">
    <div class="card-h"><h3>Player Heatmaps</h3>
      <div class="tabs sm" id="hmTeam">${sides.map(([k, nm]) => `<span class="tab ${k === 'home' ? 'active' : ''}" data-k="${k}">${esc(nm)}</span>`).join('')}</div></div>
    <div class="placeholder-note" id="hmNote">Attacking left → right. Heatmaps load per player.</div>
    <div class="hm-grid" id="hmGrid"></div></section>`;
  async function renderTeam(k) {
    team = k;
    document.querySelectorAll('#hmTeam .tab').forEach(t => t.classList.toggle('active', t.dataset.k === k));
    const side = sides.find(s => s[0] === k)[2];
    const xi = (side.starting_xi || []);
    const grid = document.getElementById('hmGrid');
    grid.innerHTML = xi.map(p => `<div class="hm-cell"><canvas width="150" height="98" id="hm_${p.id}"></canvas>
      <div class="hm-nm">${p.number ?? ''} ${esc(p.name)}</div></div>`).join('') || '<div class="placeholder-note">No starters.</div>';
    // fetch sequentially-ish (small fan-out) so we stay friendly to the API
    for (const p of xi) {
      let h = heatCache[p.id];
      if (!h) { h = await A('/api/match/heatmap?player_id=' + p.id); heatCache[p.id] = h; }
      const cv = document.getElementById('hm_' + p.id);
      if (!cv) continue;                       // user switched team mid-load
      if (h.available) drawPoints(cv, h.points);
      else { const c = cv.getContext('2d'); c.fillStyle = '#0e1f17'; c.fillRect(0, 0, cv.width, cv.height); drawPitch(c, cv.width, cv.height); c.fillStyle = '#7f8aa3'; c.font = '11px Inter'; c.textAlign = 'center'; c.fillText('no data', cv.width / 2, cv.height / 2); }
    }
  }
  document.querySelectorAll('#hmTeam .tab').forEach(t => t.onclick = () => renderTeam(t.dataset.k));
  renderTeam('home');
}

const empty = (msg) => `<section class="card"><div class="placeholder-note">${esc(msg)}</div></section>`;

// ---- boot + live polling ----
(async () => {
  if (!EID) { document.getElementById('hero').innerHTML = empty('No match selected.'); return; }
  await loadHeader();
  renderTabs();
  await loadActive();
  if (head?.status === 'inprogress') {
    timer = setInterval(async () => {
      await loadHeader();
      if (['stats', 'timeline', 'players'].includes(active)) loadActive(true);
      if (head?.status !== 'inprogress') clearInterval(timer);   // stop once final
    }, 30000);
  }
})();
