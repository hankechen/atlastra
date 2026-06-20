// shared helpers + sidebar for the Atlastra UI
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const initials = (n) => n.replace(/[^A-Za-z .'-]/g, '').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
const eurM = (v) => v == null ? '—' : '€' + (v / 1e6).toFixed(0) + 'M';
// Safe nav hrefs — encodeURIComponent at build time so the URL has no quotes/
// apostrophes that would break an onclick="" attribute (e.g. "Matt O'Riley").
const pHref = (n) => `/player.html?name=${encodeURIComponent(n)}`;
const tHref = (n) => `/team.html?name=${encodeURIComponent(n)}`;
// FotMob-style colour for an average match rating (~6–9 scale)
const ratingColor = (r) => r >= 7.5 ? '#34c46a' : r >= 7.0 ? '#9acd32' : r >= 6.7 ? '#e8b04b' : '#e5484d';

// Player avatar: initials sit behind; the FotMob photo overlays and removes itself
// on error (missing image) so we gracefully fall back. Container needs the avatar CSS.
function avatarHTML(url, name) {
  return `<span class="ini">${initials(name || '')}</span>` +
    (url ? `<img src="${url}" alt="" loading="lazy" onerror="this.remove()">` : '');
}
// Team crest <img> (hidden on error); cls lets callers size it inline vs as a badge.
function crestHTML(url, cls = 'crest') {
  return url ? `<img class="${cls}" src="${url}" alt="" loading="lazy" onerror="this.remove()">` : '';
}

// Live global search dropdown on a topbar search input: players + teams as you
// type, click a result to navigate, Enter for the full /search.html page.
function attachSearchDropdown(input) {
  if (!input) return;
  const wrap = input.closest('.search');
  wrap.classList.add('has-dd');
  const dd = document.createElement('div');
  dd.className = 'search-dd';
  wrap.appendChild(dd);
  let timer;
  const hide = () => dd.classList.remove('open');
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  input.addEventListener('input', () => {
    const q = input.value.trim();
    clearTimeout(timer);
    if (!q) { hide(); return; }
    timer = setTimeout(async () => {
      let d;
      try { d = await api('/api/search?q=' + encodeURIComponent(q)); } catch { return; }
      const players = d.players.slice(0, 6), teams = d.teams.slice(0, 4);
      const row = (href, pic, nm, sub) =>
        `<a class="dd-row" href="${href}"><span class="pic">${pic}</span>
          <span class="ddx"><div class="nm">${esc(nm)}</div><div class="sub">${sub}</div></span></a>`;
      let html = '';
      if (players.length) html += '<div class="dd-h">Players</div>' + players.map(p =>
        row(`/player.html?name=${encodeURIComponent(p.player)}`, avatarHTML(p.photo, p.player),
            p.player, `${crestHTML(p.team_logo, 'crest-sm')}${esc(p.team || '')} · ${esc(p.position || '')}`)).join('');
      if (teams.length) html += '<div class="dd-h">Teams</div>' + teams.map(t =>
        row(`/team.html?name=${encodeURIComponent(t.team)}`,
            `<span class="crest-pic">${crestHTML(t.team_logo, 'crest-md') || '🛡️'}</span>`,
            t.team, esc(t.league))).join('');
      html = html ? html + `<a class="dd-all" href="/search.html?q=${encodeURIComponent(q)}">See all results for “${esc(q)}” →</a>`
        : '<div class="dd-empty">No results</div>';
      dd.innerHTML = html;
      dd.classList.add('open');
    }, 170);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && input.value.trim()) location.href = '/search.html?q=' + encodeURIComponent(input.value.trim());
    if (e.key === 'Escape') hide();
  });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) hide(); });
}

