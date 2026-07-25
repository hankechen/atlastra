// Tactics Lab — two fully-editable teams (A/B), tactic sliders, explainable projection,
// opponent matchup, AI advisor, and a post-sim shape + passing-network visualization.
renderSidebar('Tactics');

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const S = {
  active: 'A',
  sides: { A: blankSide('Real Madrid'), B: blankSide('') },
  roles: {}, roleDefaults: {}, formations: [],
  lastMetrics: { A: null, B: null }, lastChem: { A: null, B: null }, sim: null,
};
function blankSide(team) { return { team, formation: '4-3-3', xi: [], squad: [], tactics: {}, subs: [] }; }
const cur = () => S.sides[S.active];
const other = () => S.sides[S.active === 'A' ? 'B' : 'A'];
const hasB = () => !!S.sides.B.team && S.sides.B.xi.length;

const TACTICS_META = [
  ['In Possession', [
    ['tempo', 'Tempo', 'Patient', 'Fast'], ['directness', 'Directness', 'Build-up', 'Direct'],
    ['width', 'Width', 'Narrow', 'Wide'], ['patience', 'Final-third patience', 'Quick', 'Patient'],
    ['counter', 'Approach', 'Possession', 'Counter'],
  ]],
  ['Out of Possession', [
    ['line_height', 'Defensive line', 'Deep', 'High'], ['press', 'Press intensity', 'Passive', 'Aggressive'],
    ['compactness', 'Compactness', 'Open', 'Compact'],
  ]],
];
// entries are DB team name, or [db_name, friendly_label] where the DB stores a different name
const CLUBS = ['Real Madrid', 'Barcelona', 'Manchester City', 'Arsenal', 'Liverpool',
  ['Bayern München', 'Bayern Munich'], 'PSG', ['Internazionale', 'Inter'], 'Atlético Madrid',
  'Bayer Leverkusen', 'Manchester United', 'Chelsea', ['Tottenham Hotspur', 'Tottenham'],
  'Newcastle United', 'Napoli', ['Milan', 'AC Milan'], 'Juventus', 'Borussia Dortmund',
  'Aston Villa', 'Bournemouth'];
const NATIONS = ['Argentina', 'France', 'Brazil', 'England', 'Spain', 'Germany', 'Portugal', 'Netherlands',
  'Belgium', 'Croatia', 'Morocco', 'Uruguay', 'Colombia', 'Mexico', 'Japan', 'USA'];
function teamOptions(withNone) {
  const opt = (t) => { const v = Array.isArray(t) ? t[0] : t, l = Array.isArray(t) ? t[1] : t; return `<option value="${esc(v)}">${esc(l)}</option>`; };
  const grp = (lbl, arr) => `<optgroup label="${lbl}">${arr.map(opt).join('')}</optgroup>`;
  return (withNone ? '<option value="">none — profile mode</option>' : '') + grp('Clubs', CLUBS) + grp('National teams', NATIONS);
}
function ensureOption(sel, val) {   // keep a deep-linked/custom team selectable
  if (val && !Array.from(sel.options).some((o) => o.value === val)) sel.insertBefore(new Option(val, val), sel.firstChild);
  sel.value = val;
}
function fillTeams() {
  document.getElementById('teamInput').innerHTML = teamOptions(false);
  document.getElementById('oppInput').innerHTML = teamOptions(true);
}

