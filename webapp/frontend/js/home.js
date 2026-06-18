renderSidebar('Home');
attachSearchDropdown(document.getElementById('searchBox'));

// ---- placeholders (no live/predictor/ToTS data in the warehouse) ----
const LIVE = [
  ['76', 'Arsenal', '2', '1', 'Chelsea', 'LIVE'], ['54', 'Barcelona', '1', '0', 'Sevilla', 'LIVE'],
  ['31', 'Inter', '0', '0', 'AC Milan', 'LIVE'], ['HT', 'Bayern Munich', '1', '1', 'Leverkusen', 'HT'],
  ['23', 'PSG', '0', '0', 'Marseille', 'LIVE'],
];
document.getElementById('liveMatches').innerHTML = LIVE.map(([m, h, hg, ag, a, st]) => `
  <div class="match"><span class="min">${m}'</span>
    <span class="tm"><span class="crest"></span>${h}</span>
    <span class="sc">${hg} - ${ag}</span>
    <span class="tm away">${a}<span class="crest"></span></span>
    <span class="live">${st}</span></div>`).join('');

const BALLON = [['Kylian Mbappé', 34], ['Lamine Yamal', 29], ['Jude Bellingham', 16], ['Pedri', 8], ['Florian Wirtz', 7]];
document.getElementById('ballon').innerHTML = BALLON.map(([n, p], i) => `
  <div class="prow" onclick="location.href='${pHref(n)}'"><span class="rk">${i + 1}</span><span class="pic"></span>
    <span class="nm">${n}</span><span class="bo-bar"><i style="width:${p * 2.5}%"></i></span><b>${p}%</b></div>`).join('');

const TOTS = [['Donnarumma'], ['Mendes', 'Saliba', 'Rüdiger', 'Frimpong'], ['Pedri', 'Bellingham', 'Wirtz'], ['Raphinha', 'Mbappé', 'Salah']];
document.getElementById('tof').innerHTML = TOTS.map(line =>
  `<div class="pline">${line.map(p => `<span class="pp"><span class="dot"></span>${p}</span>`).join('')}</div>`).join('');

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
      <div class="lab">${lab}</div><div class="pic"></div>
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