// FotMob 3-letter (FIFA) nationality code -> flag emoji.
const FIFA2ISO = {
  ESP: 'ES', FRA: 'FR', ITA: 'IT', POR: 'PT', NED: 'NL', BEL: 'BE', GER: 'DE',
  IRL: 'IE', BRA: 'BR', ARG: 'AR', URU: 'UY', COL: 'CO', CHI: 'CL', ECU: 'EC',
  PER: 'PE', PAR: 'PY', VEN: 'VE', MEX: 'MX', USA: 'US', CAN: 'CA', CRO: 'HR',
  SRB: 'RS', SVN: 'SI', SVK: 'SK', CZE: 'CZ', POL: 'PL', UKR: 'UA', SUI: 'CH',
  AUT: 'AT', DEN: 'DK', SWE: 'SE', NOR: 'NO', FIN: 'FI', ISL: 'IS', TUR: 'TR',
  GRE: 'GR', HUN: 'HU', ROU: 'RO', BUL: 'BG', ALB: 'AL', KOS: 'XK', BIH: 'BA',
  MKD: 'MK', MNE: 'ME', SEN: 'SN', CIV: 'CI', GHA: 'GH', NGA: 'NG', CMR: 'CM',
  MAR: 'MA', ALG: 'DZ', TUN: 'TN', EGY: 'EG', MLI: 'ML', GUI: 'GN', COD: 'CD',
  GAB: 'GA', ANG: 'AO', RSA: 'ZA', JPN: 'JP', KOR: 'KR', AUS: 'AU', IRN: 'IR',
  KSA: 'SA', UZB: 'UZ', GEO: 'GE', ARM: 'AM', ISR: 'IL', JAM: 'JM', PAN: 'PA',
};
// ISO 3166 alpha-2 (e.g. 'BR') -> flag emoji via regional-indicator symbols.
// Used for national teams in the live/fixtures feed, which carry an alpha-2 code.
function flagISO2(cc) {
  if (!cc || cc.length !== 2) return '';
  const up = cc.toUpperCase();
  if (up === 'GB') return ''; // home nations come through as ENG/SCO/WAL separately
  return up.replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt()));
}
function flagEmoji(ccode) {
  const special = { ENG: '🏴\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}',
    SCO: '🏴\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}',
    WAL: '🏴\u{E0067}\u{E0062}\u{E0077}\u{E006C}\u{E0073}\u{E007F}' };
  if (special[ccode]) return special[ccode];
  const iso = FIFA2ISO[ccode];
  return iso ? iso.replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt())) : '';
}

