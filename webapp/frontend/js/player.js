renderSidebar('Players');
Chart.defaults.color = '#7f8aa3';
Chart.defaults.borderColor = 'rgba(150,158,178,.22)';
Chart.defaults.font.family = 'Inter';
let radarChart, careerChart, finChart;
let curSeason = null;                                // selected season (raw code)
const setText = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };

// stat tiles read from a scope object {games,minutes,goals,...,pass_accuracy_pct}.
// kind: 'count' shown as-is, 'per90' = v/min*90, 'pct' = v%, 'dec' = 1 decimal total.
// A tile def is [icon,key,label,kind]; a nested [[def],[def]] renders one grouped
// (double-wide) tile — goals+xG, assists+xA and duels+duels% are paired.
const TOTAL_DEFS = [
  ['👕', 'games', 'Apps', 'count'],
  [['⚽', 'goals', 'Goals', 'count'], ['◎', 'xg', 'xG', 'dec']],
  [['🅰', 'assists', 'Assists', 'count'], ['⚲', 'xa', 'xA', 'dec']],
  ['💡', 'chances_created', 'Chances', 'count'],
  ['★', 'big_chances_created', 'Big Chances', 'count'],
  ['⚡', 'dribbles_completed', 'Dribbles', 'count'],
  [['💪', 'duels_won', 'Duels Won', 'count'], ['％', 'duels_won_pct', 'Duels %', 'pct']],
  ['🛡', 'tackles', 'Tackles', 'count'], ['✋', 'interceptions', 'Interceptions', 'count'],
  ['◉', 'pass_accuracy_pct', 'Pass Acc', 'pct'],
  [['↗', 'progressive_passes_total', 'Prog. Passes', 'count'],
   ['🏃', 'progressive_carries_total', 'Prog. Carries', 'count']],
];
const PER90_DEFS = [
  ['👕', 'games', 'Apps', 'count'],
  [['⚽', 'goals', 'Goals / 90', 'per90'], ['◎', 'xg', 'xG / 90', 'per90']],
  [['🅰', 'assists', 'Assists / 90', 'per90'], ['⚲', 'xa', 'xA / 90', 'per90']],
  ['💡', 'chances_created', 'Chances / 90', 'per90'],
  ['★', 'big_chances_created', 'Big Ch. / 90', 'per90'],
  ['⚡', 'dribbles_completed', 'Dribbles / 90', 'per90'],
  [['💪', 'duels_won', 'Duels / 90', 'per90'], ['％', 'duels_won_pct', 'Duels %', 'pct']],
  ['🛡', 'tackles', 'Tackles / 90', 'per90'], ['◉', 'pass_accuracy_pct', 'Pass Acc', 'pct'],
  [['↗', 'progressive_passes', 'Prog. Passes / 90', 'dec'],
   ['🏃', 'progressive_carries', 'Prog. Carries / 90', 'dec']],
];
const SCOPES = [['league', 'League'], ['ucl', 'UCL'], ['combined', 'Combined'], ['worldcup', 'World Cup']];
let statScopes = {}, scopeTotals = 'combined', scopePer90 = 'combined';
let tilePct = {};        // {league:{...}, ucl:{...}, combined:{...}} — one map per scope
let wcTilePct = {};      // per-stat percentile vs the WC field (WC scope)
let seasonLabelTxt = ''; // the selected season, for the percentile tooltips

function fmtTile(def, s) {
  const [, key, , kind] = def, v = s ? s[key] : null;
  if (v == null) return '—';
  if (kind === 'count') return Math.round(v).toLocaleString();
  if (kind === 'dec') return v.toFixed(1);
  if (kind === 'pct') return Math.round(v) + '%';
  const m = s.minutes || 0;                       // per90
  return m ? (v / m * 90).toFixed(2) : '—';
}
const pctColor = (p) => p >= 80 ? '#2fbf71' : p >= 60 ? '#7d9f3a' : p >= 40 ? '#c9a227' : '#c97a27';
const ordinal = (n) => { const v = n % 100, s = ['th', 'st', 'nd', 'rd']; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
// percentile bar + number shown under a stat (skip Apps — no peer percentile)
function pctBar(key, pctMap, peer) {
  const p = (pctMap || {})[key];
  if (p == null || key === 'games') return '';
  return `<div class="tpctw" title="${ordinal(p)} percentile vs ${peer} (100 = best in position)">
    <div class="tpct"><i style="width:${p}%;background:${pctColor(p)}"></i></div>
    <span class="tpctn" style="color:${pctColor(p)}">${ordinal(p)}</span></div>`;
}
// Each scope ranks against ITS OWN field: the League tiles against league players, the
// UCL tiles against the players in that season's Champions League, the World Cup tiles
// against that edition's field. Ranking every scope against the league — which is what
// a single percentile map did — left the bar sitting still while the number under it
// changed competition.
const PEER_TXT = {
  league: 'the same position across all top-5 leagues',
  ucl: 'the same position in that season’s Champions League',
  combined: 'the same position, league + UCL combined',
  worldcup: 'the World Cup field, same position',
};
const oneTile = (def, s, scope) =>
  `<div class="ic">${def[0]}</div><b>${fmtTile(def, s)}</b><span>${def[2]}</span>${
    pctBar(def[1], scope === 'worldcup' ? wcTilePct : (tilePct[scope] || {}),
           PEER_TXT[scope] || PEER_TXT.league)}`;

function renderTiles(elId, defs, scope) {
  const s = statScopes[scope];
  document.getElementById(elId).innerHTML = defs.map(d => {
    if (Array.isArray(d[0])) {                      // grouped (double-wide) tile
      return `<div class="tile pair">${d.map(sub => `<div class="tsub">${oneTile(sub, s, scope)}</div>`).join('')}</div>`;
    }
    return `<div class="tile">${oneTile(d, s, scope)}</div>`;
  }).join('');
}
// Build a League/UCL/Combined toggle once; a delegated listener on the container
// survives tile re-renders, and we only flip the .active class + redraw on click.
function setupScopeTog(togId, tilesId, defs, getScope, setScope) {
  const tog = document.getElementById(togId);
  tog.innerHTML = SCOPES.map(([k, lab]) =>
    `<button class="sct" data-k="${k}"${statScopes[k] ? '' : ' disabled title="no minutes"'}>${lab}</button>`).join('');
  const update = () => {
    const sc = getScope();
    tog.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.k === sc));
    renderTiles(tilesId, defs, sc);
  };
  tog.onclick = (e) => {
    const b = e.target.closest('button');
    if (b && !b.disabled) { setScope(b.dataset.k); update(); }
  };
  update();
}
const PLAYSTYLE = { MID: ['Deep-Lying Playmaker', 'Progressive Passer', 'Press Resistant', 'Tempo Controller', 'Space Creator'],
  FWD: ['Advanced Forward', 'Poacher', 'Pressing Forward', 'Box Threat'],
  DEF: ['Ball-Playing Defender', 'Stopper', 'Aerial Dominator', 'Progressive Carrier'], GK: ['Sweeper Keeper', 'Shot Stopper'] };
