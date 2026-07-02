renderSidebar('Live Matches');
attachSearchDropdown(document.getElementById('searchBox'));

const EID = new URLSearchParams(location.search).get('id');
const TABS = [['preview', 'Preview'], ['predict', 'Predict'], ['lineups', 'Lineups'], ['moments', 'Key Moments'], ['prediction', 'Odds'], ['stats', 'Stats'],
              ['shotmap', 'Shot Map'], ['timeline', 'Timeline'], ['players', 'Players'], ['heatmaps', 'Heatmaps']];
const TAB_KEYS = TABS.map(([k]) => k);
let head = null, timer = null;
const _urlTab = new URLSearchParams(location.search).get('tab');
let active = TAB_KEYS.includes(_urlTab) ? _urlTab : null;   // resolved after header (default by status)
let playersCache = null, playerSort = { key: 'rating', dir: -1 };
const heatCache = {};                       // player_id -> {points}

// Fetch a match endpoint. The deployed server can't reach SofaScore directly --
// a remote scraper fills its cache on demand -- so if the data isn't there yet the
// server returns {available:false, pending:true}; wait for the relay then retry.
// Genuinely-empty data comes back pending:false, so this doesn't spin on those.
const RELAY_POLL_MS = 900;   // poll cadence while waiting on the relay-filled cache
async function A(p) {
  const url = p + (p.includes('?') ? '&' : '?') + 'id=' + encodeURIComponent(EID);
  let r = await api(url);
  for (let i = 0; i < 28 && r && r.available === false && r.pending; i++) {
    await new Promise(res => setTimeout(res, RELAY_POLL_MS));
    r = await api(url);
  }
  return r;
}
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
  const pens = (head.home_pens != null && head.away_pens != null)
    ? `<div class="mh-pens">${head.home_pens}-${head.away_pens} on penalties</div>` : '';
  // each team links to its page: national teams -> /nat.html, clubs -> /team.html
  const teamLink = (side) => {
    const id = head[side + '_id'], name = head[side];
    if (head[side + '_national'] && id != null) return `/nat.html?id=${id}`;
    return name ? `/team.html?name=${encodeURIComponent(name)}` : null;
  };
  const open = (side) => { const l = teamLink(side); return l ? ` onclick="location.href='${l}'" style="cursor:pointer"` : ''; };
  hero.innerHTML = `
    <div class="mh-comp">${esc(head.competition || '')}${head.round ? ' · ' + esc(head.round) : ''}</div>
    <div class="mh-row">
      <div class="mh-team home"${open('home')}>${badge('home')}<span class="mh-name">${esc(head.home)}</span>${rankBadge(head.home_rank)}</div>
      <div class="mh-score">${played ? `${head.home_score}<span class="dash">-</span>${head.away_score}` : '<span class="vs">vs</span>'}
        ${pens}<div class="mh-st">${heroStatus()}</div></div>
      <div class="mh-team away"${open('away')}>${rankBadge(head.away_rank)}<span class="mh-name">${esc(head.away)}</span>${badge('away')}</div>
    </div>
    ${head.referee ? `<div class="mh-ref">🧑‍⚖️ Referee · ${head.referee.country ? flagISO2(head.referee.country) + ' ' : ''}${esc(head.referee.name)}</div>` : ''}`;
}

// ---- tabs ----
function renderTabs() {
  const el = document.getElementById('matchTabs');
  el.innerHTML = TABS.map(([k, label]) => `<span class="tab ${k === active ? 'active' : ''}" data-k="${k}">${label}</span>`).join('');
  el.querySelectorAll('.tab').forEach(t => t.onclick = () => { active = t.dataset.k; renderTabs(); loadActive(); });
}
const body = () => document.getElementById('tabBody');
const LOADERS = { preview: loadPreview, predict: loadPredict, stats: loadStatsTab, lineups: loadLineups, prediction: loadPrediction, shotmap: loadShotmap, timeline: loadTimeline, moments: loadKeyMoments, players: loadPlayers, heatmaps: loadHeatmaps };
async function loadActive(refresh) {
  if (!refresh) body().innerHTML = '<section class="card"><div class="placeholder-note">Loading…</div></section>';
  try { await LOADERS[active](); } catch { body().innerHTML = '<section class="card"><div class="placeholder-note">Could not load this section.</div></section>'; }
}