// clean line icons (stroke = currentColor so they inherit nav colour)
const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5"/>',
  live: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
  players: '<circle cx="12" cy="8" r="3.6"/><path d="M5 19.5c0-3.6 3.1-5.4 7-5.4s7 1.8 7 5.4"/>',
  teams: '<path d="M12 3l7 2.5v6.2c0 4.2-3 7.1-7 8.8-4-1.7-7-4.6-7-8.8V5.5z"/>',
  compare: '<path d="M4 9h13M14 6l3 3-3 3M20 15H7M10 12l-3 3 3 3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5 16 16"/>',
  rankings: '<path d="M5 20v-5M10 20v-9M15 20v-6M20 20V7"/>',
  profile: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.4 18.5c1.1-2.2 3.1-3.3 5.6-3.3s4.5 1.1 5.6 3.3"/>',
  myplayers: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 19c0-3.1 2.7-4.7 6-4.7s6 1.6 6 4.7"/><path d="M16 6.2a3 3 0 010 5.6M21 19c0-2.4-1.6-3.9-3.7-4.4"/>',
  watchlist: '<path d="M6.5 4h11v17l-5.5-3.8L6.5 21z"/>',
  pro: '<path d="M6 4h12l3 5-9 11L3 9z"/><path d="M3 9h18M9 4l3 16 3-16"/>',
  chevR: '<path d="M9 6l6 6-6 6"/>',
  chevD: '<path d="M6 9l6 6 6-6"/>',
  archetypes: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
  scout: '<circle cx="11" cy="11" r="7"/><path d="M11 7v8M7 11h8M20.5 20.5 16 16"/>',
  styles: '<path d="M12 2.5 21 7.5v9L12 21.5 3 16.5v-9z"/><path d="M12 8l4 2.2v4.4L12 17l-4-2.4v-4.4z"/>',
};
const svg = (k) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg>`;

// [label, icon, href, trailing, children]  trailing: 'live' dot / 'chevR' arrow;
// children: nested [label, icon, href] tools shown indented under the parent.
const NAV_MAIN = [
  ['Home', 'home', '/index.html'], ['Live Matches', 'live', '/live.html', 'live'],
  ['Players', 'players', '/players.html', null, [
    ['Compare', 'compare', '/compare.html'],
    ['Scout', 'scout', '/scout.html'],
    ['Archetypes', 'archetypes', '/archetypes.html'],
  ]],
  ['Teams', 'teams', '/teams.html', null, [
    ['Team Styles', 'styles', '/styles.html'],
  ]],
  ['Search', 'search', '/search.html'],
];
const NAV_ANALYTICS = [['Rankings & Awards', 'rankings', '#', 'chevR']];

// in-page sub-tabs mirroring the sidebar groups. tab = [label, href, activeKey];
// activeKey matches the value a page passes to renderSidebar().
const TAB_GROUPS = [
  [['Directory', '/players.html', 'Players'], ['Compare', '/compare.html', 'Compare'],
   ['Scout', '/scout.html', 'Scout'], ['Archetypes', '/archetypes.html', 'Archetypes']],
  [['Standings', '/teams.html', 'Teams'], ['Team Styles', '/styles.html', 'Team Styles']],
];

// fills an existing #subtabs placeholder (opt-in per page) with its group's tabs
function renderSubtabs(active) {
  const el = document.getElementById('subtabs');
  if (!el) return;
  const group = TAB_GROUPS.find(g => g.some(([, , key]) => key === active));
  if (!group) return;
  el.innerHTML = group.map(([label, href, key]) =>
    `<a href="${href}" class="${key === active ? 'active' : ''}">${label}</a>`).join('');
}
const NAV_MINE = [
  ['My Profile', 'profile', '#'], ['My Players', 'myplayers', '#'],
  ['My Comparisons', 'compare', '#'], ['Watchlist', 'watchlist', '#'],
];
const LEAGUES = [['Premier League', '#e23a3a'], ['La Liga', '#e8a33d'], ['Serie A', '#2d8fd5'],
                 ['Bundesliga', '#d12a2a'], ['Ligue 1', '#edc23a']];

function renderSidebar(active) {
  const item = ([n, ic, href, trail, children]) => {
    const end = trail === 'live' ? '<span class="livedot"></span>'
      : trail === 'chevR' ? `<span class="chev">${svg('chevR')}</span>` : '';
    const childActive = children && children.some(([cn]) => cn === active);
    const parentCls = `navi ${n === active ? 'active' : ''} ${childActive ? 'group-active' : ''}`;
    let html = `<a href="${href}" class="${parentCls}">${svg(ic)}<span class="t">${n}</span>${end}</a>`;
    if (children) html += `<div class="subnav">${children.map(([cn, cic, chref]) =>
      `<a href="${chref}" class="navi sub ${cn === active ? 'active' : ''}">${svg(cic)}<span class="t">${cn}</span></a>`).join('')}</div>`;
    return html;
  };
  const section = (label, items, extra = '') =>
    `<div class="nav-label">${label}${extra}</div><nav class="nav">${items.map(item).join('')}</nav>`;
  const leagues = LEAGUES.map(([n, c]) =>
    `<a href="#" class="navi league"><span class="sq" style="background:${c}"></span><span class="t">${n}</span></a>`).join('');
  document.getElementById('sidebar').innerHTML = `
    <div class="brand"><svg class="logo" viewBox="0 0 32 32"><path d="M16 3 L29 28 H3 Z" fill="none" stroke="url(#g)" stroke-width="3" stroke-linejoin="round"/><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#5570f0"/><stop offset="1" stop-color="#7d5cf5"/></linearGradient></defs></svg>ATLASTRA</div>
    <div class="sb-scroll">
      ${section('Main', NAV_MAIN)}
      <div class="sb-div"></div>
      ${section('Analytics', NAV_ANALYTICS)}
      <div class="sb-div"></div>
      ${section('My Stuff', NAV_MINE)}
      <div class="sb-div"></div>
      <div class="nav-label">Leagues<span class="chev">${svg('chevD')}</span></div>
      <nav class="nav">${leagues}</nav>
    </div>
    <div class="pro"><span class="pro-ic">${svg('pro')}</span>
      <div class="pro-tx"><h4>ATLASTRA PRO</h4><p>Unlock advanced stats and features.</p></div>
      <span class="chev">${svg('chevR')}</span></div>
    <a href="#" class="sb-user"><span class="ava">JD<i class="on"></i></span>
      <span class="u-tx"><b>John Doe</b><span>View Profile</span></span><span class="chev">${svg('chevR')}</span></a>`;
  renderSubtabs(active);
}

// ---- live / fixtures match row (shared by home widget + /live.html) ----
// A team's badge: flag emoji for national teams (have a country code), else the
// club crest, else an empty placeholder square so the grid stays aligned.
// SofaScore gives UK home nations non-ISO codes (England 'EN', Scotland 'SX' --
// which is really Sint Maarten's code), so resolve those by name to the proper
// tag-sequence flag emoji (held in flagEmoji's special map) before any ISO lookup.
const HOME_NATION = { England: 'ENG', Scotland: 'SCO', Wales: 'WAL' };
function teamBadge(m, side) {
  const hn = HOME_NATION[m[side]];
  const f = hn ? flagEmoji(hn) : flagISO2(m[side + '_country']);
  if (f) return `<span class="nflag">${f}</span>`;
  return crestHTML(m[side + '_logo'], 'crest') || '<span class="crest"></span>';
}
// Left "minute" cell: running clock for live, 'HT' on the break, 'FT' for results,
// local kickoff time for upcoming.
function matchClock(m) {
  if (m.status === 'inprogress') return m.minute ? `${m.minute}'` : 'HT';
  if (m.status === 'finished') return 'FT';
  return new Date(m.kickoff_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
const matchDay = (ts) => new Date(ts * 1000)
  .toLocaleDateString([], { day: 'numeric', month: 'short' });
// One `.match` grid row. The trailing tag is LIVE (red) for in-play, else the date.
function matchRow(m) {
  const played = m.home_score != null;
  const score = played ? `${m.home_score} - ${m.away_score}` : 'vs';
  const clk = m.status === 'inprogress' ? 'min live-min' : 'min';
  const tag = m.status === 'inprogress'
    ? `<span class="live">● LIVE</span>` : `<span class="tag">${matchDay(m.kickoff_ts)}</span>`;
  const bold = (side) => m.winner === (side === 'home' ? 1 : 2) ? ' won' : '';
  const href = m.event_id != null ? ` onclick="location.href='/match.html?id=${m.event_id}'" style="cursor:pointer"` : '';
  return `<div class="match"${href}>
    <span class="${clk}">${matchClock(m)}</span>
    <span class="tm${bold('home')}">${teamBadge(m, 'home')}<span class="nm">${m.home}</span></span>
    <span class="sc">${score}</span>
    <span class="tm away${bold('away')}"><span class="nm">${m.away}</span>${teamBadge(m, 'away')}</span>
    ${tag}</div>`;
}

// player list row used by rankings / trending
function playerRow(p, { chip, arrow, value } = {}) {
  const end = chip ? `<span class="ratingchip">${p.rating}</span>`
    : arrow ? `<span class="up">▲</span><span>${value ?? p.rating}</span>`
    : `<span>${value ?? p.rating}</span>`;
  return `<div class="prow" onclick="location.href='${pHref(p.player)}'"><span class="rk">${p.rank}</span>
    <span class="pic" title="${p.player}">${avatarHTML(p.photo, p.player)}</span>
    <span><div class="nm">${p.player}</div><div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team} · ${p.position}</div></span>
    <span class="end">${end}</span></div>`;
}