const TECH = [['La Pausa', 24], ['Body Feint', 18], ['Outside Foot Pass', 15], ['Third-Man Combination', 12], ['Half Turn', 9]];

function drawGauge(canvasId, rating, w = 124, h = 78) {
  const c = document.getElementById(canvasId);
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h - 8, rad = w * 0.42;
  const frac = rating ? Math.max(0, Math.min(1, rating / 99)) : 0;
  const g = ctx.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, '#5570f0'); g.addColorStop(1, '#7d5cf5');
  for (const [col, a0, a1] of [['rgba(150,158,178,.22)', Math.PI, 2 * Math.PI], [g, Math.PI, Math.PI + Math.PI * frac]]) {
    ctx.beginPath(); ctx.lineWidth = Math.round(w * 0.073); ctx.lineCap = 'round';
    ctx.strokeStyle = col; ctx.arc(cx, cy, rad, a0, a1); ctx.stroke();
  }
}

// The name in the URL can be a partial ("Saka"), which every endpoint resolves the same
// way. /api/player is the one that reports the canonical spelling, so cards started before
// it answers label themselves with what they were given until it does.
let _canonName = null, _formDone = false, _sideCardsDone = false;
const nameLabel = () => _canonName || current;   // `current` is set at boot, below

async function load(name, careerStat = 'xa', season = null) {
  let url = '/api/player?name=' + encodeURIComponent(name) + '&career_stat=' + careerStat;
  if (season) url += '&season=' + encodeURIComponent(season);
  const p = await api(url);
  if (!p.name) { document.getElementById('crumb').textContent = 'not found'; return; }
  _canonName = p.name;
  sideCards(p.name);                    // once the page can render — see sideCards()
  document.getElementById('crumb').textContent = p.name;
  document.getElementById('pname').innerHTML = p.name + ' <span class="verified">✔</span>';

  // fan comment thread (mount once per page; keyed by canonical player name)
  if (window.mountComments && !window._cmtsMounted) {
    window._cmtsMounted = true;
    mountComments('player:' + p.name, document.getElementById('comments'),
      { title: 'Fan Comments', subject: p.name });
  }

  // season selector + pinned-analysis labelling. The stat tiles, League/UCL
  // gauges and avg rating follow the chosen season; the radar / SWOT / archetype
  // / signature actions / heatmap only exist for the pinned (latest) season.
  curSeason = p.season;
  const seasons = p.seasons || [];
  const selLabel = (seasons.find(s => s.value === p.season) || {}).label || '';
  document.getElementById('seasonSel').innerHTML = seasons.map(s =>
    `<option value="${s.value}"${s.value === p.season ? ' selected' : ''}>${s.label}</option>`).join('');
  const banner = document.getElementById('pinnedBanner');
  // hist_level: what the radar/SWOT/heatmap reflect for the chosen season —
  // 'current' (full datamb), 'reduced' (per-season, Understat+FotMob), 'none'.
  if (p.hist_level === 'current') {
    banner.hidden = true;
    setText('radarNote', 'Compared to same position in Top-5 leagues · ' + selLabel);
    setText('simNote', 'By statistical profile · ' + selLabel);
    setText('ratingNote', 'Common-metric rating · combined stats below');
  } else {
    banner.hidden = false;
    setText('ratingNote', 'Common-metric rating · ' + selLabel);
    setText('simNote', 'By statistical profile · ' + p.pinned_season + ' (latest)');
    if (p.hist_level === 'reduced') {
      banner.innerHTML = `Showing <b>${selLabel}</b>. Stats, League/UCL ratings, radar, ` +
        `strengths &amp; weaknesses and heatmap are for this season (radar uses a reduced ` +
        `metric set). Composite rating, archetype &amp; signature actions reflect ` +
        `<b>${p.pinned_season}</b> (latest).`;
      setText('radarNote', 'Same position · ' + selLabel + ' · reduced metric set');
    } else {                                   // 'none' (pre-2020/21)
      banner.innerHTML = `Showing <b>${selLabel}</b> statistics &amp; League/UCL ratings. ` +
        `Radar, strengths/weaknesses &amp; heatmap aren't available this far back; ` +
        `archetype &amp; signature actions reflect <b>${p.pinned_season}</b> (latest).`;
      setText('radarNote', 'Not available for ' + selLabel);
    }
  }
  const photoEl = document.querySelector('.ph .photo');
  if (photoEl) photoEl.innerHTML = avatarHTML(p.photo, p.name);
  const credEl = document.getElementById('photoCredit');
  if (credEl) {
    const c = p.photo_credit;
    credEl.innerHTML = c
      ? `<a href="${c.page || '#'}" target="_blank" rel="noopener" title="${c.credit || ''} — ${c.license || ''}">📷 ${c.credit || 'Wikimedia'}${c.license ? ' · ' + c.license : ''}</a>`
      : '';
  }
  document.getElementById('pteam').innerHTML = crestHTML(p.team_logo, 'crest-sm') + (p.team || '');
  document.getElementById('ppos').textContent = p.detailed_position || p.position_group;
  document.getElementById('page').textContent = p.age ?? '—';
  document.getElementById('pnat').textContent =
    (p.country_code ? flagEmoji(p.country_code) + ' ' : '') + (p.nationality || '—');
  document.getElementById('pmv').textContent = eurM(p.market_value_eur);
  const av = document.getElementById('pavg');
  av.textContent = p.avg_rating == null ? '—' : (+p.avg_rating).toFixed(1);
  av.style.color = p.avg_rating == null ? '' : ratingColor(p.avg_rating);
  document.getElementById('compareLink').href = '/compare.html?name=' + encodeURIComponent(p.name);
  document.getElementById('scoutLink').href = '/scoutreport.html?name=' + encodeURIComponent(p.name) +
    (curSeason ? '&season=' + curSeason : '');
  document.getElementById('cardLink').href = '/card.html?name=' + encodeURIComponent(p.name);

  // follow / watchlist (localStorage via Store)
  const item = { id: p.name, name: p.name, team: p.team,
    position: p.detailed_position || p.position_group, rating: p.rating, photo: p.photo };
  const fb = document.getElementById('followBtn'), wb = document.getElementById('watchBtn');
  const syncF = () => { const on = Store.has('players', p.name); fb.classList.toggle('on', on); fb.textContent = on ? '✓ Following' : '★ Follow'; };
  const syncW = () => { const on = Store.has('watchlist', p.name); wb.classList.toggle('on', on); wb.textContent = on ? '🔖 On watchlist' : '🔖 Watch'; };
  fb.onclick = () => { Store.toggle('players', item); syncF(); };
  wb.onclick = () => { Store.toggle('watchlist', item); syncW(); };
  syncF(); syncW();
  wireFavBtn(document.getElementById('favBtn'), 'favPlayers', { name: p.name, photo: p.photo });


  // Recent Form — per-match log from FotMob (result, rating, G/A). Needs the FotMob
  // player id, which is embedded in the photo URL (…/playerimages/<id>.png), so unlike
  // the cards in sideCards() it does have to wait for the profile. Neither it nor the
  // bio depends on the chosen season, so a season change leaves both alone.
  (function () {
    const pm = /playerimages\/(\d+)\./.exec(p.photo || '');
    if (!pm || _formDone) return;
    _formDone = true;
    // Preferred foot + height (FotMob) — facts our DB doesn't carry.
    api('/api/player_bio?pid=' + pm[1]).then((bio) => {
      if (!bio || !bio.available) return;
      if (bio.height) { document.getElementById('pheight').textContent = bio.height; document.getElementById('mHeight').hidden = false; }
      if (bio.foot) { document.getElementById('pfoot').textContent = bio.foot; document.getElementById('mFoot').hidden = false; }
    }).catch(() => {});
    api('/api/player_form?pid=' + pm[1]).then((f) => {
      if (!f || !f.available || !(f.matches || []).length) return;
      const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
      const rc = (r) => r >= 7.5 ? 'rt-hi' : r >= 6.5 ? 'rt-mid' : 'rt-lo';
      const when = (ts) => ts ? new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '';
      const rows = f.matches.map((x) => {
        const rp = x.result ? `<span class="fm-res ${x.result}">${x.result}</span>` : '';
        const ga = [];
        if (x.goals) ga.push(`<span title="${x.goals} goal${x.goals > 1 ? 's' : ''}">⚽ ${x.goals}</span>`);
        if (x.assists) ga.push(`<span title="${x.assists} assist${x.assists > 1 ? 's' : ''}">🅰 ${x.assists}</span>`);
        if (x.red) ga.push('<span class="fm-cd red" title="red card"></span>');
        else if (x.yellow) ga.push('<span class="fm-cd yel" title="yellow card"></span>');
        const rating = x.rating != null
          ? `<span class="fm-rt ${rc(x.rating)}">${x.rating.toFixed(1)}</span>`
          : (x.minutes > 0 && x.minutes < 20
            ? '<span class="fm-nr" title="Too few minutes to be rated">Not enough mins</span>'
            : '<span class="fm-nr" title="No match rating available">No rating</span>');
        const link = x.event_id ? `/match.html?id=${x.event_id}` : '';
        return `<tr class="fm-row"${link ? ` data-href="${link}"` : ''}>
          <td class="fm-date">${when(x.date_ts)}</td>
          <td class="fm-opp"><span class="fm-sc">${rp}${x.gf ?? ''}<i>–</i>${x.ga ?? ''}</span>
            <span class="fm-tm">${x.home ? 'vs' : '@'} ${esc(x.opponent)}</span>
            <span class="fm-comp">${esc(x.competition || '')}</span></td>
          <td class="fm-ga">${ga.join(' ')}</td>
          <td class="fm-mins">${x.minutes}'</td>
          <td class="fm-rating">${x.motm ? '<span class="fm-motm" title="Player of the Match">★</span>' : ''}${rating}</td>
        </tr>`;
      }).join('');
      document.getElementById('recentForm').innerHTML = `<div class="fm-wrap"><table class="fm-tbl"><tbody>${rows}</tbody></table></div>`;
      const s = f.summary || {};
      const pills = (s.form || '').split('').map((r) => `<i class="fm-pill ${r}">${r}</i>`).join('');
      const bits = [];
      if (s.avg_rating != null) bits.push(`Avg <b>${s.avg_rating.toFixed(2)}</b>`);
      if (s.goals) bits.push(`${s.goals} G`);
      if (s.assists) bits.push(`${s.assists} A`);
      document.getElementById('formSummary').innerHTML =
        `<span class="fm-pills">${pills}</span>${bits.length ? ' · ' + bits.join(' · ') : ''}`;
      document.getElementById('formCard').style.display = '';
      document.querySelectorAll('#recentForm tr[data-href]').forEach((tr) => {
        tr.onclick = () => { location.href = tr.getAttribute('data-href'); };
      });
    }).catch(() => {});
  })();


  // dual ratings (League + UCL, common-metric)
  const lg = p.ratings?.league, ucl = p.ratings?.ucl;
  document.getElementById('rLeague').textContent = lg?.rating ?? '—';
  document.getElementById('cLeague').textContent = lg ? lg.classification : 'not rated';
  drawGauge('gaugeLeague', lg?.rating);
  document.getElementById('rUcl').textContent = ucl?.rating ?? '—';
  document.getElementById('cUcl').textContent = ucl ? ucl.classification : 'no UCL minutes';
  drawGauge('gaugeUcl', ucl?.rating);
  // World Cup gauge — only for seasons that had a World Cup the player featured in
  const wc = p.ratings?.worldcup, wcBox = document.getElementById('rgaugeWc');
  if (wc) {
    wcBox.style.display = '';
    document.getElementById('rWc').textContent = wc.rating;
    document.getElementById('cWc').textContent = `${wc.classification} · ${wc.apps} app${wc.apps === 1 ? '' : 's'}`;
    drawGauge('gaugeWc', wc.rating);
  } else { wcBox.style.display = 'none'; }

  // total + per-90 stat tiles, each with its own League/UCL/Combined scope toggle
  statScopes = p.stats_scopes || {};
  tilePct = p.tile_pct || {};
  wcTilePct = p.wc_tile_pct || {};
  const dflt = statScopes.combined ? 'combined' : Object.keys(statScopes)[0];
  if (!statScopes[scopeTotals]) scopeTotals = dflt;
  if (!statScopes[scopePer90]) scopePer90 = dflt;
  setupScopeTog('togTotals', 'totalTiles', TOTAL_DEFS, () => scopeTotals, k => { scopeTotals = k; });
  setupScopeTog('togPer90', 'tiles', PER90_DEFS, () => scopePer90, k => { scopePer90 = k; });

  // strengths / weaknesses
  document.getElementById('strengths').innerHTML = p.strengths.map(s => `<li class="ok">✔ ${s}</li>`).join('') || '<li class="muted">—</li>';
  document.getElementById('weaknesses').innerHTML = p.weaknesses.map(s => `<li class="bad">✘ ${s}</li>`).join('') || '<li class="muted">—</li>';

  // archetype + similar players (use case 10)
  renderArchetype(p.archetype);

  // signature actions (use case 9): real, from the player's standout per-90 actions
  document.getElementById('tech').innerHTML = (p.signature_actions || []).map((a, i) =>
    `<div class="t"><span class="rk">${i + 1}</span><span class="tn" style="width:150px">${a.name}</span>
      <span class="bar" title="${a.percentile}th percentile"><i style="width:${a.percentile}%"></i></span>
      <b>${a.value}<span class="per90">/90</span></b></div>`).join('')
    || '<div class="muted">Not enough on-ball data.</div>';

  drawRadar(p.radar);
  drawCareer(p.career, careerStat);
  drawHeatmap(p.heatmap);
  renderWorldCups(p.worldcups, p.country_code);
}

