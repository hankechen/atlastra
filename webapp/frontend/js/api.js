// shared helpers + sidebar for the Atlastra UI
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const initials = (n) => n.replace(/[^A-Za-z .'-]/g, '').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
const eurM = (v) => v == null ? '—' : '€' + (v / 1e6).toFixed(0) + 'M';

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
function flagEmoji(ccode) {
  const special = { ENG: '🏴\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}',
    SCO: '🏴\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}',
    WAL: '🏴\u{E0067}\u{E0062}\u{E0077}\u{E006C}\u{E0073}\u{E007F}' };
  if (special[ccode]) return special[ccode];
  const iso = FIFA2ISO[ccode];
  return iso ? iso.replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt())) : '';
}

const NAV = [
  ['Home', '🏠', '/index.html'], ['Players', '👤', '/players.html'], ['Teams', '🛡️', '#'],
  ['Compare', '⇄', '#'], ['Rankings', '📊', '#'], ['Awards', '🏆', '#'],
  ['Live Matches', '◉', '#'], ['Search', '⌕', '#'],
];
const LEAGUES = [['Premier League', '#e0322f'], ['La Liga', '#e8a33d'], ['Serie A', '#2d8fd5'],
                 ['Bundesliga', '#d12a2a'], ['Ligue 1', '#e8c33d']];

function renderSidebar(active) {
  const nav = NAV.map(([n, ic, href]) =>
    `<a href="${href}" class="${n === active ? 'active' : ''}"><span class="ic">${ic}</span>${n}</a>`).join('');
  const leagues = LEAGUES.map(([n, c]) =>
    `<div class="league-row"><span class="dot" style="background:${c}"></span>${n}</div>`).join('');
  document.getElementById('sidebar').innerHTML = `
    <div class="brand"><svg class="logo" viewBox="0 0 32 32"><path d="M16 3 L29 28 H3 Z" fill="none" stroke="url(#g)" stroke-width="3"/><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#5570f0"/><stop offset="1" stop-color="#7d5cf5"/></linearGradient></defs></svg>ATLASTRA</div>
    <nav class="nav">${nav}</nav>
    <div class="nav-label">My Shortcuts</div>
    <nav class="nav"><a href="#"><span class="ic">👥</span>My Players</a><a href="#"><span class="ic">⇄</span>My Comparisons</a>
      <a href="#"><span class="ic">🔖</span>Watchlist</a></nav>
    <div class="nav-label">Leagues</div>${leagues}
    <div class="pro"><h4>ATLASTRA PRO</h4><p>Unlock advanced stats, custom rankings and exclusive insights.</p>
      <button class="btn btn-primary btn-block">Upgrade Now</button></div>`;
}

// player list row used by rankings / trending
function playerRow(p, { chip, arrow, value } = {}) {
  const end = chip ? `<span class="ratingchip">${p.rating}</span>`
    : arrow ? `<span class="up">▲</span><span>${value ?? p.rating}</span>`
    : `<span>${value ?? p.rating}</span>`;
  return `<div class="prow"><span class="rk">${p.rank}</span>
    <span class="pic" title="${p.player}"></span>
    <span><div class="nm">${p.player}</div><div class="sub">${p.team} · ${p.position}</div></span>
    <span class="end">${end}</span></div>`;
}
