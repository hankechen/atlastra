renderSidebar('Home');
attachSearchDropdown(document.getElementById('searchBox'));

// ---- big-games widget ----
// Home page shows only the marquee competitions — the World Cup (& other majors),
// the top-5 leagues, or the actual Champions League (NOT UCL qualification). Live
// big games come first, followed by upcoming big fixtures; if none are in play, it
// shows the latest big-game results alongside what's next. Re-polls every 30s.
const isBigGame = (m) => {
  const g = m.group || '';
  if (g === 'International' || g === 'Top 5 Leagues') return true;   // WC/Euro/Copa + top-5
  if (g === 'Champions League') return !/qualif/i.test(m.round || '');  // real UCL, not qual
  return false;
};
async function loadLiveWidget() {
  const box = document.getElementById('liveMatches');
  try {
    const d = await api('/api/live?recent=40&upcoming=20');
    const live = d.live.filter(isBigGame);
    const upcoming = d.upcoming.filter(isBigGame);
    const recent = d.recent.filter(isBigGame);
    // in play -> live + upcoming; nothing on -> latest results + upcoming fixtures
    const feed = live.length
      ? [...live, ...upcoming].slice(0, 6)
      : [...recent.slice(0, 3), ...upcoming.slice(0, 3)].slice(0, 6);
    box.innerHTML = feed.length ? feed.map(matchRow).join('')
      : '<div class="placeholder-note">No big games right now.</div>';
    const note = box.parentElement.querySelector('.placeholder-note');
    if (note) note.textContent = !feed.length ? ''
      : live.length ? `${live.length} big match${live.length > 1 ? 'es' : ''} live now`
        : 'Latest results & upcoming big games';
  } catch {
    box.innerHTML = '<div class="placeholder-note">Live feed unavailable.</div>';
  }
}
loadLiveWidget();
setInterval(() => { if (!document.hidden) loadLiveWidget(); }, 30000);

// World Cup XI — best XI by average match rating among players at the World Cup (4-3-3)
(async () => {
  const xi = await api('/api/team_of_week');
  document.getElementById('totsNote').textContent =
    `Best XI by average match rating · ${xi.edition || ''} · ${xi.formation}`;
  const surname = (n) => n.split(' ').slice(-1)[0];
  const dot = (p) => p.photo ? avatarHTML(p.photo, p.player)
    : (flagISO2(p.cc) ? `<span class="flagdot">${flagISO2(p.cc)}</span>` : avatarHTML(null, p.player));
  document.getElementById('tof').innerHTML = xi.lines.map(line =>
    `<div class="pline">${line.players.map(p => `
      <span class="pp" onclick="location.href='${pHref(p.player)}'" title="${p.player} · ${p.team}${p.club ? ' (' + p.club + ')' : ''} · ${(+p.rating).toFixed(1)} avg rating">
        <span class="dot">${dot(p)}</span>
        <span class="pp-rt" style="background:${ratingColor(p.rating)}">${(+p.rating).toFixed(1)}</span>
        <span class="pp-n">${surname(p.player)}</span></span>`).join('')}</div>`).join('');
})();

