// Tactics Lab — two fully-editable teams (A/B), tactic sliders, explainable projection,
// opponent matchup, AI advisor, and a post-sim shape + passing-network visualization.
renderSidebar('Tactics');

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const S = {
  active: 'A',
  sides: { A: blankSide('Real Madrid'), B: blankSide('') },
  roles: {}, roleDefaults: {}, formations: [],
  lastMetrics: { A: null, B: null }, sim: null,
};
function blankSide(team) { return { team, formation: '4-3-3', xi: [], squad: [], tactics: {} }; }
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
const CLUBS = ['Real Madrid', 'Barcelona', 'Manchester City', 'Arsenal', 'Liverpool', 'Bayern Munich',
  'PSG', 'Inter', 'Atlético Madrid', 'Bayer Leverkusen', 'Manchester United', 'Chelsea', 'Tottenham',
  'Newcastle United', 'Napoli', 'AC Milan', 'Juventus', 'Borussia Dortmund', 'Aston Villa', 'Bournemouth'];
const NATIONS = ['Argentina', 'France', 'Brazil', 'England', 'Spain', 'Germany', 'Portugal', 'Netherlands',
  'Belgium', 'Croatia', 'Morocco', 'Uruguay', 'Colombia', 'Mexico', 'Japan', 'Korea Republic', 'USA'];
function fillTeams() {
  document.getElementById('teamList').innerHTML =
    CLUBS.map((t) => `<option value="${esc(t)}">Club</option>`).join('') +
    NATIONS.map((t) => `<option value="${esc(t)}">National team</option>`).join('');
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
  const ph = p && p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy">` : '';
  return `<button class="tl-chip" style="left:${s.x}%;bottom:${s.y}%" data-slot="${s.id}">
      <span class="tl-ph">${ph}<i class="tl-rt">${p ? p.rating : '-'}</i></span>
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
        <div class="tl-pitchhead"><b>${esc(a.team)}</b><span class="muted">${a.formation} · tap a player to change role or swap</span></div>
        <div class="tl-pitch">${fieldSVG()}${chips}</div></section>
      <section class="card tl-tactics"><div class="card-h"><h3>Tactical Instructions</h3></div>${groups}</section>
    </div>
    <div id="tlResults" class="tl-results"></div>`;
  document.querySelectorAll('input[data-tac]').forEach((el) => { el.oninput = () => { cur().tactics[el.dataset.tac] = +el.value; document.getElementById('tv-' + el.dataset.tac).textContent = el.value; debouncedSim(); }; });
  document.querySelectorAll('.tl-chip').forEach((el) => { el.onclick = (e) => { e.stopPropagation(); openSlotEditor(el.dataset.slot); }; });
  document.querySelectorAll('.tl-sidebtn').forEach((el) => { el.onclick = () => { S.active = el.dataset.side; render(); runSim(); }; });
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
  pop.onclick = (e) => { if (e.target === pop) closePop(); };
  const pid = s.player && /playerimages\/(\d+)\./.exec(s.player.photo || '');
  if (pid) api('/api/player_bio?pid=' + pid[1]).then((b) => { const el = document.getElementById('popBio'); if (el && b && b.available) { const bits = [s.player.position, 'rating ' + s.player.rating]; if (b.foot) bits.push(b.foot + ' foot'); if (b.height) bits.push(b.height); el.textContent = bits.filter(Boolean).join(' · '); } }).catch(() => {});
}
function closePop() { const p = document.getElementById('tlPop'); if (p) p.remove(); }

// ---- results ----
const UNIT_META = [['attack', 'Attack'], ['midfield', 'Midfield'], ['defense', 'Defense'],
  ['press_resist', 'Press resistance'], ['att_pace', 'Attack pace*'], ['def_pace', 'Defensive pace*'], ['aerial', 'Aerial']];
const METRIC_META = [['xg', 'xG', 2, 1], ['xga', 'xGA', 2, -1], ['possession', 'Possession %', 0, 1],
  ['ppda', 'PPDA', 1, -1], ['progression', 'Progression', 0, 1], ['territory', 'Territory %', 0, 1]];
const barColor = (v) => v >= 78 ? '#1f9d55' : v >= 60 ? '#5570f0' : v >= 45 ? '#e0a12b' : '#e0325b';

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
        <div class="tl-battles"><div class="tl-blbl"><span>${esc(cur().team.split(' ')[0])}</span><span>${esc(oppName.split(' ')[0])}</span></div>${battles}</div></section>`;
  }
  document.getElementById('tlResults').innerHTML = `
    ${matchup}
    ${vizCard(r.viz)}
    <div class="tl-rgrid">
      <section class="card tl-card"><div class="card-h"><h3>Projected Metrics</h3>${prev ? '<span class="muted">Δ vs last run</span>' : ''}</div><div class="tl-metrics">${metrics}</div></section>
      <section class="card tl-card"><div class="card-h"><h3>Unit Strengths</h3></div>${units}<div class="tl-foot">* pace is an estimate (no tracking data): position baseline nudged by dribble volume & clearances.</div></section>
    </div>
    <div class="tl-rgrid">
      <section class="card tl-card"><div class="card-h"><h3>Tactical Weaknesses</h3></div>${weak}</section>
      <section class="card tl-card"><div class="card-h"><h3>Style Match</h3><span class="muted">closest famous sides</span></div>${style}
        <div class="tl-adv"><button id="advBtn" class="btn btn-ghost">🧠 Ask the AI analyst</button><div id="advOut"></div></div></section>
    </div>`;
  S.lastMetrics[S.active] = { ...r.metrics };
  document.getElementById('advBtn').onclick = loadAdvisor;
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

async function loadAdvisor() {
  const out = document.getElementById('advOut'), btn = document.getElementById('advBtn');
  btn.disabled = true; out.innerHTML = '<div class="tl-loading sm">Analysing…</div>';
  const r = S.sim;
  const payload = { team: cur().team, metrics: r.metrics, units: r.units, tactics: cur().tactics, weaknesses: r.weaknesses, opponent_name: hasB() ? other().team : null };
  let a; try { a = await fetch('/api/tactics/advisor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then((x) => x.json()); } catch { a = null; }
  btn.disabled = false;
  if (!a || !a.available) { out.innerHTML = '<div class="tl-advtext muted">AI analyst is unavailable right now.</div>'; return; }
  out.innerHTML = `<div class="tl-advtext"><p>${esc(a.text).replace(/\n+/g, '</p><p>')}</p></div>`;
}

// ---- init ----
fillTeams();
document.addEventListener('click', closePop);
document.getElementById('loadBtn').onclick = () => {
  S.sides.A.team = document.getElementById('teamInput').value.trim() || 'Real Madrid';
  S.sides.A.formation = document.getElementById('formSel').value || '4-3-3';
  S.sides.B.team = document.getElementById('oppInput').value.trim();
  S.active = 'A'; loadAll();
};
document.getElementById('formSel').onchange = () => { cur().formation = document.getElementById('formSel').value; loadSide(S.active).then(() => { render(); runSim(); }); };
// deep-link a matchup: /tactics.html?a=Real Madrid&b=Manchester City
const _qp = new URLSearchParams(location.search);
if (_qp.get('a')) S.sides.A.team = _qp.get('a');
if (_qp.get('b')) S.sides.B.team = _qp.get('b');
document.getElementById('teamInput').value = S.sides.A.team;
document.getElementById('oppInput').value = S.sides.B.team;
loadAll();