// World Cup record card: one row per edition the player featured in (newest first).
// Hidden entirely when the player has no World Cup appearances.
function renderWorldCups(wcs, cc) {
  const card = document.getElementById('worldCupCard');
  if (!wcs || !wcs.length) { card.style.display = 'none'; return; }
  card.style.display = '';
  const flag = flagEmoji(cc) || '';   // country_code is a FIFA 3-letter code (ESP/NOR/…)
  const cell = (v, l) => `<div class="wc-s"><b>${v == null ? '—' : v}</b><span>${l}</span></div>`;
  document.getElementById('worldCups').innerHTML = wcs.map(w => `
    <div class="wc-row">
      <div class="wc-ed"><span class="wc-yr">${w.edition}</span><span class="wc-lbl">World Cup</span></div>
      <div class="wc-team">${flag} <span>${w.team || ''}</span><small>${w.position || ''}</small></div>
      <div class="wc-stats">
        ${cell(w.apps, 'Apps')}${cell(w.minutes != null ? w.minutes + "'" : null, 'Minutes')}
        ${cell(w.goals, 'Goals')}${cell(w.assists, 'Assists')}
        ${cell(w.sofa_rating != null ? w.sofa_rating.toFixed(2) : null, 'Avg Rating')}
      </div>
      <div class="wc-atlas" style="--wc-c:${pctColor(w.atlas_rating || 0)}">
        <b>${w.atlas_rating == null ? '—' : w.atlas_rating}</b><span>${w.atlas_class || ''}</span></div>
    </div>`).join('');
}