// ---- data ----
async function loadSide(key) {
  const sd = S.sides[key];
  if (!sd.team) { sd.xi = []; sd.squad = []; return; }
  let r; try { r = await api(`/api/tactics/squad?team=${encodeURIComponent(sd.team)}&formation=${encodeURIComponent(sd.formation)}`); } catch { r = null; }
  if (!r || !r.available) { sd.xi = []; sd.squad = []; sd.error = true; return; }
  sd.xi = r.xi; sd.squad = r.squad; sd.tactics = { ...(r.tactic_defaults || {}) }; sd.error = false;
  S.roles = r.roles; S.roleDefaults = r.role_defaults; S.formations = r.formations;
}
async function loadAll() {
  document.getElementById('tlBody').innerHTML = '<div class="tl-loading">Loading squads…</div>';
  await loadSide('A');
  if (S.sides.B.team) await loadSide('B');
  if (cur().error) { document.getElementById('tlBody').innerHTML = `<div class="empty-state">No squad data for “${esc(cur().team)}”. Try a top-5-league team.</div>`; return; }
  const fs = document.getElementById('formSel');
  fs.innerHTML = S.formations.map((f) => `<option${f === cur().formation ? ' selected' : ''}>${f}</option>`).join('');
  S.lastMetrics = { A: null, B: null };
  render(); runSim();
}
async function runSim() {
  const a = cur(), b = other();
  const payload = { team: a.team, xi: a.xi, tactics: a.tactics };
  if (b.team && b.xi.length) payload.opponent = { team: b.team, xi: b.xi, tactics: b.tactics };
  let r; try { r = await fetch('/api/tactics/sim', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then((x) => x.json()); } catch { return; }
  S.sim = r; renderResults(r);
}
let _simT; const debouncedSim = () => { clearTimeout(_simT); _simT = setTimeout(runSim, 260); };

// ---- render pitch + tactics ----
function chipHTML(s) {
  const p = s.player, nm = p ? p.player.split(' ').slice(-1)[0] : '—';
  const ph = p && p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy" draggable="false">` : '';
  const brk = p && p.breakout ? `<i class="tl-brk" title="Breakout season — Atlas rating above FIFA, rating boosted +${p.breakout}">⚡</i>` : '';
  return `<button class="tl-chip" style="left:${s.x}%;bottom:${s.y}%" data-slot="${s.id}">
      <span class="tl-phwrap"><span class="tl-ph">${ph}</span><i class="tl-rt">${p ? p.rating : '-'}</i>${brk}</span>
      <span class="tl-nm">${esc(nm)}</span><span class="tl-role">${esc(s.role)}</span></button>`;
}
// Pitch markings drawn behind the chips (halfway line, circle, penalty boxes, goals).
function fieldSVG() {
  return `<svg class="tl-field" viewBox="0 0 100 130" preserveAspectRatio="none" aria-hidden="true">
    <g fill="none" stroke="#dfeee5" stroke-opacity="0.16" stroke-width="0.5">
      <line x1="0" y1="65" x2="100" y2="65"/><circle cx="50" cy="65" r="11"/>
      <rect x="26" y="0" width="48" height="17"/><rect x="38" y="0" width="24" height="6.5"/>
      <path d="M39.5 17 A 10 10 0 0 0 60.5 17"/>
      <rect x="26" y="113" width="48" height="17"/><rect x="38" y="123.5" width="24" height="6.5"/>
      <path d="M39.5 113 A 10 10 0 0 1 60.5 113"/></g>
    <g fill="#dfeee5" fill-opacity="0.18" stroke="none">
      <circle cx="50" cy="65" r="0.8"/><circle cx="50" cy="11.5" r="0.8"/><circle cx="50" cy="118.5" r="0.8"/></g>
    <g stroke="#dfeee5" stroke-opacity="0.4" stroke-width="1.4">
      <line x1="43" y1="0.5" x2="57" y2="0.5"/><line x1="43" y1="129.5" x2="57" y2="129.5"/></g>
  </svg>`;
}
function sideToggle() {
  if (!hasB()) return '';
  const btn = (k) => `<button class="tl-sidebtn ${S.active === k ? 'on' : ''}" data-side="${k}">
      <i>${k}</i>${esc(S.sides[k].team)}</button>`;
  return `<div class="tl-sidetog">${btn('A')}${btn('B')}<span class="tl-editing">editing ${esc(cur().team)}</span></div>`;
}
function render() {
  const a = cur();
  const chips = a.xi.map(chipHTML).join('');
  const groups = TACTICS_META.map(([g, items]) => `<div class="tl-tgroup"><h4>${g}</h4>${items.map(([k, lbl, lo, hi]) =>
    `<div class="tl-slider"><div class="tl-slabel"><span>${lbl}</span><b id="tv-${k}">${a.tactics[k] ?? 50}</b></div>
      <input type="range" min="0" max="100" value="${a.tactics[k] ?? 50}" data-tac="${k}">
      <div class="tl-sends"><span>${lo}</span><span>${hi}</span></div></div>`).join('')}</div>`).join('');
  document.getElementById('tlBody').innerHTML = `
    ${sideToggle()}
    <div class="tl-grid">
      <section class="card tl-pitchwrap">
        <div class="tl-pitchhead"><b>${esc(a.team)}</b><span class="muted">${esc(a.formation)} · drag to reposition · drop on a player to swap · tap to change role</span>
          <button class="tl-addbtn" id="tlAddBtn" title="Add any player to this squad (what-if transfer)">＋ Add player</button></div>
        <div class="tl-pitch">${fieldSVG()}${chips}</div></section>
      <section class="card tl-tactics"><div class="card-h"><h3>Tactical Instructions</h3></div>${groups}</section>
    </div>
    <div id="tlResults" class="tl-results"></div>`;
  document.querySelectorAll('input[data-tac]').forEach((el) => { el.oninput = () => { cur().tactics[el.dataset.tac] = +el.value; document.getElementById('tv-' + el.dataset.tac).textContent = el.value; debouncedSim(); }; });
  document.querySelectorAll('.tl-chip').forEach((el) => { el.addEventListener('pointerdown', (e) => chipDown(e, el)); });
  document.querySelectorAll('.tl-sidebtn').forEach((el) => { el.onclick = () => { S.active = el.dataset.side; render(); runSim(); }; });
  const addBtn = document.getElementById('tlAddBtn'); if (addBtn) addBtn.onclick = openAddPlayer;
}

// ---- add any player to the squad (what-if transfer, e.g. "put Rodri on Real Madrid") ----
let _addT;
function openAddPlayer() {
  closePop();
  const pop = document.createElement('div'); pop.className = 'tl-pop'; pop.id = 'tlPop';
  pop.innerHTML = `<div class="tl-pop-bd tl-addbd">
      <div class="tl-pop-h"><b>Add a player to ${esc(cur().team)}</b><button class="tl-pop-x">✕</button></div>
      <div class="tl-addhint">Drop any player into this squad — their real ratings drive the sim. They'll slot into their position; swap or drag them anywhere after.</div>
      <input class="tl-addsearch" id="addSearch" type="text" placeholder="Search a player (e.g. Rodri)…" autocomplete="off">
      <div class="tl-addresults" id="addResults"></div></div>`;
  document.body.appendChild(pop);
  pop.querySelector('.tl-pop-x').onclick = closePop;
  pop.onclick = (e) => { if (e.target === pop) closePop(); };
  const inp = document.getElementById('addSearch'); inp.focus();
  inp.oninput = () => { clearTimeout(_addT); _addT = setTimeout(() => runAddSearch(inp.value), 250); };
  document.addEventListener('keydown', function esc(ev) { if (ev.key === 'Escape') { closePop(); document.removeEventListener('keydown', esc); } });
}
async function runAddSearch(qv) {
  const box = document.getElementById('addResults'); if (!box) return;
  if (!qv || qv.trim().length < 2) { box.innerHTML = ''; return; }
  let r; try { r = await api('/api/tactics/find?q=' + encodeURIComponent(qv.trim())); } catch { r = null; }
  if (!document.getElementById('addResults')) return;              // popover closed meanwhile
  const rows = (r && r.results) || [];
  if (!rows.length) { box.innerHTML = '<div class="tl-addempty">No players found.</div>'; return; }
  const inSquad = new Set(cur().squad.map((p) => p.player));
  box.innerHTML = rows.map((p) => `<button class="tl-addrow" data-name="${esc(p.player)}"${inSquad.has(p.player) ? ' disabled' : ''}>
      <span class="tl-addph">${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy">` : ''}</span>
      <span class="tl-addnm"><b>${esc(p.player)}</b><span>${esc(p.position || '')}${p.team ? ' · ' + esc(p.team) : ''}</span></span>
      <span class="tl-addrt">${p.rating}</span>${inSquad.has(p.player) ? '<span class="tl-addin">in squad</span>' : '<span class="tl-addplus">＋</span>'}</button>`).join('');
  box.querySelectorAll('.tl-addrow').forEach((el) => { if (!el.disabled) el.onclick = () => addPlayerToSquad(el.dataset.name); });
}
// Compatible families to fall back to when no exact-position slot is free.
const _FAM_COMPAT = { DM: ['CM'], CM: ['DM', 'AM'], AM: ['CM', 'W'], W: ['AM'], ST: ['W'], FB: [], CB: [], GK: [] };
async function addPlayerToSquad(name) {
  let r; try { r = await api('/api/tactics/player?name=' + encodeURIComponent(name)); } catch { r = null; }
  if (!r || !r.available || !r.player) { toast('Could not add that player.'); return; }
  const p = r.player, a = cur();
  if (!a.squad.some((x) => x.player === p.player)) a.squad.push(p);
  // auto-slot into a matching-family position: prefer an empty slot, else the weakest starter
  const fams = [p.family, ...(_FAM_COMPAT[p.family] || [])].filter(Boolean);
  let target = null;
  for (const fam of fams) {
    const slots = a.xi.filter((s) => s.family === fam);
    target = slots.find((s) => !s.player)
      || slots.reduce((lo, s) => (!lo || (s.player ? s.player.rating : 0) < (lo.player ? lo.player.rating : 0)) ? s : lo, null);
    if (target) break;
  }
  closePop();
  if (target) {
    const replaced = target.player ? target.player.player : null;
    target.player = p; render(); runSim();
    toast(`${p.player} added${replaced ? ` — replaced ${replaced} (still in squad)` : ''}. Tap any player to swap or change role.`);
  } else {
    render();
    toast(`${p.player} added to the squad — tap a player chip to swap him into the XI.`);
  }
}
let _toastT;
function toast(msg) {
  let el = document.getElementById('tlToast');
  if (!el) { el = document.createElement('div'); el.id = 'tlToast'; el.className = 'tl-toast'; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add('show');
  clearTimeout(_toastT); _toastT = setTimeout(() => el.classList.remove('show'), 4200);
}

// ---- slot editor ----
function openSlotEditor(slotId) {
  closePop();
  const a = cur(), s = a.xi.find((x) => x.id === slotId); if (!s) return;
  const roleOpts = Object.keys(S.roles[s.family] || {}).map((rn) => `<option${rn === s.role ? ' selected' : ''}>${esc(rn)}</option>`).join('');
  const used = new Set(a.xi.map((x) => x.player && x.player.player));
  const swap = [...a.squad].sort((x, y) => y.rating - x.rating).map((p) =>
    `<option value="${esc(p.player)}"${s.player && p.player === s.player.player ? ' selected' : ''}>${esc(p.player)} · ${esc(p.position || '')} · ${p.rating}${used.has(p.player) && (!s.player || p.player !== s.player.player) ? ' (in XI)' : ''}</option>`).join('');
  const roleNote = (S.roles[s.family] || {})[s.role]?.note || '';
  const pop = document.createElement('div'); pop.className = 'tl-pop'; pop.id = 'tlPop';
  pop.innerHTML = `<div class="tl-pop-bd">
      <div class="tl-pop-h"><b>${esc(s.player ? s.player.player : s.id)}</b><button class="tl-pop-x">✕</button></div>
      <div class="tl-bio" id="popBio">${s.player ? esc((s.player.position || '') + ' · rating ' + s.player.rating) : ''}</div>
      <label class="tl-pf"><span>Role</span><select id="popRole">${roleOpts}</select></label>
      <div class="tl-rnote" id="popNote">${esc(roleNote)}</div>
      <label class="tl-pf"><span>Player</span><select id="popSwap">${swap}</select></label></div>`;
  document.body.appendChild(pop);
  document.getElementById('popRole').onchange = (e) => { s.role = e.target.value; document.getElementById('popNote').textContent = (S.roles[s.family] || {})[s.role]?.note || ''; const el = document.querySelector(`.tl-chip[data-slot="${s.id}"] .tl-role`); if (el) el.textContent = s.role; runSim(); };
  document.getElementById('popSwap').onchange = (e) => { const np = a.squad.find((p) => p.player === e.target.value); const oth = a.xi.find((x) => x !== s && x.player && x.player.player === e.target.value); if (oth) oth.player = s.player; s.player = np; render(); runSim(); closePop(); };
  pop.querySelector('.tl-pop-x').onclick = closePop;
  pop.onclick = (e) => { if (e.target === pop) closePop(); };   // close on backdrop only
  document.addEventListener('keydown', function esc(ev) { if (ev.key === 'Escape') { closePop(); document.removeEventListener('keydown', esc); } });
  const pid = s.player && /playerimages\/(\d+)\./.exec(s.player.photo || '');
  if (pid) api('/api/player_bio?pid=' + pid[1]).then((b) => { const el = document.getElementById('popBio'); if (el && b && b.available) { const bits = [s.player.position, 'rating ' + s.player.rating]; if (b.foot) bits.push(b.foot + ' foot'); if (b.height) bits.push(b.height); el.textContent = bits.filter(Boolean).join(' · '); } }).catch(() => {});
}
function closePop() { const p = document.getElementById('tlPop'); if (p) p.remove(); }

// ---- drag-to-swap (pointer events: mouse + touch) ----
let _drag = null;
function chipDown(e, el) {
  if (e.button != null && e.button !== 0) return;
  _drag = { slot: el.dataset.slot, el, x0: e.clientX, y0: e.clientY, moved: false, ghost: null };
}
function targetChip(x, y) {
  const el = document.elementFromPoint(x, y);
  return el && el.closest ? el.closest('.tl-chip') : null;
}
function onDragMove(e) {
  if (!_drag) return;
  if (!_drag.moved && Math.hypot(e.clientX - _drag.x0, e.clientY - _drag.y0) < 6) return;
  if (!_drag.moved) {
    _drag.moved = true;
    _drag.el.classList.add('tl-dragging');
    const g = document.createElement('div'); g.className = 'tl-ghost';
    g.appendChild(_drag.el.querySelector('.tl-ph').cloneNode(true));
    document.body.appendChild(g); _drag.ghost = g;
  }
  _drag.ghost.style.left = e.clientX + 'px'; _drag.ghost.style.top = e.clientY + 'px';
  document.querySelectorAll('.tl-chip.tl-drop').forEach((c) => c.classList.remove('tl-drop'));
  const t = targetChip(e.clientX, e.clientY);
  if (t && t !== _drag.el) t.classList.add('tl-drop');
  e.preventDefault();
}
// Derive a player's role family from where they're dropped, so moving a player actually
// reshapes the side (a CB dragged into midfield becomes a midfielder, etc.). x/y are 0-100
// with y measured from the goal-line up (same as the chip's left%/bottom% anchor).
function familyFromPos(x, y) {
  if (y < 13) return { family: 'GK', line: 'GK' };
  const wide = x <= 21 || x >= 79;
  if (y < 36) return { family: wide ? 'FB' : 'CB', line: 'DEF' };
  if (y < 64) {
    if (wide) return y < 50 ? { family: 'FB', line: 'MID' } : { family: 'W', line: 'ATT' };
    if (y < 46) return { family: 'DM', line: 'MID' };
    if (y < 56) return { family: 'CM', line: 'MID' };
    return { family: 'AM', line: 'MID' };
  }
  return { family: wide ? 'W' : 'ST', line: 'ATT' };
}
// Once a player is freely moved, the side is no longer a stock formation.
function markCustom() {
  cur().formation = 'Custom';
  const fs = document.getElementById('formSel');
  if (fs && S.active === 'A') {                              // formSel tracks side A
    if (!Array.from(fs.options).some((o) => o.value === 'Custom')) fs.add(new Option('Custom', 'Custom'));
    fs.value = 'Custom';
  }
}
function onDragUp(e) {
  if (!_drag) return;
  const d = _drag; _drag = null;
  if (d.ghost) d.ghost.remove();
  d.el.classList.remove('tl-dragging');
  document.querySelectorAll('.tl-chip.tl-drop').forEach((c) => c.classList.remove('tl-drop'));
  if (!d.moved) { openSlotEditor(d.slot); return; }        // a tap → open the editor
  const xi = cur().xi, a = xi.find((s) => s.id === d.slot);
  const t = targetChip(e.clientX, e.clientY);
  if (t && t.dataset.slot && t.dataset.slot !== d.slot) {   // drop on another chip → swap players
    const b = xi.find((s) => s.id === t.dataset.slot);
    if (a && b) { const tmp = a.player; a.player = b.player; b.player = tmp; render(); runSim(); }
    return;
  }
  // drop on empty pitch → reposition the player and re-derive their role family (custom formation)
  const pitch = document.querySelector('.tl-pitch');
  if (!a || !pitch) return;
  const rect = pitch.getBoundingClientRect();
  if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) return;
  const nx = Math.max(4, Math.min(96, (e.clientX - rect.left) / rect.width * 100));
  const ny = Math.max(3, Math.min(96, (rect.bottom - e.clientY) / rect.height * 100));
  a.x = Math.round(nx); a.y = Math.round(ny);
  // keep exactly one keeper: a GK stays a GK wherever he roams; nobody else becomes a GK
  let fam = a.family === 'GK' ? { family: 'GK', line: 'GK' } : familyFromPos(nx, ny);
  if (fam.family === 'GK' && a.family !== 'GK') fam = { family: 'CB', line: 'DEF' };
  if (fam.family !== a.family) {
    a.family = fam.family; a.line = fam.line;
    a.role = (S.roleDefaults && S.roleDefaults[fam.family]) || Object.keys(S.roles[fam.family] || {})[0] || '';
  }
  markCustom(); render(); runSim();
}
document.addEventListener('pointermove', onDragMove, { passive: false });
document.addEventListener('pointerup', onDragUp);
document.addEventListener('pointercancel', () => { if (_drag) { if (_drag.ghost) _drag.ghost.remove(); _drag.el.classList.remove('tl-dragging'); document.querySelectorAll('.tl-chip.tl-drop').forEach((c) => c.classList.remove('tl-drop')); _drag = null; } });

// ---- results ----
const UNIT_META = [['attack', 'Attack'], ['midfield', 'Midfield'], ['defense', 'Defense'],
  ['press_resist', 'Press resistance'], ['att_pace', 'Attack pace*'], ['def_pace', 'Defensive pace*'], ['aerial', 'Aerial']];
const METRIC_META = [['xg', 'xG', 2, 1], ['xga', 'xGA', 2, -1], ['possession', 'Possession %', 0, 1],
  ['ppda', 'PPDA', 1, -1], ['progression', 'Progression', 0, 1], ['territory', 'Territory %', 0, 1]];
const barColor = (v) => v >= 78 ? '#1f9d55' : v >= 60 ? '#5570f0' : v >= 45 ? '#e0a12b' : '#e0325b';

// Recent-form panel inside the matchup card — shows each side's last league + UCL results
// (and how deep they went in Europe), which nudge the win probabilities.
const _RND = { Final: 'Final', Semifinals: 'Semis', Quarterfinals: 'QF', 'Round of 16': 'R16', 'Playoff round': 'Playoff', 'League phase': 'League phase', 'Group stage': 'Group' };
function formCard(r, oppName) {
  const f = r.form; if (!f) return '';
  const chips = (rec) => [...(rec || '')].map((c) => `<i class="tl-fchip ${c}">${c}</i>`).join('');
  const line = (fx, nm) => {
    const short = esc(nm.split(' ')[0]);
    if (!fx || (!fx.league && !fx.ucl)) return `<div class="tl-frow"><span class="tl-fnm">${short}</span><span class="tl-fdim">no recent match data</span></div>`;
    const val = (fx.form >= 0 ? '+' : '') + Number(fx.form).toFixed(2);
    const lg = fx.league ? `<span class="tl-fseg">Lg ${chips(fx.league.record)}</span>` : '';
    const uc = fx.ucl ? `<span class="tl-fseg">UCL ${chips(fx.ucl.record)}${fx.ucl.best_round ? ' <em>' + esc(_RND[fx.ucl.best_round] || fx.ucl.best_round) + '</em>' : ''}</span>` : '';
    return `<div class="tl-frow"><span class="tl-fnm">${short}</span><b class="tl-fval ${fx.form >= 0 ? 'good' : 'bad'}">${val}</b>${lg}${uc}</div>`;
  };
  const adj = r.form_adj;
  const tilt = adj && Math.abs(adj.diff) > 0.05
    ? `<div class="tl-fnote">↳ recent form tilts the odds toward <b>${esc((adj.diff > 0 ? cur().team : oppName).split(' ')[0])}</b></div>` : '';
  return `<div class="tl-form"><div class="tl-fhdr">Recent form <span class="muted">last 6 · league + UCL</span></div>${line(f.home, cur().team)}${line(f.away, oppName)}${tilt}</div>`;
}

// Playstyle chemistry: cohesion score + the named role synergies/clashes behind it.
function chemCard(c) {
  if (!c) return '';
  const links = (c.links || []).map((l) => {
    const icon = l.kind === 'synergy' ? '🔗' : '⚡';
    const players = (l.players || []).length
      ? `<div class="tl-chplayers">${l.players.map((p) => esc(p)).join(' · ')}</div>` : '';
    return `<div class="tl-chlink ${l.kind} sev-${esc(l.sev)}"><div class="tl-cht">${icon} ${esc(l.title)}</div>
        <div class="tl-chr">${esc(l.detail)}</div>${players}</div>`;
  }).join('');
  const body = links || '<div class="tl-noweak">Neutral, balanced set of roles — no notable playstyle interactions either way.</div>';
  const prev = S.lastChem[S.active];
  let delta = '';
  if (prev != null && prev !== c.score) {
    const up = c.score > prev;
    delta = `<span class="tl-delta ${up ? 'good' : 'bad'}">${up ? '▲' : '▼'} ${Math.abs(c.score - prev)}</span>`;
  }
  return `<section class="card tl-card tl-chem"><div class="card-h"><h3>Playstyle Chemistry</h3><span class="muted">how the roles fit together</span></div>
    <div class="tl-chscore"><span class="tl-chnum" style="color:${barColor(c.score)}">${c.score}</span>
      <div class="tl-chmeta"><b>${esc(c.label)}</b>${delta}
        <span class="tl-utrack"><i style="width:${c.score}%;background:${barColor(c.score)}"></i></span></div></div>
    <div class="tl-chlinks">${body}</div>
    <div class="tl-foot">Roles are playstyles — some reinforce each other, some fight for the same space. Chemistry nudges finishing (xG) up to ±10%; change a player's role to move it.</div></section>`;
}

// ---- substitution planner: design your changes, see how they shift the side ----
const surname = (n) => (n || '').split(' ').slice(-1)[0];
function xiPlayers(side) { return side.xi.filter((s) => s.player); }
function benchOf(side) {                                   // squad players not in the XI, best first
  const inXI = new Set(side.xi.map((s) => s.player && s.player.player).filter(Boolean));
  return (side.squad || []).filter((p) => !inXI.has(p.player)).sort((a, b) => b.rating - a.rating);
}
function firstFreeOut(side) {
  const used = new Set(side.subs.map((s) => s.out));
  const c = xiPlayers(side).filter((x) => !used.has(x.id) && x.family !== 'GK')
    .sort((a, b) => a.player.rating - b.player.rating);            // weakest outfielder = natural sub
  return c.length ? c[0].id : '';
}
function firstFreeIn(side) {
  const used = new Set(side.subs.map((s) => s.in));
  const p = benchOf(side).find((x) => !used.has(x.player));        // best available sub
  return p ? p.player : '';
}
function subRow(sub, i) {
  const side = cur();
  const otherOut = new Set(side.subs.filter((_, j) => j !== i).map((s) => s.out));
  const otherIn = new Set(side.subs.filter((_, j) => j !== i).map((s) => s.in));
  const outOpts = xiPlayers(side).filter((s) => !otherOut.has(s.id)).map((s) =>
    `<option value="${esc(s.id)}"${s.id === sub.out ? ' selected' : ''}>${esc(surname(s.player.player))} · ${esc(s.id)}</option>`).join('');
  const inOpts = benchOf(side).filter((p) => !otherIn.has(p.player)).map((p) =>
    `<option value="${esc(p.player)}"${p.player === sub.in ? ' selected' : ''}>${esc(p.player)} · ${esc(p.position || '')} ${p.rating}</option>`).join('');
  return `<div class="tl-subrow" data-i="${i}">
      <span class="tl-submin"><input type="number" min="1" max="120" value="${sub.minute || 65}" data-subfield="minute" aria-label="minute">'</span>
      <select class="tl-subsel out" data-subfield="out" aria-label="player off">${outOpts}</select>
      <span class="tl-subarrow">→</span>
      <select class="tl-subsel in" data-subfield="in" aria-label="player on">${inOpts}</select>
      <button class="tl-subx" title="Remove substitution">✕</button></div>`;
}
function subsInner() {
  const side = cur();
  side.subs = side.subs || [];
  const bench = benchOf(side);
  const rows = side.subs.map((s, i) => subRow(s, i)).join('');
  const list = side.subs.length
    ? `<div class="tl-sublist">${rows}</div>`
    : `<div class="tl-subempty">No substitutions planned. Add one to see how it reshapes your side.</div>`;
  let foot = '';
  if (side.subs.length >= 5) foot = '<div class="tl-subnote">Max 5 substitutions.</div>';
  else if (!bench.length) foot = '<div class="tl-subnote">No bench players — add players to the squad first.</div>';
  else foot = '<button class="tl-subadd" id="subAdd">＋ Add substitution</button>';
  return `${list}${foot}<div id="subImpact" class="tl-subimpact"></div>`;
}
function subsCard() {
  return `<section class="card tl-card tl-subs" id="subsCard">
    <div class="card-h"><h3>Substitutions</h3><span class="muted">plan changes &amp; see the projected impact</span></div>
    <div id="subsInner">${subsInner()}</div></section>`;
}
function rerenderSubs() {
  const inner = document.getElementById('subsInner');
  if (inner) { inner.innerHTML = subsInner(); wireSubs(); }
}
function wireSubs() {
  const add = document.getElementById('subAdd');
  if (add) add.onclick = () => {
    const side = cur();
    const out = firstFreeOut(side), inP = firstFreeIn(side);
    if (!out || !inP) return;
    side.subs.push({ out, in: inP, minute: 60 + side.subs.length * 5 });
    rerenderSubs();
  };
  document.querySelectorAll('#subsCard .tl-subrow').forEach((row) => {
    const i = +row.dataset.i;
    row.querySelectorAll('[data-subfield]').forEach((el) => {
      el.onchange = () => {
        cur().subs[i][el.dataset.subfield] = el.dataset.subfield === 'minute' ? +el.value : el.value;
        if (el.dataset.subfield !== 'minute') rerenderSubs();   // re-filter dropdowns + re-project
      };
    });
    row.querySelector('.tl-subx').onclick = () => { cur().subs.splice(i, 1); rerenderSubs(); };
  });
  updateSubImpact();
}
// Compute the post-substitution XI and project it against the same opponent/tactics, then
// show how each key metric shifts vs the starting XI. Reuses the normal sim endpoint.
async function updateSubImpact() {
  const box = document.getElementById('subImpact'); if (!box) return;
  const side = cur();
  const subs = (side.subs || []).filter((s) => s.out && s.in);
  if (!subs.length || !S.sim || !S.sim.metrics) { box.innerHTML = ''; return; }
  const xi = side.xi.map((s) => ({ ...s }));                 // clone slots; swap in the subs
  subs.forEach((sub) => {
    const slot = xi.find((s) => s.id === sub.out);
    const p = (side.squad || []).find((x) => x.player === sub.in);
    if (slot && p) slot.player = p;
  });
  const payload = { team: side.team, xi, tactics: side.tactics };
  const b = other();
  if (b.team && b.xi.length) payload.opponent = { team: b.team, xi: b.xi, tactics: b.tactics };
  box.innerHTML = '<div class="tl-loading sm">Projecting impact…</div>';
  let after; try { after = await fetch('/api/tactics/sim', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then((x) => x.json()); } catch { after = null; }
  if (!after || !after.metrics || !document.getElementById('subImpact')) { if (box) box.innerHTML = ''; return; }
  box.innerHTML = subImpactHTML(S.sim, after, subs);
}
function subImpactHTML(before, after, subs) {
  const specs = [
    ['xG', after.metrics.xg, before.metrics.xg, 2, 1],
    ['xGA', after.metrics.xga, before.metrics.xga, 2, -1],
    ['Possession %', after.metrics.possession, before.metrics.possession, 0, 1],
    ['Chemistry', after.chemistry && after.chemistry.score, before.chemistry && before.chemistry.score, 0, 1],
  ];
  const cells = specs.map(([lbl, a, b, dp, good]) => {
    if (a == null || b == null) return '';
    const d = a - b, flat = Math.abs(d) < (dp ? 0.01 : 0.5);
    const cls = flat ? 'flat' : ((d > 0) === (good > 0) ? 'good' : 'bad');
    const ds = flat ? '±0' : (d > 0 ? '+' : '') + (dp ? d.toFixed(dp) : Math.round(d));
    return `<div class="tl-simp"><span class="tl-simpl">${lbl}</span><b>${dp ? a.toFixed(dp) : Math.round(a)}</b><span class="tl-simpd ${cls}">${ds}</span></div>`;
  }).join('');
  const dxg = after.metrics.xg - before.metrics.xg, dxga = after.metrics.xga - before.metrics.xga;
  const dch = (after.chemistry ? after.chemistry.score : 0) - (before.chemistry ? before.chemistry.score : 0);
  const bits = [];
  if (dxg >= 0.1) bits.push('more of an attacking threat'); else if (dxg <= -0.1) bits.push('less of an attacking threat');
  if (dxga <= -0.1) bits.push('tighter at the back'); else if (dxga >= 0.1) bits.push('more open defensively');
  if (dch >= 3) bits.push('better balanced'); else if (dch <= -3) bits.push('a bit less cohesive');
  const line = bits.length ? 'These changes make your side ' + bits.join(', ') + '.' : 'A marginal, like-for-like refresh — little tactical change.';
  return `<div class="tl-subhdr">Projected impact after your ${subs.length} sub${subs.length > 1 ? 's' : ''} <span class="muted">vs the starting XI</span></div>
    <div class="tl-simps">${cells}</div><div class="tl-subline">${esc(line)}</div>`;
}

function renderResults(r) {
  if (!r || !r.units) return;
  const units = UNIT_META.map(([k, lbl]) => `<div class="tl-ubar"><span class="tl-ul">${lbl}</span>
      <span class="tl-utrack"><i style="width:${r.units[k]}%;background:${barColor(r.units[k])}"></i></span><b>${r.units[k]}</b></div>`).join('');
  const prev = S.lastMetrics[S.active];
  const metrics = METRIC_META.map(([k, lbl, dp, good]) => {
    const v = r.metrics[k], val = dp ? v.toFixed(dp) : Math.round(v);
    let delta = '';
    if (prev && prev[k] != null && Math.abs(v - prev[k]) > (dp ? 0.02 : 0.5)) {
      const dv = v - prev[k], up = dv > 0, ben = (good > 0) === up;
      delta = `<span class="tl-delta ${ben ? 'good' : 'bad'}">${up ? '▲' : '▼'} ${Math.abs(dv).toFixed(dp)}</span>`;
    }
    return `<div class="tl-metric"><span class="tl-mk">${lbl}</span><b>${val}</b>${delta}</div>`;
  }).join('');
  const weak = (r.weaknesses || []).length ? r.weaknesses.map((w) => `<div class="tl-weak sev-${w.severity}"><div class="tl-wt">⚠ ${esc(w.title)}</div><div class="tl-wr">${esc(w.reason)}</div></div>`).join('')
    : '<div class="tl-noweak">✓ No major structural weaknesses flagged for this setup.</div>';
  const style = (r.style || []).map((s) => `<div class="tl-styl"><span>${esc(s.name)}</span><span class="tl-utrack sm"><i style="width:${s.pct}%"></i></span><b>${s.pct}%</b></div>`).join('');

  let matchup = '';
  if (r.win_probs) {
    const w = r.win_probs, oppName = other().team;
    const battles = (r.battles || []).map((b) => `<div class="tl-battle"><span>${esc(b.label)}</span><span class="tl-bbar"><i class="you" style="width:${b.a}%"></i><i class="opp" style="width:${100 - b.a}%"></i></span><b>${b.a}%</b></div>`).join('');
    matchup = `<section class="card tl-card"><div class="card-h"><h3>Matchup: ${esc(cur().team)} vs ${esc(oppName)}</h3><span class="muted">10k-sim</span></div>
        <div class="tl-wp"><div class="tl-wpseg you" style="width:${w.home}%">${esc(cur().team.split(' ')[0])} ${w.home}%</div>
          <div class="tl-wpseg draw" style="width:${w.draw}%">${w.draw >= 10 ? 'Draw ' + w.draw + '%' : ''}</div>
          <div class="tl-wpseg opp" style="width:${w.away}%">${esc(oppName.split(' ')[0])} ${w.away}%</div></div>
        <div class="tl-xgc"><span>${esc(cur().team.split(' ')[0])} xG <b>${r.metrics.xg.toFixed(2)}</b></span><span>${esc(oppName.split(' ')[0])} xG <b>${(r.opponent_metrics ? r.opponent_metrics.xg : 0).toFixed(2)}</b></span></div>
        ${formCard(r, oppName)}
        <div class="tl-battles"><div class="tl-blbl"><span>${esc(cur().team.split(' ')[0])}</span><span>${esc(oppName.split(' ')[0])}</span></div>${battles}</div></section>`;
  }
  document.getElementById('tlResults').innerHTML = `
    ${projCard(r.projection)}
    ${matchup}
    ${vizCard(r.viz)}
    <div class="tl-rgrid">
      <section class="card tl-card"><div class="card-h"><h3>Projected Metrics</h3>${prev ? '<span class="muted">Δ vs last run</span>' : ''}</div><div class="tl-metrics">${metrics}</div></section>
      <section class="card tl-card"><div class="card-h"><h3>Unit Strengths</h3></div>${units}<div class="tl-foot">Ratings &amp; attributes are EA FC / FIFA 26 player cards (stable, ability-based) — pace, shooting, passing &amp; defending are real card values, not season stats.</div></section>
    </div>
    ${chemCard(r.chemistry)}
    ${subsCard()}
    <div class="tl-rgrid">
      <section class="card tl-card"><div class="card-h"><h3>Tactical Weaknesses</h3></div>${weak}</section>
      <section class="card tl-card"><div class="card-h"><h3>Style Match</h3><span class="muted">closest famous sides</span></div>${style}
        <div class="tl-adv"><div class="tl-advhdr">🧠 AI analyst read</div>
          <div id="advOut">${_lastAdvText || '<div class="tl-loading sm">Reading your setup…</div>'}</div></div></section>
    </div>`;
  S.lastMetrics[S.active] = { ...r.metrics };
  if (r.chemistry) S.lastChem[S.active] = r.chemistry.score;
  wireSubs();
  const ssb = document.getElementById('simSeasonBtn'); if (ssb) ssb.onclick = openSeasonModal;
  scheduleAdvisor();
}

// ---- season & cup projection ----
function ordinal(n) { const s = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
function projCard(p) {
  if (!p) return '';
  const stages = (p.run || []).map((s) => `<div class="tl-pstage"><span class="tl-psl">${esc(s.stage)}</span>
      <span class="tl-utrack sm"><i style="width:${s.reach}%;background:${barColor(s.reach)}"></i></span><b>${s.reach}%</b></div>`).join('');
  const league = p.kind === 'club'
    ? `<div class="tl-projrow"><span class="tl-pbadge">🏆 ${esc(p.league)}</span><b>${ordinal(p.position)}</b>
        <span class="muted">projected · ${p.points} pts</span></div>` : '';
  const cup = `<div class="tl-projrow"><span class="tl-pbadge">${p.kind === 'club' ? '⭐ ' : '🌍 '}${esc(p.comp)}</span>
      <b>${esc(p.likely)}</b><span class="muted">most likely · ${p.win_pct}% to win it</span></div>`;
  return `<section class="card tl-card"><div class="card-h"><h3>Season &amp; Cup Projection</h3>
      <button class="tl-seasonbtn" id="simSeasonBtn">🔮 Simulate full season</button></div>
    ${league}${cup}
    <div class="tl-pstages">${stages}</div>
    <div class="tl-foot">Model projection from squad quality + your tactics (xG→expected points). Knockout ties carry realistic variance.</div></section>`;
}

// ---- full season simulation modal: standings table + cup run + team stat leaders ----
async function openSeasonModal() {
  const side = cur();
  closePop();
  const pop = document.createElement('div'); pop.className = 'tl-pop'; pop.id = 'tlPop';
  pop.innerHTML = `<div class="tl-pop-bd tl-seasonbd">
      <div class="tl-pop-h"><b>Season Simulation — ${esc(side.team)}</b><button class="tl-pop-x">✕</button></div>
      <div id="seasonBody"><div class="tl-loading">Simulating the season…</div></div></div>`;
  document.body.appendChild(pop);
  pop.querySelector('.tl-pop-x').onclick = closePop;
  pop.onclick = (e) => { if (e.target === pop) closePop(); };
  document.addEventListener('keydown', function esc(ev) { if (ev.key === 'Escape') { closePop(); document.removeEventListener('keydown', esc); } });
  const payload = { team: side.team, xi: side.xi, tactics: side.tactics };
  let r; try { r = await fetch('/api/tactics/season', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then((x) => x.json()); } catch { r = null; }
  const body = document.getElementById('seasonBody'); if (!body) return;
  if (!r || !r.available) { body.innerHTML = '<div class="empty-state">Could not simulate — load a team first.</div>'; return; }
  body.innerHTML = seasonHTML(r);
}
function seasonHTML(r) {
  const p = r.projection || {}, st = r.standings;
  let league = '';
  if (st && st.table && st.table.length) {
    const you = st.table.find((x) => x.is_user);
    const rows = st.table.map((t) => `<tr class="${t.is_user ? 'you' : ''}">
        <td class="tl-stpos">${t.pos}</td>
        <td class="tl-stteam">${t.logo ? `<img src="${esc(t.logo)}" alt="" loading="lazy">` : ''}<span>${esc(t.team)}</span></td>
        <td class="tl-stpts">${t.pts}</td></tr>`).join('');
    league = `<div class="tl-sechdr">🏆 ${esc(p.league || 'League')} — projected finish</div>
      <div class="tl-sefin"><span class="tl-sepos">${ordinal(you ? you.pos : p.position)}</span>
        <span class="tl-semeta"><b>${you ? you.pts : p.points} pts</b><span class="muted">over ${st.games} games</span></span></div>
      <div class="tl-sttblwrap"><table class="tl-sttbl"><thead><tr><th></th><th>Team</th><th>Pts</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  } else if (p.kind === 'club' && p.position) {
    league = `<div class="tl-sechdr">🏆 ${esc(p.league || 'League')} — projected finish</div>
      <div class="tl-sefin"><span class="tl-sepos">${ordinal(p.position)}</span><span class="tl-semeta"><b>${p.points} pts</b></span></div>`;
  }
  const run = (p.run || []).map((s) => `<div class="tl-pstage"><span class="tl-psl">${esc(s.stage)}</span>
      <span class="tl-utrack sm"><i style="width:${s.reach}%;background:${barColor(s.reach)}"></i></span><b>${s.reach}%</b></div>`).join('');
  const cup = p.comp ? `<div class="tl-sechdr">${p.kind === 'club' ? '⭐' : '🌍'} ${esc(p.comp)}</div>
      <div class="tl-serow"><b>${esc(p.likely)}</b> <span class="muted">most likely finish · ${p.win_pct}% to win it</span></div>
      <div class="tl-pstages">${run}</div>` : '';
  const leaders = (r.leaders || []).map((cat) => `<div class="tl-lcat"><div class="tl-lcath">${esc(cat.label)}</div>
      ${cat.top.map((x, i) => `<div class="tl-lrow"><span class="tl-lrank">${i + 1}</span>
          <span class="tl-lph">${x.photo ? `<img src="${esc(x.photo)}" alt="" loading="lazy">` : ''}</span>
          <span class="tl-lnm">${esc(x.player)}</span><b class="tl-lval">${x.value}</b></div>`).join('')}</div>`).join('');
  const leadersBlock = leaders ? `<div class="tl-sechdr">📊 Projected stat leaders <span class="muted">full season · top 5 per category</span></div>
      <div class="tl-lgrid">${leaders}</div>` : '';
  return `<div class="tl-season">${league}${cup}${leadersBlock}
    <div class="tl-foot">League: rivals extrapolated at their current pace, your side from the model. Player totals project each starter's real current per-90 output over ~85% of a full season. Knockout runs carry realistic variance.</div></div>`;
}

// ---- visualization: shape + passing network + territory heat ----
function vizCard(viz) {
  if (!viz || !viz.positions) return '';
  const pos = {}; viz.positions.forEach((p) => { pos[p.id] = p; });
  const PAD = 9, W = 100, H = 118;
  const px = (x) => PAD + x / 100 * (W - 2 * PAD);
  const py = (y) => PAD + (100 - y) / 100 * (H - 2 * PAD);   // invert: attack at top
  // territory heat: a soft band centered on how high the side plays
  const ty = py(20 + viz.territory * 0.6);
  const lines = viz.network.map((e) => {
    const a = pos[e.from], b = pos[e.to]; if (!a || !b) return '';
    return `<line x1="${px(a.x).toFixed(1)}" y1="${py(a.y).toFixed(1)}" x2="${px(b.x).toFixed(1)}" y2="${py(b.y).toFixed(1)}" stroke="#8ea2ff" stroke-opacity="${(0.14 + e.w * 0.85).toFixed(2)}" stroke-width="${(0.4 + e.w * 3).toFixed(2)}" stroke-linecap="round"/>`;
  }).join('');
  const dots = viz.positions.map((p) => {
    const r = (2.4 + p.involvement / 100 * 3.4).toFixed(2);
    return `<g><circle cx="${px(p.x).toFixed(1)}" cy="${py(p.y).toFixed(1)}" r="${r}" fill="#5570f0" stroke="#fff" stroke-width="0.5"/>
      <text x="${px(p.x).toFixed(1)}" y="${(py(p.y) + (+r) + 3).toFixed(1)}" text-anchor="middle" font-size="2.7" font-weight="700" fill="#e6ebf5">${esc(p.name.split(' ').slice(-1)[0])}</text></g>`;
  }).join('');
  return `<section class="card tl-card"><div class="card-h"><h3>Shape &amp; Passing Network — ${esc(cur().team)}</h3>
      <span class="muted">avg. positions · line = likely pass volume · tint = territory</span></div>
    <div class="tl-vizwrap"><svg viewBox="0 0 ${W} ${H}" class="tl-viz" preserveAspectRatio="xMidYMid meet">
      <defs><linearGradient id="heatg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="#5570f0" stop-opacity="0"/><stop offset="${((ty - 12) / H).toFixed(2)}" stop-color="#5570f0" stop-opacity="0.02"/>
        <stop offset="${(ty / H).toFixed(2)}" stop-color="#5570f0" stop-opacity="0.20"/><stop offset="${((ty + 14) / H).toFixed(2)}" stop-color="#5570f0" stop-opacity="0.02"/>
        <stop offset="1" stop-color="#5570f0" stop-opacity="0"/></linearGradient></defs>
      <rect x="0" y="0" width="${W}" height="${H}" fill="url(#heatg)"/>
      <g stroke="#ffffff" stroke-opacity="0.13" fill="none" stroke-width="0.4">
        <rect x="${PAD}" y="${PAD}" width="${W - 2 * PAD}" height="${H - 2 * PAD}" rx="2"/>
        <line x1="${PAD}" y1="${H / 2}" x2="${W - PAD}" y2="${H / 2}"/>
        <circle cx="${W / 2}" cy="${H / 2}" r="9"/>
        <rect x="${W / 2 - 18}" y="${PAD}" width="36" height="15"/><rect x="${W / 2 - 18}" y="${H - PAD - 15}" width="36" height="15"/>
        <rect x="${W / 2 - 8}" y="${PAD}" width="16" height="6"/><rect x="${W / 2 - 8}" y="${H - PAD - 6}" width="16" height="6"/></g>
      ${lines}${dots}
    </svg></div>
    <div class="tl-vizlabels"><span>↑ attacking direction</span><span>possession ${viz.possession}% · territory ${viz.territory}%</span></div></section>`;
}

// The analyst auto-updates when the setup settles (debounced so it doesn't fire on every
// slider tick), cached by setup so an unchanged state doesn't re-hit the model.
let _advT, _lastAdvKey = '', _lastAdvText = '';
function scheduleAdvisor() { clearTimeout(_advT); _advT = setTimeout(loadAdvisor, 1400); }
async function loadAdvisor() {
  const r = S.sim; if (!r || !r.units) return;
  const key = JSON.stringify([cur().team, r.metrics, r.units, cur().tactics, r.chemistry && r.chemistry.score, hasB() ? other().team : null]);
  if (key === _lastAdvKey && _lastAdvText) return;         // setup unchanged → keep current read
  const out = document.getElementById('advOut'); if (out && !_lastAdvText) out.innerHTML = '<div class="tl-loading sm">Reading your setup…</div>';
  const payload = { team: cur().team, metrics: r.metrics, units: r.units, tactics: cur().tactics, weaknesses: r.weaknesses, chemistry: r.chemistry, opponent_name: hasB() ? other().team : null };
  let a; try { a = await fetch('/api/tactics/advisor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then((x) => x.json()); } catch { a = null; }
  const el = document.getElementById('advOut'); if (!el) return;
  if (!a || !a.available) { if (!_lastAdvText) el.innerHTML = '<div class="tl-advtext muted">AI analyst is unavailable right now.</div>'; return; }
  _lastAdvKey = key; _lastAdvText = `<div class="tl-advtext"><p>${esc(a.text).replace(/\n+/g, '</p><p>')}</p></div>`;
  el.innerHTML = _lastAdvText;
}

// ---- init ----
fillTeams();
const teamSel = document.getElementById('teamInput'), oppSel = document.getElementById('oppInput');
// (Re)load ONLY the opponent side, KEEPING the team you've been building (side A) — its
// custom positions, roles, swaps, added players and tactics. Shared by the opponent
// dropdown AND the Load button, so neither wipes your work when you set an opponent.
async function applyOpponent() {
  if (!S.sides.A.xi.length) { S.active = 'A'; return loadAll(); }   // A not built yet → full load
  if (S.sides.B.team) {
    if (S.sides.B.formation === 'Custom') S.sides.B.formation = '4-3-3';  // fresh opponent → stock shape
    await loadSide('B');
    if (S.sides.B.error) { S.sides.B.team = ''; S.sides.B.xi = []; S.sides.B.squad = []; ensureOption(oppSel, ''); }
  } else {
    S.sides.B.xi = []; S.sides.B.squad = [];
  }
  if (!hasB() && S.active === 'B') S.active = 'A';   // opponent cleared while viewing it
  S.lastMetrics.B = null;
  render(); runSim();
}
document.getElementById('loadBtn').onclick = () => {
  const newA = teamSel.value || 'Real Madrid';
  const aChanged = newA !== S.sides.A.team || !S.sides.A.xi.length;
  S.sides.A.team = newA;
  S.sides.B.team = oppSel.value;
  if (aChanged) {                                    // switching team A → full (re)load
    S.sides.A.formation = document.getElementById('formSel').value || '4-3-3';
    S.active = 'A'; loadAll();
  } else {                                           // same team A → keep its build, just load the opponent
    applyOpponent();
  }
};
// team select reloads side A (you're explicitly switching it); opponent select keeps A.
teamSel.onchange = () => { S.sides.A.team = teamSel.value; S.active = 'A'; loadAll(); };
oppSel.onchange = () => { S.sides.B.team = oppSel.value; applyOpponent(); };
document.getElementById('formSel').onchange = () => {
  const v = document.getElementById('formSel').value;
  if (v === 'Custom') return;                 // custom layout already applied; picking a preset re-loads it
  cur().formation = v; loadSide(S.active).then(() => { render(); runSim(); });
};
// deep-link a matchup: /tactics.html?a=Real Madrid&b=Manchester City
const _qp = new URLSearchParams(location.search);
if (_qp.get('a')) S.sides.A.team = _qp.get('a');
if (_qp.get('b')) S.sides.B.team = _qp.get('b');
ensureOption(teamSel, S.sides.A.team);
ensureOption(oppSel, S.sides.B.team);
loadAll();