// ---- Stats ----
async function loadStatsTab() {
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

// ---- Predict (the user's own score pick — shares the Score Predictor store) ----
const _mppVal = (id) => {
  const x = document.getElementById(id).value;
  if (x === '') return null;
  const n = parseInt(x, 10);
  return isNaN(n) ? null : Math.max(0, Math.min(30, n));
};
function _predState(p, finished, locked) {
  if (finished) {
    if (!p) return `<span class="lock">Final <b>${head.home_score}-${head.away_score}</b> — you didn't predict this one.</span>`;
    const cls = p.pts > 0 ? 'pos' : 'zero';
    return `Final <b>${head.home_score}-${head.away_score}</b> · you called ${p.h}-${p.a}
      <span class="mpp-pts ${cls}">${p.pts > 0 ? '+' + p.pts : '0'} pts</span>`;
  }
  if (locked) return '<span class="lock">Predictions are locked — this match has kicked off.</span>';
  if (p) return `<span class="saved">✓ Prediction saved: ${p.h}-${p.a}.</span> Change it any time before kickoff.`;
  return 'Enter your scoreline to lock in a prediction. <b>+5</b> for the exact score · <b>+2</b> for the right result.';
}
function loadPredict() {
  if (typeof predStore !== 'function') { body().innerHTML = empty('Predictor unavailable.'); return; }
  const eid = EID;
  const finished = head?.status === 'finished' && head.home_score != null;
  if (finished) predSettle(eid, head.home_score, head.away_score, head.competition);   // settle on view
  const now = Date.now() / 1000;
  const locked = head?.status !== 'notstarted' || (head?.start_ts && head.start_ts <= now);
  const d = predStore(), p = d.preds[eid];
  const dis = (locked || finished) ? ' disabled' : '';
  const setc = p ? ' set' : '';
  const v = (x) => x == null ? '' : x;
  body().innerHTML = `<section class="card">
    <div class="card-h"><h3>Predict the Score</h3><a class="see" href="/predict.html">Open Score Predictor →</a></div>
    <div class="mpp">
      <div class="mpp-team">${badge('home')}<span>${esc(head.home)}</span></div>
      <div class="mpp-score">
        <input id="mppH" class="mpp-in${setc}" type="number" min="0" max="30" inputmode="numeric" value="${p ? v(p.h) : ''}"${dis}>
        <span class="dash">-</span>
        <input id="mppA" class="mpp-in${setc}" type="number" min="0" max="30" inputmode="numeric" value="${p ? v(p.a) : ''}"${dis}>
      </div>
      <div class="mpp-team away"><span>${esc(head.away)}</span>${badge('away')}</div>
    </div>
    <div class="mpp-state" id="mppState">${_predState(p, finished, locked)}</div>
    <div class="mpp-foot">Your running total: <b>${d.total}</b> pts</div>
  </section>`;
  if (!locked && !finished) {
    const save = () => {
      const h = _mppVal('mppH'), a = _mppVal('mppA');
      const cleared = h == null || a == null;
      const meta = { ko: head.start_ts, comp: head.competition, home: head.home, away: head.away,
                     home_country: head.home_country, away_country: head.away_country };
      predSave(eid, meta, cleared ? null : h, cleared ? null : a);
      document.querySelectorAll('.mpp-in').forEach(x => x.classList.toggle('set', !cleared));
      document.getElementById('mppState').innerHTML = _predState(cleared ? null : { h, a }, false, false);
    };
    document.getElementById('mppH').addEventListener('change', save);
    document.getElementById('mppA').addEventListener('change', save);
  }
}

// ---- Prediction (from bookmaker odds) ----
async function loadPrediction() {
  const d = await A('/api/match/prediction');
  if (!d.available) { body().innerHTML = empty('No betting odds available for this match yet.'); return; }
  const c = d.consensus;
  const teams = { home: head?.home || 'Home', draw: 'Draw', away: head?.away || 'Away' };
  const predLabel = d.predicted === 'draw' ? 'Draw' : `${teams[d.predicted]} win`;
  const seg = (k, cls) => `<div class="pr-seg ${cls}${d.predicted === k ? ' pred' : ''}" style="width:${c[k]}%" title="${esc(teams[k])} ${c[k]}%">${c[k] >= 10 ? c[k] + '%' : ''}</div>`;
  const oddsRows = d.books.map((b, i) => `<tr><td class="tl">Bookmaker ${i + 1}</td>
    <td>${b.odds.home}</td><td>${b.odds.draw}</td><td>${b.odds.away}</td></tr>`).join('');
  const liveOdds = head?.status === 'inprogress';
  const s = d.score;
  const resLabel = s && (s.result === 'draw' ? 'Draw' : `${teams[s.result]} win`);
  const aspCard = s ? `<section class="card asp-card">
      <div class="card-h"><h3>Atlastra Prediction</h3><span class="see">${s.live ? '<span class="live">● projected final</span>' : 'most likely scoreline'}</span></div>
      <div class="asp">
        <span class="asp-tm">${esc(teams.home)}</span>
        <span class="asp-sc">${s.home}<span class="asp-dash">–</span>${s.away}</span>
        <span class="asp-tm">${esc(teams.away)}</span></div>
      <div class="asp-note">🔮 Atlastra predicts ${s.live ? 'this finishes' : 'a final'} <b>${esc(teams.home)} ${s.home}–${s.away} ${esc(teams.away)}</b> · ${esc(resLabel)} (${s.result_conf}% likely)</div>
    </section>` : '';
  body().innerHTML = aspCard + `<section class="card">
      <div class="card-h"><h3>Match Prediction</h3><span class="see">${liveOdds ? '<span class="live">● live odds</span> · ' : ''}${d.n_books} bookmaker${d.n_books > 1 ? 's' : ''}</span></div>
      <div class="pr-head">${liveOdds ? 'In-play' : 'Most likely'}: <b>${esc(predLabel)}</b> <span class="muted">· ${c[d.predicted]}% implied</span></div>
      <div class="pr-bar">${seg('home', 'h')}${seg('draw', 'd')}${seg('away', 'a')}</div>
      <div class="pr-legend">
        <span><i class="h"></i>${esc(teams.home)} <b>${c.home}%</b></span>
        <span><i class="d"></i>Draw <b>${c.draw}%</b></span>
        <span><i class="a"></i>${esc(teams.away)} <b>${c.away}%</b></span></div>
      <div class="card-h" style="margin-top:18px"><h3>Bookmaker odds <span class="muted" style="font-weight:400">(decimal)</span></h3></div>
      <div class="ltbl-wrap"><table class="ltbl">
        <thead><tr><th class="tl">Source</th><th>${esc(teams.home)}</th><th>Draw</th><th>${esc(teams.away)}</th></tr></thead>
        <tbody>${oddsRows}</tbody></table></div>
      <div class="placeholder-note" style="margin-top:10px">Win probabilities are implied from bookmaker 1X2 odds with the margin removed, averaged across sources (via SofaScore). Not betting advice.</div>
    </section>`;
}

// ---- Lineups (formation pitch) ----
const mgrTag = (name) => name ? `<span class="lp-mgr" title="Manager">👔 ${esc(name)}</span>` : '';
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
    for (let j = 0; j < k; j++) {
      // SofaScore lists each line from the team's RIGHT. The home team attacks UP,
      // so its left must land on the viewer's left -> reverse the slot. Away attacks
      // DOWN and is already oriented correctly.
      const slot = isHome ? (k - 1 - j) : j;
      out.push({ x: 0.09 + 0.82 * ((slot + 0.5) / k), y, p: xi[idx++] });
    }
  }
  return out;
}
function chipHTML({ x, y, p }, isHome) {
  const rt = p.rating != null
    ? `<i class="luc-rt" style="background:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</i>` : '';
  // our Atlastra League/UCL combined rating (or ~estimate when not in our DB)
  const arTitle = p.atlas_wc ? 'World Cup rating' : p.atlas_est ? 'Estimated Atlastra rating' : 'Atlastra rating (best of League/UCL)';
  const ar = p.atlas_rating != null
    ? `<i class="luc-ar${p.atlas_est ? ' est' : ''}" title="${arTitle}">${p.atlas_est ? '~' : ''}${p.atlas_rating}</i>` : '';
  const cap = p.captain ? '<i class="luc-cap">C</i>' : '';
  // goal / assist icons from the player's match stats (keyed by SofaScore id)
  const st = _luStats && _luStats[p.id];
  const g = st ? (st.goals || 0) : 0, a = st ? (st.assists || 0) : 0;
  const ev = [];
  if (g) ev.push(`<span class="ev g" title="${g} goal${g > 1 ? 's' : ''}">⚽${g > 1 ? '<b>' + g + '</b>' : ''}</span>`);
  if (a) ev.push(`<span class="ev a" title="${a} assist${a > 1 ? 's' : ''}">👟${a > 1 ? '<b>' + a + '</b>' : ''}</span>`);
  const evHTML = ev.length ? `<span class="luc-ev">${ev.join('')}</span>` : '';
  return `<div class="luc ${isHome ? 'h' : 'a'}" style="left:${(x * 100).toFixed(1)}%;top:${(y * 100).toFixed(1)}%"
      onclick="openPlayerModal(${p.id})" title="${esc(p.name)} — view match stats">
      <span class="luc-dot">${p.number ?? ''}${rt}${ar}${cap}</span>
      <span class="luc-nm">${esc(_surname(p.name))}${evHTML}</span></div>`;
}
// fallback list (used when a formation can't be parsed, e.g. predicted lineups)
function lineupSideList(s, label) {
  if (!s) return '';
  const row = (p) => `<div class="lu-row"><span class="lu-no">${p.number ?? ''}</span>
    <span class="lu-nm">${esc(p.name)}${p.atlas_rating != null ? `<span class="lu-ar${p.atlas_est ? ' est' : ''}" title="${p.atlas_est ? 'Estimated Atlastra rating' : 'Atlastra rating (best of League/UCL)'}">${p.atlas_est ? '~' : ''}${p.atlas_rating}</span>` : ''}</span><span class="lu-pos">${esc(p.position || '')}</span>
    ${p.rating != null ? `<span class="ratingchip sm" style="border-color:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>` : ''}</div>`;
  return `<section class="card lu-col">
    <div class="card-h"><h3>${esc(label)}</h3><span class="see">${[mgrTag(s.manager), esc(s.formation || '')].filter(Boolean).join(' · ')}</span></div>
    ${(s.starting_xi || []).map(row).join('') || '<div class="placeholder-note">No starting XI.</div>'}
    ${(s.substitutes || []).length ? `<div class="lu-sub-h">Substitutes</div>${s.substitutes.map(row).join('')}` : ''}
  </section>`;
}
function subsCol(s, label) {
  if (!s || !(s.substitutes || []).length) return '';
  const row = (p) => {
    const st = _luStats && _luStats[p.id];
    const g = st ? (st.goals || 0) : 0, a = st ? (st.assists || 0) : 0;
    const ev = (g ? ` ⚽${g > 1 ? g : ''}` : '') + (a ? ` 👟${a > 1 ? a : ''}` : '');
    return `<div class="lu-row" onclick="openPlayerModal(${p.id})" style="cursor:pointer">
      <span class="lu-no">${p.number ?? ''}</span>
      <span class="lu-nm">${esc(p.name)}${p.atlas_rating != null ? `<span class="lu-ar${p.atlas_est ? ' est' : ''}" title="${p.atlas_est ? 'Estimated Atlastra rating' : 'Atlastra rating (best of League/UCL)'}">${p.atlas_est ? '~' : ''}${p.atlas_rating}</span>` : ''}<span class="lu-ev">${ev}</span></span>
      <span class="lu-pos">${esc(p.position || '')}</span>
      ${p.rating != null ? `<span class="ratingchip sm" style="border-color:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>` : ''}</div>`;
  };
  return `<section class="card lu-col"><div class="card-h"><h3>${esc(label)} — subs</h3></div>${s.substitutes.map(row).join('')}</section>`;
}