// Guest-only "Discover" card — a random spotlight/head-to-head/result so the home
// page feels alive before signing in. Hidden for signed-in users.
(async () => {
  if (Auth.user == null) { try { await Auth.me(); } catch { /* guest */ } }
  if (Auth.user) return;                       // signed in -> skip
  let c; try { c = await api('/api/discover'); } catch { return; }
  if (!c || !c.type) return;
  const box = document.getElementById('discover');
  if (!box) return;
  const head = (t, link) => `<div class="card-h"><h3>${t}</h3>${link || ''}</div>`;
  const chip = (r) => `<span class="ratingchip">${r}</span>`;
  let body = '';
  if (c.type === 'player') {
    body = head('✨ ' + c.kicker, `<a class="see" href="${pHref(c.player)}">View profile</a>`) +
      `<a class="disc-player" href="${pHref(c.player)}">
        <span class="pic">${avatarHTML(c.photo, c.player)}</span>
        <span class="disc-main"><div class="nm">${c.player}</div>
          <div class="sub">${crestHTML(c.team_logo, 'crest-sm')}${c.team || ''} · ${c.position || ''}</div>
          ${c.line ? `<div class="disc-line">${c.line}</div>` : ''}</span>
        <span class="disc-end">${chip(c.rating)}<span class="disc-tag">${c.blurb || ''}</span></span></a>`;
  } else if (c.type === 'compare') {
    const side = (p) => `<a class="disc-vs-p" href="${pHref(p.player)}">
        <span class="pic">${avatarHTML(p.photo, p.player)}</span>
        <div class="nm">${p.player}</div>${chip(p.rating)}
        <div class="sub">${p.line || (p.position || '')}</div></a>`;
    const cmp = `/compare.html?name=${encodeURIComponent(c.a.player)}&name=${encodeURIComponent(c.b.player)}`;
    body = head('⚔ ' + c.kicker, `<a class="see" href="${cmp}">Compare →</a>`) +
      `<div class="disc-vs">${side(c.a)}<span class="disc-vs-x">vs</span>${side(c.b)}</div>`;
  } else if (c.type === 'stat') {
    body = head('📊 ' + c.kicker, `<a class="see" href="${pHref(c.player)}">View profile</a>`) +
      `<a class="disc-player" href="${pHref(c.player)}">
        <span class="pic">${avatarHTML(c.photo, c.player)}</span>
        <span class="disc-main"><div class="disc-tag">${c.label}</div>
          <div class="nm">${c.player}</div>
          <div class="sub">${crestHTML(c.team_logo, 'crest-sm')}${c.team || ''}</div></span>
        <span class="disc-end"><b class="disc-big">${c.value}</b><span class="disc-unit">${c.unit}</span></span></a>`;
  } else if (c.type === 'match') {
    const pens = (c.home_pens != null) ? `<div class="disc-pens">pens ${c.home_pens}-${c.away_pens}</div>` : '';
    const href = c.event_id != null ? `/match.html?id=${c.event_id}` : '';
    body = head('⚽ ' + c.kicker, `<a class="see" href="/live.html">More results</a>`) +
      `<a class="disc-match"${href ? ` href="${href}"` : ''}>
        <span class="disc-m-t">${crestHTML(c.home_logo, 'crest-md') || '🛡️'}<span class="nm">${c.home}</span></span>
        <span class="disc-m-s">${c.home_score} - ${c.away_score}${pens}<div class="disc-comp">${c.competition || ''}</div></span>
        <span class="disc-m-t away">${crestHTML(c.away_logo, 'crest-md') || '🛡️'}<span class="nm">${c.away}</span></span></a>`;
  }
  box.innerHTML = body;
  box.style.display = 'block';
})();

// ---- real data ----
(async () => {
  const ov = await api('/api/overview');
  document.getElementById('heroStats').innerHTML = [
    [ov.leagues, 'Leagues'], [ov.teams + '+', 'Teams'], [(ov.players / 1000 | 0) + 'K+', 'Players'],
    [(ov.matches / 1000 | 0) + 'K+', 'Matches'], [ov.stats_tracked + '+', 'Stats Tracked'],
  ].map(([b, s]) => `<div class="s"><b>${b}</b><span>${s}</span></div>`).join('');

  const ranks = await api('/api/rankings?limit=10');
  document.getElementById('top5').innerHTML = ranks.map(p => playerRow(p, { chip: true })).join('');

  // Games rail: quick links into the games hub (mirrors the sidebar Games group).
  const GAMES = [
    ['predict', 'Score Predictor', 'Call the scores, earn points', '/predict.html'],
    ['daily', 'Daily Challenge', "Today's puzzle · streak", '/daily.html'],
    ['guess', 'Guess the Rating', 'Read the line, guess the rating', '/guess.html'],
    ['higherlower', 'Higher or Lower', 'Pick the bigger stat', '/higherlower.html'],
    ['mystery', 'Guess the Player', 'Unmask the mystery player', '/mystery.html'],
    ['draft', 'Draft Battle', 'Draft an XI, beat the board', '/draft.html'],
  ];
  document.getElementById('gamesList').innerHTML = GAMES.map(([ic, name, sub, href]) =>
    `<a class="grow" href="${href}"><span class="gic">${svg(ic)}</span>
      <span class="gtx"><b>${name}</b><span>${sub}</span></span>
      <span class="gchev">${svg('chevR')}</span></a>`).join('');

  const spot = await api('/api/spotlight');
  const cards = [
    ['Top Goalscorer', spot.top_scorer, 'Goals'], ['Top Assists', spot.top_assists, 'Assists'],
    ['Most xG', spot.most_xg, 'xG'], ['Most Chances', spot.most_chances, 'Created'],
    ['Most Dribbles', spot.most_dribbles, 'Dribbles'],
  ];
  document.getElementById('spotlight').innerHTML = cards.map(([lab, s, unit]) => `
    <div class="s"${s ? ` onclick="location.href='${pHref(s.player)}'" style="cursor:pointer"` : ''}>
      <div class="lab">${lab}</div><div class="pic">${s ? avatarHTML(s.photo, s.player) : ''}</div>
      <div class="nm">${s ? s.player : '—'}</div><div class="v">${s ? s.value + ' ' + unit : ''}</div></div>`).join('');

  // standings with league tabs
  const TABS = [['Premier League', 'ENG-Premier League'], ['La Liga', 'ESP-La Liga'],
                ['Serie A', 'ITA-Serie A'], ['Bundesliga', 'GER-Bundesliga'], ['Ligue 1', 'FRA-Ligue 1']];
  const tabsEl = document.getElementById('leagueTabs');
  async function loadStandings(key) {
    const link = document.getElementById('seeTable');
    if (link) link.href = '/teams.html?league=' + encodeURIComponent(key);
    const rows = await api('/api/standings?league=' + encodeURIComponent(key));
    document.getElementById('standings').innerHTML = rows.map(r => `<tr class="ltbl-row"
      onclick="location.href='${tHref(r.team)}'">
      <td>${r.pos}</td><td>${r.team}</td><td>${r.p}</td><td>${r.w}</td><td>${r.d}</td><td>${r.l}</td>
      <td>${r.gd}</td><td><b>${r.pts}</b></td>
      <td>${r.form.map(f => `<span class="form-pill form-${f}">${f}</span>`).join('')}</td></tr>`).join('');
  }
  tabsEl.innerHTML = TABS.map(([n, k], i) => `<span class="tab ${i ? '' : 'active'}" data-k="${k}">${n}</span>`).join('');
  tabsEl.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    tabsEl.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active'); loadStandings(t.dataset.k);
  });
  loadStandings('ENG-Premier League');
})();