// SofaScore season heatmap: blurred density over a pitch (attacks left -> right).
// Conventional football scale: faint green (low) -> yellow -> orange -> red (high),
// transparent at the very low end so the pitch shows through (no blue wash).
function heatColor(v) {
  v = Math.min(1, v);
  const t = Math.min(1, v * 1.4);                     // saturate toward red faster
  const hue = 145 - 145 * t;                          // 145 green -> 0 red
  const light = 50 + 12 * t;                          // brighter at the hot end
  const alpha = Math.max(0, Math.min(0.95, (v - 0.03) * 1.7));
  return `hsla(${hue}, 100%, ${light}%, ${alpha})`;
}
function drawPitch(ctx, W, H) {
  ctx.strokeStyle = 'rgba(150,158,178,.38)'; ctx.lineWidth = 1.5;
  ctx.strokeRect(2, 2, W - 4, H - 4);
  ctx.beginPath(); ctx.moveTo(W / 2, 2); ctx.lineTo(W / 2, H - 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.min(W, H) * 0.13, 0, 2 * Math.PI); ctx.stroke();
  const bw = W * 0.15, bh = H * 0.55;
  ctx.strokeRect(2, (H - bh) / 2, bw, bh); ctx.strokeRect(W - 2 - bw, (H - bh) / 2, bw, bh);
}
function drawHeatmap(grid) {
  const c = document.getElementById('heat'); if (!c) return;
  const ctx = c.getContext('2d'), W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0e1f17'; ctx.fillRect(0, 0, W, H);
  const note = document.getElementById('heatNote');
  if (!grid || !grid.length) {
    if (note) note.textContent = '';
    drawPitch(ctx, W, H);
    ctx.fillStyle = '#7f8aa3'; ctx.font = '13px Inter'; ctx.textAlign = 'center';
    ctx.fillText('No heatmap data', W / 2, H / 2 + 4); ctx.textAlign = 'left';
    return;
  }
  if (note) note.textContent = 'Domestic league · this season';
  const GH = grid.length, GW = grid[0].length, cw = W / GW, ch = H / GH;
  ctx.save(); ctx.filter = 'blur(10px)';
  for (let r = 0; r < GH; r++) for (let col = 0; col < GW; col++) {
    const v = grid[r][col];
    // SofaScore width axis: low-y = player's RIGHT side. Mirror rows so the
    // right flank renders at the bottom on a left->right attacking pitch.
    if (v > 0.02) { ctx.fillStyle = heatColor(v); ctx.fillRect(col * cw, (GH - 1 - r) * ch, cw + 1.5, ch + 1.5); }
  }
  ctx.restore();
  drawPitch(ctx, W, H);
}