// ---- player match-stats modal (opened from a lineup chip / sub) ----
let _luStats = null, _luNames = {}, _luAtlas = {}, _luTourn = {};  // SofaScore id -> stats / name / {rating,est} / tournament G-A
async function ensureLineupStats() {
  if (_luStats) return _luStats;
  const d = await A('/api/match/player-stats');
  _luStats = {};
  if (d.available) for (const p of d.players) _luStats[p.id] = p;
  return _luStats;
}
const _pmCell = (label, val) => `<div class="pm-s"><span>${label}</span><b>${val}</b></div>`;
async function openPlayerModal(id) {
  if (id == null) return;
  const map = await ensureLineupStats();
  const p = map[id] || null;
  const name = (p && p.name) || _luNames[id] || 'Player';
  const f2 = (v) => v == null ? '—' : (+v).toFixed(2);
  const passAcc = p && p.passes ? Math.round((p.accurate_passes || 0) / p.passes * 100) + '%' : '—';
  const cards = p ? (('🟨'.repeat(p.yellow || 0)) + (p.red ? '🟥' : '')) : '';
  const grid = p ? [
    _pmCell('Minutes', p.minutes ?? '—'), _pmCell('Goals', p.goals ?? 0),
    _pmCell('Assists', p.assists ?? 0), _pmCell('Shots (SoT)', `${p.shots ?? 0} (${p.shots_on_target ?? 0})`),
    _pmCell('xG', f2(p.xg)), _pmCell('xA', f2(p.xa)),
    _pmCell('Passes', `${p.passes ?? 0} · ${passAcc}`), _pmCell('Key passes', p.key_passes ?? 0),
    _pmCell('Big chances', p.big_chances_created ?? 0),
    _pmCell('Dribbles', `${p.dribbles ?? 0} (${p.dribble_attempts ?? 0})`),
    _pmCell('Tackles', p.tackles ?? 0), _pmCell('Recoveries', p.recoveries ?? 0),
    _pmCell('Duels won', p.duels_won ?? 0), _pmCell('Touches', p.touches ?? 0), _pmCell('Fouls', p.fouls ?? 0),
  ].join('') : '<div class="placeholder-note">No match stats recorded for this player (likely an unused substitute).</div>';
  const chip = p && p.rating != null
    ? `<span class="ratingchip" style="border-color:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>` : '';
  const sub = p ? `${esc(p.position || '')}${p.number != null ? ' · #' + p.number : ''} · ${esc(p.team || '')}${p.started ? '' : ' · sub'}` : '';
  // whole-tournament totals (e.g. World Cup goals/assists), when this is a tournament match
  const tv = _luTourn[id];
  const tourn = tv ? `<div class="pm-tourn"><div class="pm-tourn-h">${esc(tv.label)} · Tournament stats</div>
      <div class="pm-tourn-row">
        <div class="pm-ts"><b>${tv.goals}</b><span>Goals</span></div>
        <div class="pm-ts"><b>${tv.assists}</b><span>Assists</span></div>
        <div class="pm-ts"><b>${tv.apps}</b><span>Apps</span></div>
      </div></div>` : '';
  const wrap = document.createElement('div');
  wrap.className = 'pm-overlay';
  wrap.innerHTML = `<div class="pm-card">
      <button class="pm-x" aria-label="Close">×</button>
      <div class="pm-head"><div class="pm-headtxt"><div class="pm-nm">${esc(name)} ${cards}</div><div class="pm-sub">${sub}</div>
        <div class="pm-club" id="pmClub"></div></div>
        <div class="pm-headright"><span class="pm-crest" id="pmCrest"></span>${chip}</div></div>
      ${tourn}
      ${p ? '<div class="pm-sec-h">This match</div>' : ''}
      <div class="pm-grid">${grid}</div>
      <div class="pm-heat"><div class="pm-heat-h">Match heatmap</div><canvas id="pmHeat" width="300" height="195"></canvas></div>
      ${(_luAtlas[id] && _luAtlas[id].rating != null && !_luAtlas[id].est)
        ? `<a class="btn btn-ghost pm-full" href="/player.html?name=${encodeURIComponent(name)}&from=match&eid=${EID}">View full season profile →</a>` : ''}
    </div>`;
  document.body.appendChild(wrap);
  const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey); };
  function onKey(e) { if (e.key === 'Escape') close(); }
  wrap.querySelector('.pm-x').onclick = close;
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
  document.addEventListener('keydown', onKey);
  // club team (so a national-team player shows the club they play for); on the
  // deployed server this is relay-fetched, so wait while it's pending.
  (async () => {
    let cl = await api('/api/player_club?id=' + id).catch(() => null);
    for (let i = 0; i < 10 && cl && cl.available === false && cl.pending; i++) {
      await new Promise(r => setTimeout(r, 2000));
      cl = await api('/api/player_club?id=' + id).catch(() => null);
    }
    const ce = wrap.querySelector('#pmClub'), cr = wrap.querySelector('#pmCrest');
    if (!cl || !cl.team) return;
    if (cr && cl.logo) cr.innerHTML = `<img class="pm-club-crest" src="${cl.logo}" alt="" title="${esc(cl.team)}" onerror="this.remove()">`;
    if (ce) ce.innerHTML = cl.national
      ? esc(cl.team)
      : `<span class="pm-club-lbl">Club:</span> <a onclick="event.stopPropagation();location.href='/team.html?name=${encodeURIComponent(cl.team)}'">${esc(cl.team)}</a>`;
  })();
  try {
    const h = await A('/api/match/heatmap?player_id=' + id);
    const cv = wrap.querySelector('#pmHeat');
    if (cv && h.available && h.points.length) drawPoints(cv, h.points);
    else if (cv) cv.closest('.pm-heat').innerHTML = '<div class="placeholder-note">No heatmap for this player.</div>';
  } catch { /* heatmap optional */ }
}
async function loadLineups(isRefresh) {
  const d = await A('/api/match/lineups');
  if (!d.available) { body().innerHTML = empty('Lineups not published yet.'); return; }
  const note = d.confirmed ? '' : '<div class="placeholder-note" style="margin-bottom:10px">⚠ Predicted lineup — not yet confirmed.</div>';
  // Manual refresh: the lineups tab isn't on the 30s live poll, so this pulls the
  // latest per-player match stats (ratings, goals/assists) on demand.
  const bar = `<div class="lp-refresh-bar"><span class="lp-updated" id="lpUpdated">Updated ${
    new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    }</span><button class="btn btn-ghost btn-sm lp-reload" id="lpReload">↻ Refresh stats</button></div>`;
  // id -> name (incl. subs) so the player-stats modal has a name even with no stats;
  // also keep the Atlastra rating/est so the modal only offers a profile link for a
  // player actually IN our DB (a real, non-estimated rating).
  for (const side of [d.home, d.away])
    for (const p of [...(side?.starting_xi || []), ...(side?.substitutes || [])])
      if (p.id != null) { _luNames[p.id] = p.name; _luAtlas[p.id] = { rating: p.atlas_rating, est: p.atlas_est }; _luTourn[p.id] = p.tourn || null; }
  // per-player match stats -> goal/assist icons on the chips (+ the modal); fresh
  // each load so icons reflect the latest score.
  const ps = await A('/api/match/player-stats');
  _luStats = {};
  if (ps.available) for (const pl of ps.players) _luStats[pl.id] = pl;
  const hx = d.home?.starting_xi || [], ax = d.away?.starting_xi || [];
  const hRows = parseFormation(d.home?.formation, hx.length);
  const aRows = parseFormation(d.away?.formation, ax.length);
  if (hRows && aRows) {
    const chips = placeSide(hx, hRows, true).map(c => chipHTML(c, true)).join('')
                + placeSide(ax, aRows, false).map(c => chipHTML(c, false)).join('');
    body().innerHTML = note + bar + `
      <section class="card">
        <div class="lp-head a"><span>${esc(head?.away || 'Away')}</span><b>${esc(d.away?.formation || '')}</b>${mgrTag(d.away?.manager)}</div>
        <div class="lineup-pitch">
          <span class="lp-mid"></span><span class="lp-circle"></span><span class="lp-spot"></span>
          <span class="lp-box top"></span><span class="lp-box bot"></span>
          <span class="lp-six top"></span><span class="lp-six bot"></span>
          ${chips}
        </div>
        <div class="lp-head h">${mgrTag(d.home?.manager)}<b>${esc(d.home?.formation || '')}</b><span>${esc(head?.home || 'Home')}</span></div>
      </section>
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:16px;margin-top:14px">
        ${subsCol(d.home, head?.home || 'Home')}${subsCol(d.away, head?.away || 'Away')}</div>`;
  } else {
    body().innerHTML = note + bar + `<div class="grid" style="grid-template-columns:1fr 1fr;gap:16px">
      ${lineupSideList(d.home, head?.home || 'Home')}${lineupSideList(d.away, head?.away || 'Away')}</div>`;
  }
  const rb = document.getElementById('lpReload');
  if (rb) rb.onclick = async () => { rb.disabled = true; rb.textContent = '↻ Refreshing…'; await loadLineups(true); };
  if (!isRefresh) {                                                // don't re-pop the modal on a manual refresh
    const pq = new URLSearchParams(location.search).get('player'); // deep-link to a player's match stats
    if (pq) openPlayerModal(+pq);
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

// ---- Key Moments (auto-generated commentary from goals + big chances) ----
async function loadKeyMoments() {
  const d = await A('/api/match/key-moments');
  if (!d.available || !d.moments.length) {
    body().innerHTML = empty('No key moments yet — goals and big chances will appear here with commentary.');
    return;
  }
  const rows = d.moments.map(m => {
    const mins = (m.minute ?? '') + (m.added_time ? '+' + m.added_time : '');
    return `<div class="km-row km-${m.kind} ${m.side || ''}">
      <span class="km-min">${mins}'</span>
      <span class="km-ic">${m.icon || '•'}</span>
      <span class="km-tx">${esc(m.text)}</span></div>`;
  }).join('');
  body().innerHTML = `<section class="card"><div class="card-h"><h3>Key Moments</h3>
      <span class="see">auto-generated commentary · goals &amp; big chances</span></div>
    <div class="km">${rows}</div></section>`;
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
          ? `<td class="tl"><span class="pl-no">${p.number ?? ''}</span>${p.has_profile
              ? `<a class="pl-name-link" href="/player.html?name=${encodeURIComponent(p.name)}&from=match&eid=${EID}" title="View ${esc(p.name)}'s profile">${esc(p.name)}</a>`
              : esc(p.name)} <span class="muted">${esc(p.position || '')} · ${esc((p.team || '').slice(0, 12))}</span></td>`
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
  ctx.strokeStyle = 'rgba(150,158,178,.38)'; ctx.lineWidth = 1.2;
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
  // SofaScore width axis: low-y = player's RIGHT side, so mirror rows (as the
  // season heatmap does) -- otherwise a left winger's heat lands on the right.
  for (let r = 0; r < GH; r++) for (let c = 0; c < GW; c++) {
    const v = grid[r][c] / (max || 1);
    if (v > 0.05) { ctx.fillStyle = heatColor(v); ctx.fillRect(c * cw, (GH - 1 - r) * ch, cw + 1.5, ch + 1.5); }
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

// ---- Preview (data-driven, works for upcoming national-team fixtures too) ----
async function loadPreview() {
  let d = await A('/api/fixture_preview');
  // The event header, then form / squad / h2h, arrive from separate relay-fetched
  // SofaScore calls a cycle apart. Wait through `pending` whether or not the fixture
  // is available yet (a cold cache reports available:false+pending) instead of
  // flashing "Preview not available".
  for (let i = 0; i < 34 && d && d.pending; i++) {
    body().innerHTML = '<section class="card"><div class="placeholder-note">Loading preview…</div></section>';
    await new Promise(r => setTimeout(r, RELAY_POLL_MS));
    d = await api('/api/fixture_preview?id=' + encodeURIComponent(EID));
  }
  if (!d || !d.available) { body().innerHTML = empty('Preview not available for this match.'); return; }
  const H = d.home, AW = d.away, p = d.prediction, h2h = d.h2h;
  const fp = (f) => `<span class="pv-fp ${f === 'W' ? 'w' : f === 'L' ? 'l' : 'd'}">${f}</span>`;
  const recent = (t) => `<div class="pv-recent"><div class="pv-form-pills">${t.recent.map(r => fp(r.result)).join('') || '<span class="muted" style="font-size:12px">No recent matches</span>'}</div>
    ${t.recent.slice(0, 5).map(r => `<div class="pv-rec-row">${fp(r.result)}<b>${r.gf}–${r.ga}</b><span>vs ${esc(r.opponent)}</span></div>`).join('')}</div>`;
  const keyc = (t) => `<div class="pv-keycol"><h5>${esc(t.name)}</h5>${(t.key || []).length
    ? t.key.map(k => `<a class="pv-kpl" href="/player.html?name=${encodeURIComponent(k.player)}&from=match&eid=${EID}"><span class="pv-kpl-ph">${avatarHTML(k.photo, k.player)}</span><span class="pv-kpl-tx"><b>${esc(k.player)}</b><span>${esc(k.position)}${k.club ? ' · ' + esc(k.club) : ''}</span></span><span class="pv-kpl-rat">${k.rating}</span></a>`).join('')
    : '<div class="muted" style="font-size:12px;padding:6px 0">No top-5-league players in the squad.</div>'}</div>`;
  body().innerHTML = `
    ${p ? `<section class="card pv-pred"><div class="card-h"><h3>Projection</h3><span class="muted" style="font-size:12px">bookmaker consensus</span></div>
      <div class="pv-bar"><i class="h" style="width:${p.home}%">${p.home}%</i><i class="d" style="width:${p.draw}%">${p.draw}%</i><i class="a" style="width:${p.away}%">${p.away}%</i></div>
      <div class="pv-bar-leg"><span><i class="dot h"></i>${esc(H.name)}</span><span><i class="dot d"></i>Draw</span><span><i class="dot a"></i>${esc(AW.name)}</span></div></section>` : ''}
    <section class="card pv-form"><div class="card-h"><h3>Recent form</h3></div><div class="pv-keycols">${recent(H)}${recent(AW)}</div></section>
    <section class="card"><div class="card-h"><h3>Key players</h3><span class="muted" style="font-size:12px">by Atlastra rating</span></div><div class="pv-keycols">${keyc(H)}${keyc(AW)}</div></section>
    <section class="card"><div class="card-h"><h3>Head-to-head</h3></div>${h2h
      ? `<div class="pv-h2h-tally"><div class="pv-h2h-t"><b>${h2h.home_wins ?? 0}</b><span>${esc(H.name)} wins</span></div><div class="pv-h2h-t"><b>${h2h.draws ?? 0}</b><span>Draws</span></div><div class="pv-h2h-t"><b>${h2h.away_wins ?? 0}</b><span>${esc(AW.name)} wins</span></div></div>`
      : '<div class="muted" style="padding:8px">No previous meetings on record.</div>'}</section>`;
}

const empty = (msg) => `<section class="card"><div class="placeholder-note">${esc(msg)}</div></section>`;

// ---- boot + live polling ----
(async () => {
  if (!EID) { document.getElementById('hero').innerHTML = empty('No match selected.'); return; }
  document.getElementById('hero').innerHTML = '<div class="placeholder-note">Loading match…</div>';
  await loadHeader();
  if (!active) active = head?.status === 'notstarted' ? 'predict' : 'lineups';   // default by status
  renderTabs();
  await loadActive();
  if (head?.status === 'inprogress') {
    timer = setInterval(async () => {
      await loadHeader();
      if (['stats', 'timeline', 'moments', 'players', 'prediction', 'preview'].includes(active)) loadActive(true);
      if (head?.status !== 'inprogress') clearInterval(timer);   // stop once final
    }, 30000);
  }
})();