// Top Highlights strip — the day's best clips (falls back to the week if today's
// slate is empty). Card thumbnails open the clip; the header links to the full page.
(async () => {
  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  let r;
  try { r = await api('/api/highlights?period=day'); } catch { return; }
  let clips = (r && r.clips) || [];
  if (clips.length < 3) {                       // thin day → widen to the week
    try { const w = await api('/api/highlights?period=week'); if (w && w.clips && w.clips.length > clips.length) clips = w.clips; } catch {}
  }
  if (!clips.length) return;
  const strip = document.getElementById('hlHome');
  strip.innerHTML = clips.slice(0, 4).map(c => {
    const thumb = c.thumbnail ? `<img src="${esc(c.thumbnail)}" alt="" loading="lazy">` : '<div class="hs-noimg"></div>';
    const score = (c.home_score ?? '') + '–' + (c.away_score ?? '');
    return `<a class="hs-card" href="${esc(c.url)}" target="_blank" rel="noopener">
        <div class="hs-media">${thumb}<span class="hl-play">▶</span></div>
        <div class="hs-cap"><span class="hs-comp">${esc(c.competition)}</span>
          <span class="hs-mt">${esc(c.home)} <b>${score}</b> ${esc(c.away)}</span></div>
      </a>`;
  }).join('');
  document.getElementById('hlCard').style.display = '';
})();

// Signature Skills promo — showcase a few marquee players' AI-analysed moves.
// Only features players already in the analysed set, so every call is a cache hit
// (never triggers a slow fresh analysis on the home page).
(async () => {
  const FEATURED = ['Lamine Yamal', 'Vinícius Júnior', 'Kylian Mbappe-Lottin',
    'Jude Bellingham', 'Erling Haaland', 'Pedri', 'Ousmane Dembélé', 'Bruno Fernandes'];
  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  let cached;
  try { cached = new Set((await api('/api/highlight_players')).players); } catch { return; }
  const pick = FEATURED.filter(n => cached.has(n)).sort(() => Math.random() - 0.5).slice(0, 3);
  if (!pick.length) return;
  const results = await Promise.all(pick.map(n =>
    api('/api/signature_skills?name=' + encodeURIComponent(n)).catch(() => null)));
  const ytId = (url) => { const m = /youtu\.be\/([\w-]{11})/.exec(url || ''); return m ? m[1] : null; };
  const cards = [];
  for (const r of results) {
    if (!r || !r.available || !(r.skills || []).length) continue;
    const id = ytId(r.video);
    const thumb = id ? `<img src="https://i.ytimg.com/vi/${id}/hqdefault.jpg" alt="" loading="lazy">` : '';
    const moves = r.skills.slice(0, 3).map(s => {
      const clip = typeof skillClipId === 'function' && skillClipId(s.skill);
      return `<span class="sig-chip${clip ? ' has-clip' : ''}"${clip ? ` data-skillclip="${esc(s.skill)}"` : ''}>${esc(s.skill)}</span>`;
    }).join('');
    cards.push(`<a class="sig-p" href="/player.html?name=${encodeURIComponent(r.player)}">
      <div class="sig-p-thumb">${thumb}<span class="hl-play">▶</span></div>
      <div class="sig-p-body"><b>${esc(r.player)}</b><div class="sig-p-moves">${moves}</div></div></a>`);
  }
  if (!cards.length) return;
  document.getElementById('sigPromoGrid').innerHTML = cards.join('');
  document.getElementById('sigPromo').style.display = '';
})();
