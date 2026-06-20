renderSidebar('Home');
attachSearchDropdown(document.getElementById('searchBox'));

// ---- live matches widget (real SofaScore feed) ----
// Show what's on: live games first, then the soonest upcoming, then latest results.
(async () => {
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
})();

const BALLON = [['Kylian Mbappé', 34], ['Lamine Yamal', 29], ['Jude Bellingham', 16], ['Pedri', 8], ['Florian Wirtz', 7]];
document.getElementById('ballon').innerHTML = BALLON.map(([n, p], i) => `
  <div class="prow" onclick="location.href='${pHref(n)}'"><span class="rk">${i + 1}</span><span class="pic"></span>
    <span class="nm">${n}</span><span class="bo-bar"><i style="width:${p * 2.5}%"></i></span><b>${p}%</b></div>`).join('');

// Team of the Season — real best XI by average match rating (4-3-3)
(async () => {
  const tots = await api('/api/team_of_season');
  document.getElementById('totsNote').textContent = `Best XI by average match rating · ${tots.formation}`;
  const surname = (n) => n.split(' ').slice(-1)[0];
  document.getElementById('tof').innerHTML = tots.lines.map(line =>
    `<div class="pline">${line.players.map(p => `
      <span class="pp" onclick="location.href='${pHref(p.player)}'" title="${p.player} · ${p.team} · ${p.position}">
        <span class="dot">${avatarHTML(p.photo, p.player)}</span>
        <span class="pp-rt" style="background:${ratingColor(p.avg_rating)}">${(+p.avg_rating).toFixed(1)}</span>
        <span class="pp-n">${surname(p.player)}</span></span>`).join('')}</div>`).join('');
})();

// ---- real data ----
(async () => {
  const ov = await api('/api/overview');
  document.getElementById('heroStats').innerHTML = [
    [ov.leagues, 'Leagues'], [ov.teams + '+', 'Teams'], [(ov.players / 1000 | 0) + 'K+', 'Players'],
    [(ov.matches / 1000 | 0) + 'K+', 'Matches'], [ov.stats_tracked + '+', 'Stats Tracked'],
  ].map(([b, s]) => `<div class="s"><b>${b}</b><span>${s}</span></div>`).join('');

  const ranks = await api('/api/rankings?limit=5');
  document.getElementById('top5').innerHTML = ranks.map(p => playerRow(p, { chip: true })).join('');
  document.getElementById('trending').innerHTML = ranks.map((p, i) =>
    playerRow({ ...p, rank: i + 1 }, { arrow: true, value: (9.9 - i * 0.2).toFixed(1) })).join('');

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