function renderArchetype(a) {
  const el = document.getElementById('archetype'), sim = document.getElementById('similar');
  if (!a || !a.archetype) {
    el.innerHTML = '<div class="muted">Not enough data to classify.</div>';
    sim.innerHTML = ''; document.getElementById('archMore').style.display = 'none';
    return;
  }
  document.getElementById('archMore').href = '/archetypes.html?role=' + encodeURIComponent(a.archetype);
  el.innerHTML = `
    <div class="arch-head"><div class="arch-name">${a.archetype}<span class="arch-fit">${a.fit ?? '—'}% fit</span></div>
      <div class="arch-grp">${a.group_label}${a.archetype2 ? ` · also ${a.archetype2} (${a.fit2 ?? '—'}%)` : ''}</div></div>
    <p class="arch-blurb">${a.blurb || ''}</p>
    <div class="arch-traits">${(a.traits || []).map(t =>
      `<span class="trait">${t.label}<b>${t.pct}</b></span>`).join('') || '<span class="muted">—</span>'}</div>`;
  sim.innerHTML = (a.similar || []).map(s => `
    <div class="prow" onclick="location.href='${pHref(s.player)}'" style="cursor:pointer">
      <span class="pic">${avatarHTML(s.photo, s.player)}</span>
      <span style="flex:1"><div class="nm">${s.player}</div><div class="sub">${s.team || ''} · ${s.position || ''}</div></span>
      <span class="end"><span class="simpct">${s.similarity ?? ''}%</span>${s.rating != null ? `<b class="ratingchip sm">${s.rating}</b>` : ''}</span>
    </div>`).join('') || '<div class="muted">—</div>';
}

function drawRadar(radar) {
  if (radarChart) radarChart.destroy();
  const cv = document.getElementById('radar');
  if (!radar || !radar.length) {                  // no radar for this season (pre-2020/21)
    cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
    return;
  }
  const labels = radar.map(r => r.axis);
  const data = radar.map(r => r.value ?? 50);     // axis not measured for this position -> neutral
  radarChart = new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: { labels, datasets: [{ data, fill: true, backgroundColor: 'rgba(85,112,240,.35)',
      borderColor: '#7d5cf5', pointBackgroundColor: '#7d5cf5', pointRadius: 3 }] },
    options: { plugins: { legend: { display: false } }, scales: { r: {
      min: 0, max: 100, ticks: { display: false, stepSize: 25 },
      grid: { color: 'rgba(150,158,178,.22)' }, angleLines: { color: 'rgba(150,158,178,.22)' },
      pointLabels: { color: '#8a93a6', font: { size: 11 },
        callback: (l, i) => `${l}  ${data[i]}` } } } },
  });
}

function drawCareer(career, stat) {
  if (careerChart) careerChart.destroy();
  careerChart = new Chart(document.getElementById('career'), {
    type: 'line',
    data: { labels: career.map(c => c.season), datasets: [{ data: career.map(c => c.value),
      borderColor: '#5570f0', backgroundColor: 'rgba(85,112,240,.15)', fill: true, tension: .35,
      pointBackgroundColor: '#5570f0', pointRadius: 4 }] },
    options: { plugins: { legend: { display: false }, tooltip: { enabled: true } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: 'rgba(150,158,178,.22)' }, beginAtZero: true } } },
  });
}

// Finishing vs Expected: headline (Goals/xG/differential/conversion + peer percentile
// verdict) plus a cumulative Goals-vs-xG line chart across the season's appearances.
function renderFinishing(f) {
  const sign = f.diff > 0 ? '+' : '';
  const dcls = f.diff >= 0.5 ? 'good' : f.diff <= -0.5 ? 'bad' : 'neutral';
  document.getElementById('finVerdict').innerHTML =
    `<span class="fin-badge ${f.verdict_class}">${f.verdict}</span>`;
  const stat = (v, l, cls) => `<div class="fin-stat"><b class="${cls || ''}">${v}</b><span>${l}</span></div>`;
  const pctBlock = f.percentile == null ? '' :
    `<div class="fin-pct"><label>Finishing vs ${(f.position || 'position')} peers
       <b>${f.percentile}<sup>th</sup> pct</b></label>
       <div class="fin-pbar"><i class="${dcls}" style="width:${Math.max(2, f.percentile)}%"></i></div></div>`;
  document.getElementById('finHead').innerHTML =
    `<div class="fin-head">
       ${stat(f.goals, 'Goals')}
       ${stat(f.xg.toFixed(1), 'Expected (xG)')}
       ${stat(sign + f.diff.toFixed(1), 'G − xG', dcls)}
       ${stat(f.conversion.toFixed(0) + '%', 'Conversion')}
       ${stat(f.shots_per_goal == null ? '—' : f.shots_per_goal.toFixed(1), 'Shots / Goal')}
     </div>${pctBlock}`;
  const cohort = f.cohort ? ` among ${f.cohort} peers` : '';
  document.getElementById('finNote').innerHTML =
    `Cumulative goals vs expected (xG) across ${f.timeline.length} domestic appearances. ` +
    (f.diff >= 0.5 ? `Scoring <b>${sign}${f.diff.toFixed(1)}</b> above what his chances were worth`
      : f.diff <= -0.5 ? `Scoring <b>${f.diff.toFixed(1)}</b> below what his chances were worth`
        : 'Converting his chances almost exactly as expected') +
    (f.percentile == null ? '' : ` — ${f.percentile}th percentile${cohort}.`);
  drawFinishing(f.timeline);
}

function drawFinishing(tl) {
  if (finChart) finChart.destroy();
  const short = (d) => { const t = new Date(d); return isNaN(t) ? '' : t.toLocaleDateString([], { month: 'short', day: 'numeric' }); };
  finChart = new Chart(document.getElementById('finChart'), {
    type: 'line',
    data: {
      labels: tl.map((m) => short(m.date)),
      datasets: [
        { label: 'Goals', data: tl.map((m) => m.cum_goals), borderColor: '#34c46a',
          backgroundColor: 'rgba(52,196,106,.14)', fill: true, tension: .2, pointRadius: 2, borderWidth: 2.5 },
        { label: 'Expected (xG)', data: tl.map((m) => m.cum_xg), borderColor: '#9aa4ba',
          borderDash: [5, 4], fill: false, tension: .2, pointRadius: 0, borderWidth: 2 },
      ],
    },
    options: {
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, usePointStyle: true } },
        tooltip: {
          callbacks: {
            title: (items) => { const m = tl[items[0].dataIndex]; return `${m.home ? 'vs' : '@'} ${m.opp}`; },
            afterBody: (items) => { const m = tl[items[0].dataIndex]; return `Match: ${m.goals} G · ${m.xg.toFixed(2)} xG`; },
          },
        },
      },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 12, autoSkip: true } },
        y: { grid: { color: 'rgba(150,158,178,.22)' }, beginAtZero: true } },
    },
  });
}

