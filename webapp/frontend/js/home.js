renderSidebar('Home');
attachSearchDropdown(document.getElementById('searchBox'));

// ---- live matches widget (real SofaScore feed) ----
// Show what's on: live games first, then the soonest upcoming, then latest results.
// Re-polls every 30s so in-play scores stay current without a page reload.
async function loadLiveWidget() {
  const box = document.getElementById('liveMatches');
  try {
    const d = await api('/api/live?recent=4&upcoming=5');
    const feed = [...d.live, ...d.upcoming, ...d.recent].slice(0, 5);
    box.innerHTML = feed.length ? feed.map(matchRow).join('')
      : '<div class="placeholder-note">No fixtures in the current window.</div>';
    const note = box.parentElement.querySelector('.placeholder-note');
    if (note) note.textContent = d.live.length
      ? `${d.live.length} match${d.live.length > 1 ? 'es' : ''} live now` : 'No live matches right now';
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