// Fair Value model: actual (Transfermarkt) vs the model's estimate + verdict, then
// the explainable value-drivers (each feature's € impact on the estimate).
function renderValue(v) {
  const eurM = (e) => '€' + (e / 1e6).toFixed(e >= 1e8 ? 0 : 1) + 'm';
  document.getElementById('valueBody').innerHTML = (() => {
    const badge = `<span class="fin-badge ${v.verdict_class}">${v.verdict}</span>`;
    const gap = Math.round((v.ratio - 1) * 100);
    const gapTxt = gap === 0 ? 'in line with' : `${Math.abs(gap)}% ${gap > 0 ? 'above' : 'below'}`;
    const scale = Math.max(v.actual_eur, v.predicted_eur) || 1;
    const bar = (val, cls, lbl) => `<div class="val-row">
      <label>${lbl}<b>${eurM(val)}</b></label>
      <div class="val-bar"><i class="${cls}" style="width:${Math.max(3, val / scale * 100)}%"></i></div></div>`;
    const drivers = (v.drivers || []).map((d) => {
      const pos = d.impact_m >= 0;
      const mag = Math.min(100, Math.abs(d.impact_m) / 60 * 100);
      return `<div class="val-drv">
        <span class="val-dl">${d.label}</span>
        <span class="val-dbar"><i class="${pos ? 'good' : 'bad'}" style="width:${mag}%"></i></span>
        <b class="${pos ? 'good' : 'bad'}">${pos ? '+' : '−'}€${Math.abs(d.impact_m).toFixed(1)}m</b>
      </div>`;
    }).join('');
    const m = v.model;
    const note = m ? `Gradient-boosting model · R²≈${m.r2.toFixed(2)}, avg error ≈€${Math.round(m.mae_m)}m over ${m.n} valued players. `
      : '';
    return `<div class="val-head">${badge}
        <span class="val-sum">Model estimate <b>${gapTxt}</b> the market value</span></div>
      <div class="val-cmp">
        ${bar(v.actual_eur, 'act', 'Market value (Transfermarkt)')}
        ${bar(v.predicted_eur, 'pred', 'Model estimate')}
      </div>
      <h5 class="val-dh">What drives the estimate</h5>
      <div class="val-drivers">${drivers || '<span class="muted">—</span>'}</div>
      <div class="muted" style="font-size:11.5px;margin-top:10px">${note}Estimated from age, rating,
        per-90 output and league — it does not see contract length, hype or reputation, so
        highly-priced prospects often read "overvalued".</div>`;
  })();
}

// Availability — share of his club's league matches he actually played, from his
// first appearance for them onward, plus the runs he missed. Never called injury:
// a suspension looks the same from the match log, and only length is observed.
function renderAvailability(a) {
  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const when = (d) => new Date(d).toLocaleDateString([], { month: 'short', year: '2-digit' });
  const tone = (p) => (p >= 90 ? 'great' : p >= 75 ? 'good' : p >= 50 ? 'warn' : 'bad');

  document.getElementById('availVerdict').innerHTML =
    `<span class="fin-badge ${a.verdict_class}">${esc(a.verdict)}</span>`;

  // one column per season: height is availability, so a lost season is a notch
  const bars = (a.career || []).map((c) => `
    <div class="av-col" title="${esc(c.label)} — played ${c.played} of ${c.window}${
      c.longest >= 5 ? `, longest absence ${c.longest} matches` : ''}">
      <div class="av-track"><i class="${tone(c.pct)}" style="height:${Math.max(3, c.pct)}%"></i></div>
      <label>${esc(c.label.slice(2, 4))}</label>
    </div>`).join('');

  const spells = (a.spells || []).length
    ? `<h5 class="val-dh" style="margin-top:14px">Longest absences this season</h5>
       <div class="av-spells">${a.spells.map((s) => `<div class="av-spell">
         <b>${s.matches} match${s.matches === 1 ? '' : 'es'}</b>
         <span>${when(s.from)} – ${when(s.to)}</span></div>`).join('')}</div>`
    : '';

  // the measured relationship, stated as a rate rather than pinned on this player
  const r = a.risk || [];
  const risk = r.length === 3
    ? `Across the rated panel, a player who missed nothing in a season lost 10+ consecutive
       matches the next one <b>${Math.round(r[0].rate * 100)}%</b> of the time; after missing 5–9 it
       was ${Math.round(r[1].rate * 100)}%, and after 10+ it was <b>${Math.round(r[2].rate * 100)}%</b>.
       Past absence does predict future absence — but weakly enough that we publish the rates rather
       than an injury risk score for an individual, which the data cannot support. `
    : '';

  document.getElementById('availBody').innerHTML = `
    <div class="av-head">
      <div class="av-big"><b>${a.pct.toFixed(0)}%</b><span>of ${a.window_matches} league matches</span></div>
      <div class="av-facts">
        <div><label>Played</label><b>${a.played}</b></div>
        <div><label>Missed</label><b>${a.missed}</b></div>
        <div><label>Longest absence</label><b>${a.longest_spell}</b></div>
      </div>
    </div>
    <div class="av-chart">${bars}</div>
    ${spells}
    <div class="muted" style="font-size:11.5px;margin-top:12px">${risk}Counted from his first
      appearance for the club onward, so a mid-season signing is not marked absent for a season he
      spent elsewhere. League matches only, and a suspension is indistinguishable from an injury
      here — so these are absences, not diagnoses.</div>`;
}

// Career Trajectory — the one forward-looking model on the profile. Draws the
// projection with its error bar, the drivers behind it, and the player's own
// rating history against the measured aging curve for his position.
function renderTrajectory(t) {
  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(1);

  const drivers = (t.drivers || []).map((d) => {
    const pos = d.impact >= 0;
    const mag = Math.min(100, Math.abs(d.impact) / 4 * 100);
    return `<div class="val-drv">
      <span class="val-dl">${esc(d.label)}</span>
      <span class="val-dbar"><i class="${pos ? 'good' : 'bad'}" style="width:${mag}%"></i></span>
      <b class="${pos ? 'good' : 'bad'}">${sign(d.impact)}</b>
    </div>`;
  }).join('');

  // history (solid) + the projected point (hollow, with its error bar)
  const hist = t.history || [];
  const chart = (() => {
    if (hist.length < 2) return '';
    const pts = hist.map((h) => ({ x: h.label, y: h.rating }));
    pts.push({ x: t.target_label, y: t.projected, proj: true });
    // no padding inside the plot: lo/hi already carry it, so the HTML labels
    // outside line up exactly with the top and bottom of the drawn area
    const W = 460, H = 120;
    const ys = pts.map((p) => p.y).concat([t.hi, t.lo]);
    const lo = Math.max(0, Math.min(...ys) - 4), hi = Math.min(100, Math.max(...ys) + 4);
    const sx = (i) => i * W / Math.max(1, pts.length - 1);
    const sy = (v) => H - (v - lo) / Math.max(1, hi - lo) * H;
    const solid = pts.filter((p) => !p.proj);
    const line = solid.map((p, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
    const last = solid.length - 1, pi = pts.length - 1;
    const dash = `M${sx(last).toFixed(1)},${sy(solid[last].y).toFixed(1)}L${sx(pi).toFixed(1)},${sy(t.projected).toFixed(1)}`;
    const dots = solid.map((p, i) =>
      `<circle cx="${sx(i).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="3" class="tj-dot"/>`).join('');
    const band = `<line x1="${sx(pi).toFixed(1)}" y1="${sy(t.hi).toFixed(1)}"
                        x2="${sx(pi).toFixed(1)}" y2="${sy(t.lo).toFixed(1)}" class="tj-band"/>`;
    // labels live outside the SVG: the plot is stretched to the card width
    // (preserveAspectRatio="none"), which would distort any text inside it
    return `<div class="tj-plot">
      <svg class="tj-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
           aria-label="Atlastra rating by season, with next season projected">
        <path d="${line}" class="tj-line"/>
        <path d="${dash}" class="tj-line proj"/>
        ${dots}${band}
        <circle cx="${sx(pi).toFixed(1)}" cy="${sy(t.projected).toFixed(1)}" r="4" class="tj-dot proj"/>
      </svg>
      <div class="tj-yax"><span>${Math.round(hi)}</span><span>${Math.round(lo)}</span></div>
      <div class="tj-xax"><span>${esc(hist[0].label)}</span><span>${esc(t.target_label)}</span></div>
    </div>`;
  })();

  // the measured curve for his position, at his own age
  const GROUPS = {
    CB: 'centre-backs', FB: 'full-backs', DM: 'defensive midfielders',
    CM: 'central midfielders', AM: 'attacking midfielders', W: 'wingers', ST: 'strikers',
  };
  const curveNote = (() => {
    const c = (t.curve || []).find((p) => t.age != null && p.age === Math.round(t.age));
    if (!c) return '';
    const who = GROUPS[t.position_group] || 'players';
    const dir = c.delta >= 0 ? 'still gaining' : 'losing';
    return `At ${Math.round(t.age)}, ${who} in the panel are on average
            <b>${dir} ${Math.abs(c.delta).toFixed(1)} rating points</b> a season (${c.n} player-seasons).`;
  })();

  const m = t.model;
  const note = m
    ? `Gradient-boosting model, trained on transitions through ${m.n_train} player-seasons and scored
       blind on ${m.test_seasons} (${m.n_test} projections): average error <b>${m.mae.toFixed(1)}</b>
       rating points against <b>${m.base_mae.toFixed(1)}</b> for assuming no change at all, and the
       direction right ${Math.round(m.direction_acc * 100)}% of the time on players who actually moved.`
    : '';
  // An interval is a claim about how often it is right, so state how often it was.
  const bandNote = m && m.coverage
    ? `The ${m.interval_pct}% range is not a flat ± applied to everyone — it is fitted, so it widens
       where the model knows less and it leans the way the risk does (a high-rated player has more
       room below him than above). On the held-out seasons it contained the eventual rating
       <b>${Math.round(m.coverage * 100)}%</b> of the time. `
    : '';

  const risk = t.p_present < 0.5
    ? `<span class="tj-risk bad">${Math.round((1 - t.p_present) * 100)}% likely to drop out of the top-5</span>`
    : `<span class="tj-risk">${Math.round(t.p_present * 100)}% likely to still be a top-5 regular</span>`;

  document.getElementById('trajBody').innerHTML = `
    <div class="val-head"><span class="fin-badge ${t.verdict_class}">${esc(t.verdict)}</span>
      <span class="val-sum">${esc(t.blurb)} in ${esc(t.target_label)}</span></div>
    <div class="tj-nums">
      <div class="tj-num"><label>Now</label><b>${t.rating_now}</b></div>
      <div class="tj-arrow ${t.delta >= 0 ? 'up' : 'down'}">${t.delta >= 0 ? '▲' : '▼'} ${sign(t.delta)}</div>
      <div class="tj-num proj"><label>${esc(t.target_label)}</label>
        <b>${t.projected.toFixed(1)}</b>
        <span class="tj-pm">${Math.round(t.lo)}–${Math.round(t.hi)}</span></div>
      <div class="tj-avail">${risk}</div>
    </div>
    ${chart}
    <h5 class="val-dh">What moves the projection</h5>
    <div class="val-drivers">${drivers || '<span class="muted">—</span>'}</div>
    <div class="muted" style="font-size:11.5px;margin-top:10px">${curveNote} ${bandNote}${note}
      It projects the rating, not transfers or injuries — a move to a stronger side, or a bad one,
      is exactly the kind of thing it cannot see.</div>`;
}

// ---- boot ----
const params = new URLSearchParams(location.search);
let current = params.get('name') || 'Pedri';

// Back button: if we arrived from a match (the match page tags its profile links
// with from=match&eid=…), go straight back to that match; otherwise fall back to
// the browser's history when there is somewhere to return to.
(function () {
  const back = document.getElementById('backBtn');
  if (!back) return;
  if (params.get('from') === 'match' && params.get('eid')) {
    back.textContent = '← Back to match';
    back.href = '/match.html?id=' + encodeURIComponent(params.get('eid'));
    back.style.display = '';
  } else if (history.length > 1) {
    back.textContent = '← Back';
    back.href = '#';
    back.addEventListener('click', (e) => { e.preventDefault(); history.back(); });
    back.style.display = '';
  }
})();
const careerStatVal = () => document.getElementById('careerStat').value;
load(current, 'xa', params.get('season'));      // ?season=2324 deep-links a season
document.getElementById('careerStat').onchange = (e) => load(current, e.target.value, curSeason);
document.getElementById('seasonSel').onchange = (e) => load(current, careerStatVal(), e.target.value);
// topbar search = the global player/team/match dropdown (same as Home), not an
// Enter-to-reload-this-profile box, so search is consistent across the app.
attachSearchDropdown(document.getElementById('searchBox'));



// The cards that depend only on WHICH player this is — not on the season, and not on
// the career stat the selector is showing, so they run ONCE per page. Changing season
// used to refetch every one of them to re-render an identical card.
//
// They start when /api/player comes back, not alongside it, and that is deliberate.
// They don't need it — each resolves the name itself — and starting them early did cut
// the total on a laptop. But the server has two cores: eight concurrent queries all
// finish later, and the one they delay is the only one the page cannot render without.
// Measured on the deployed host, the profile landed at 2226ms with them alongside it
// and ~500ms with them behind it, for the same total. First paint is what a reader
// waits for; a card arriving a second later is a card they haven't scrolled to.
function sideCards(name) {
  if (_sideCardsDone) return;
  _sideCardsDone = true;
  const p = { name };            // these blocks only ever read p.name
  // Big Game Index card (only if match-log data is available for this player)
  api('/api/big_game?name=' + encodeURIComponent(p.name)).then((b) => {
    if (!b || !b.available) return;
    const badge = b.badge === 'Big-Game Player' ? '<span class="bgp-badge big">⭐ Big-Game Player</span>'
      : b.badge === 'Flat-Track Bully' ? '<span class="bgp-badge bully">🛑 Flat-Track Bully</span>'
        : '<span class="bgp-badge neutral">Consistent across opposition</span>';
    const MAX = Math.max(0.4, b.big.ga90, b.weak.ga90);
    const bar = (v, cls) => `<div class="bgp-bar"><i class="${cls}" style="width:${Math.min(100, v / MAX * 100)}%"></i></div>`;
    document.getElementById('bigGame').innerHTML = `<div class="bgp">${badge}
      <div class="bgp-split">
        <div class="bgp-row"><label>vs Top-half · ${b.big.apps} apps <b>${b.big.ga90.toFixed(2)} G+A/90</b></label>${bar(b.big.ga90, 'big')}</div>
        <div class="bgp-row"><label>vs Bottom-half · ${b.weak.apps} apps <b>${b.weak.ga90.toFixed(2)} G+A/90</b></label>${bar(b.weak.ga90, 'weak')}</div>
      </div></div>`;
    document.getElementById('bigGameCard').style.display = '';
  }).catch(() => {});

  // Finishing vs Expected — Goals − xG over/under-performance (Understat match log).
  // Shown only for players with enough shots this season; hidden for the rest.
  api('/api/finishing?name=' + encodeURIComponent(p.name)).then((f) => {
    if (!f || !f.available) return;
    renderFinishing(f);
    document.getElementById('finishingCard').style.display = '';
  }).catch(() => {});

  // Fair Value model — over/undervalued vs the model estimate, with value-drivers.
  // Only ~488 players have a Transfermarkt value to model against, so hide otherwise.
  api('/api/value_model?name=' + encodeURIComponent(p.name)).then((v) => {
    if (!v || !v.available) return;
    renderValue(v);
    document.getElementById('valueCard').style.display = '';
  }).catch(() => {});

  // Availability — how much of his club's football he was actually there for,
  // derived from the match log rather than scraped from an injury feed.
  api('/api/availability?name=' + encodeURIComponent(p.name)).then((a) => {
    if (!a || !a.available) return;
    renderAvailability(a);
    document.getElementById('availCard').style.display = '';
  }).catch(() => {});

  // Career Trajectory — where the model expects his rating to go next season.
  // Only players who cleared this season's rating minutes bar are projected.
  api('/api/trajectory?name=' + encodeURIComponent(p.name)).then((t) => {
    if (!t || !t.available) return;
    renderTrajectory(t);
    document.getElementById('trajCard').style.display = '';
  }).catch(() => {});

  // Highlights & skills video (searched on YouTube; shown only if found)
  api('/api/player_video?name=' + encodeURIComponent(p.name)).then((v) => {
    if (!v || !v.available || !v.thumbnail) return;
    const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
    const link = document.getElementById('hlVid');
    link.href = v.url;
    link.innerHTML = `<div class="pv-thumb"><img src="${esc(v.thumbnail)}" alt="" loading="lazy"><span class="pv-play">▶</span></div>
      <div class="pv-meta"><b>${esc(v.title || (nameLabel() + ' — skills & goals'))}</b><span>Watch on YouTube ↗</span></div>`;
    document.getElementById('hlVidSrc').textContent = 'YouTube';
    document.getElementById('hlVidCard').style.display = '';
  }).catch(() => {});

  // Signature Skills — Gemini watches the player's reel and ranks their moves.
  // First view for a player runs the analysis (~15-30s); cached forever after.
  const sigCard = document.getElementById('sigSkillCard'), sigBox = document.getElementById('sigSkills');
  sigCard.style.display = '';
  sigBox.innerHTML = '<div class="sig-load">✨ Analysing highlight reel…</div>';
  api('/api/signature_skills?name=' + encodeURIComponent(p.name)).then((s) => {
    if (!s || !s.available || !(s.skills || []).length) { sigCard.style.display = 'none'; return; }
    const esc = (t) => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
    sigBox.innerHTML = s.skills.map((x, i) => {
      // Prefer a clip of THIS player doing the move (cut from their own reel);
      // fall back to the generic example clip for the move if we don't have one.
      // Whole feature is gated by SKILL_CLIPS_ENABLED (api.js) — off = data kept, ▶ hidden.
      const clipsOn = (typeof SKILL_CLIPS_ENABLED === 'undefined') ? true : SKILL_CLIPS_ENABLED;
      const own = clipsOn ? x.clip : null;
      const generic = clipsOn && !own && typeof skillClipId === 'function' && skillClipId(x.skill);
      const playable = own || generic;
      const attr = own
        ? ` data-clipurl="${esc(own)}" data-cliplabel="${esc(nameLabel() + ' — ' + x.skill)}"`
        : (generic ? ` data-skillclip="${esc(x.skill)}"` : '');
      return `<div class="sig-row${playable ? ' has-clip' : ''}"${attr}>
        <span class="sig-rk">${i + 1}</span>
        <div class="sig-txt"><b>${esc(x.skill)}</b><span>${esc(x.note)}</span></div>
        ${playable ? `<span class="sig-play" title="${own ? 'Watch him do it' : 'See an example'}">▶</span>` : ''}</div>`;
    }).join('');
    document.getElementById('sigSkillSrc').textContent = 'AI · tap a move';
  }).catch(() => { sigCard.style.display = 'none'; });
}
