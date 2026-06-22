// shared helpers + sidebar for the Atlastra UI
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}

// ---- Store: client-side personalization (no backend) -------------------------
// Follows, watchlist and saved comparisons persist in localStorage. Lists hold
// items keyed by a stable `id`; toggle() flips membership and returns the new
// state. An 'atla-store' event lets open views refresh live.
const ATLA_KEY = 'atlastra_store_v1';
const Store = {
  _read() { try { return JSON.parse(localStorage.getItem(ATLA_KEY)) || {}; } catch { return {}; } },
  _write(s) { try { localStorage.setItem(ATLA_KEY, JSON.stringify(s)); } catch { /* quota */ }
    window.dispatchEvent(new Event('atla-store')); },
  list(k) { return this._read()[k] || []; },
  has(k, id) { return this.list(k).some(x => x.id === id); },
  count(k) { return this.list(k).length; },
  toggle(k, item) {
    const s = this._read(), a = s[k] || [];
    const i = a.findIndex(x => x.id === item.id);
    let added;
    if (i >= 0) { a.splice(i, 1); added = false; } else { a.unshift({ ...item, ts: Date.now() }); added = true; }
    s[k] = a; this._write(s); return added;
  },
  remove(k, id) { const s = this._read(); s[k] = (s[k] || []).filter(x => x.id !== id); this._write(s); },
  // full user profile (identity). memberSince is stamped once, silently.
  profile() {
    const s = this._read(); s.profile = s.profile || {};
    if (s.profile.name == null && s.profileName) s.profile.name = s.profileName;   // migrate legacy
    if (!s.profile.memberSince) {
      s.profile.memberSince = Date.now();
      try { localStorage.setItem(ATLA_KEY, JSON.stringify(s)); } catch { /* quota */ }
    }
    return Object.assign({ name: 'Guest Scout', username: '', bio: '', picture: '',
      country: '', city: '', favClubs: [], favPlayers: [], memberSince: s.profile.memberSince }, s.profile);
  },
  setProfile(patch) { const s = this._read(); s.profile = Object.assign({}, s.profile, patch); this._write(s); },
  name() { return this.profile().name || 'Guest Scout'; },
  setName(n) { this.setProfile({ name: (n || '').trim() || 'Guest Scout' }); },
};

// ---- Notifications: poll the live feed, alert on followed teams/players --------
// No push backend — a client-side engine polls /api/live (+ /api/match/timeline
// for live scorers) every 45s, diffs against followed teams/players, and raises
// in-app notifications (and real desktop ones if the user opts in). State + the
// notification log live in localStorage so they survive across pages/reloads.
const NOTIF_KEY = 'atlastra_notifs_v1';
const Notif = {
  _read() { try { return JSON.parse(localStorage.getItem(NOTIF_KEY)) || {}; } catch { return {}; } },
  _write(s) { try { localStorage.setItem(NOTIF_KEY, JSON.stringify(s)); } catch { /* quota */ } },
  items() { return this._read().items || []; },
  unread() { return this.items().filter(i => !i.read).length; },
  add(it) {
    const s = this._read(); s.items = s.items || [];
    if (s.items.some(i => i.id === it.id)) return false;
    s.items.unshift({ ...it, read: false }); s.items = s.items.slice(0, 80); this._write(s); return true;
  },
  markRead() { const s = this._read(); (s.items || []).forEach(i => i.read = true); this._write(s); },
  clear() { const s = this._read(); s.items = []; this._write(s); },
  state() { return this._read().state || {}; },
  saveState(st) { const s = this._read(); s.state = st; this._write(s); },
  desktop() { return !!this._read().desktop; },
  setDesktop(v) { const s = this._read(); s.desktop = !!v; this._write(s); },
};

function timeAgo(ts) {
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

let _notifTimer = null;
async function notifTick() {
  const teams = new Set(Store.list('teams').map(t => t.name));
  const players = Store.list('players');
  players.forEach(p => { if (p.team) teams.add(p.team); });
  const followedPlayers = new Set(players.map(p => p.name));
  if (!teams.size) { updateNotifBadge(); return; }
  let d; try { d = await api('/api/live?recent=60&upcoming=60'); } catch { return; }
  const st = Notif.state(); st.status = st.status || {}; st.kick = st.kick || {}; st.goals = st.goals || {};
  const firstRun = !st.seeded;
  const now = Date.now() / 1000;
  const all = [...(d.live || []), ...(d.upcoming || []), ...(d.recent || [])];
  const mine = all.filter(m => teams.has(m.home) || teams.has(m.away));
  const fresh = [];
  for (const m of mine) {
    const eid = m.event_id, vs = `${m.home} vs ${m.away}`, href = `/match.html?id=${eid}`;
    const prev = st.status[eid];
    if (m.status === 'notstarted' && m.kickoff_ts && m.kickoff_ts > now && m.kickoff_ts - now < 7200 && !st.kick[eid]) {
      st.kick[eid] = 1;
      fresh.push({ id: 'k' + eid, ts: Date.now(), icon: '⏰', title: vs + ' — kicks off soon',
        body: 'Kickoff ' + new Date(m.kickoff_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), href });
    }
    if (m.status === 'inprogress' && prev !== 'inprogress') {
      const sc = m.home_score != null ? ` (${m.home_score}-${m.away_score}${m.minute ? ", " + m.minute + "'" : ''})` : '';
      fresh.push({ id: 'live' + eid, ts: Date.now(), icon: '🟢', title: vs + ' is live' + sc, body: m.competition || '', href });
    }
    if (m.status === 'finished' && prev && prev !== 'finished') {
      fresh.push({ id: 'ft' + eid, ts: Date.now(), icon: '🏁', title: `FT: ${m.home} ${m.home_score}-${m.away_score} ${m.away}`, body: m.competition || '', href });
    }
    st.status[eid] = m.status;
  }
  // live scorers (with names) via the timeline — only for live matches we care about
  for (const m of mine.filter(x => x.status === 'inprogress')) {
    let tl; try { tl = await api('/api/match/timeline?id=' + m.event_id); } catch { continue; }
    for (const ev of (tl.events || [])) {
      if (ev.type !== 'goal') continue;
      const key = m.event_id + '-' + ev.minute + '-' + (ev.player || '');
      if (st.goals[key]) continue;
      st.goals[key] = 1;
      if (firstRun) continue;                 // seed silently — don't replay past goals
      const followed = ev.player && followedPlayers.has(ev.player);
      const team = ev.side === 'home' ? m.home : m.away;
      fresh.push({ id: 'g' + key, ts: Date.now(), icon: followed ? '⭐' : '⚽',
        title: followed ? `${ev.player} scored!` : `Goal — ${team}`,
        body: `${m.home} ${ev.home_score}-${ev.away_score} ${m.away} · ${ev.minute}'`,
        href: `/match.html?id=${m.event_id}`, big: followed });
    }
  }
  st.seeded = 1; Notif.saveState(st);
  for (const f of fresh) {
    if (Notif.add(f) && Notif.desktop() && 'Notification' in window && Notification.permission === 'granted') {
      try { new Notification('Atlastra · ' + f.title, { body: f.body }); } catch { /* */ }
    }
  }
  updateNotifBadge();
  const panel = document.getElementById('npanel');
  if (panel && panel.classList.contains('open')) renderNotifPanel();
}

function updateNotifBadge() {
  const b = document.getElementById('nbadge'); if (!b) return;
  const n = Notif.unread(); b.textContent = n > 9 ? '9+' : n; b.style.display = n ? 'flex' : 'none';
}

function renderNotifPanel() {
  const p = document.getElementById('npanel'); if (!p) return;
  const items = Notif.items();
  const permBtn = ('Notification' in window && Notification.permission !== 'granted')
    ? `<button class="npanel-perm" id="nperm">🔔 Enable desktop alerts</button>` : '';
  const list = items.length ? items.map(i => `
    <a class="nitem ${i.read ? '' : 'unread'} ${i.big ? 'big' : ''}" href="${i.href || '#'}">
      <span class="nitem-ic">${i.icon || '🔔'}</span>
      <span class="nitem-tx"><b>${i.title}</b><span>${i.body || ''}</span></span>
      <span class="nitem-ago">${timeAgo(i.ts)}</span></a>`).join('')
    : `<div class="npanel-empty">No alerts yet.<br>Follow players and teams to get notified when they play or score.</div>`;
  p.innerHTML = `<div class="npanel-h"><b>Notifications</b>${items.length ? '<button id="nclear">Clear</button>' : ''}</div>
    ${permBtn}<div class="npanel-list">${list}</div>`;
  const pb = document.getElementById('nperm');
  if (pb) pb.onclick = (e) => { e.preventDefault(); Notification.requestPermission().then(r => { if (r === 'granted') Notif.setDesktop(true); renderNotifPanel(); }); };
  const cl = document.getElementById('nclear');
  if (cl) cl.onclick = (e) => { e.preventDefault(); Notif.clear(); renderNotifPanel(); updateNotifBadge(); };
}

// Make the top-right avatar a link to the user's profile (shows their picture
// or initials). Runs on every page alongside the notification bell.
function initTopbarUser() {
  const av = document.querySelector('.tb-icons .avatar');
  if (!av) return;
  const p = Store.profile();
  av.style.cursor = 'pointer';
  av.title = 'Your profile';
  if (p.picture) {
    av.style.backgroundImage = `url('${p.picture}')`;
    av.style.backgroundSize = 'cover';
    av.style.backgroundPosition = 'center';
    av.textContent = '';
  } else {
    av.textContent = initials(p.name);
    av.classList.add('avatar-ini');
  }
  av.onclick = () => { location.href = '/profile.html'; };
}

function initNotifications() {
  const icons = document.querySelector('.tb-icons');
  if (!icons || document.getElementById('nbell')) return;
  initTopbarUser();
  [...icons.children].forEach(c => { if ((c.textContent || '').trim() === '🔔') c.remove(); });
  const wrap = document.createElement('div'); wrap.className = 'nbell-wrap';
  wrap.innerHTML = `<button class="nbell" id="nbell" title="Notifications">🔔<i class="nbadge" id="nbadge"></i></button><div class="npanel" id="npanel"></div>`;
  icons.prepend(wrap);
  const panel = document.getElementById('npanel');
  document.getElementById('nbell').onclick = (e) => {
    e.stopPropagation();
    const open = panel.classList.toggle('open');
    if (open) { renderNotifPanel(); Notif.markRead(); updateNotifBadge(); }
  };
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) panel.classList.remove('open'); });
  updateNotifBadge();
  notifTick();
  if (_notifTimer) clearInterval(_notifTimer);
  _notifTimer = setInterval(notifTick, 45000);
  window.addEventListener('atla-store', () => notifTick());
}
if (document.readyState !== 'loading') initNotifications();
else document.addEventListener('DOMContentLoaded', initNotifications);
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
const NAV_ANALYTICS = [
  ['Rankings & Awards', 'rankings', '/rankings.html'],
  ['Best XI on a Budget', 'teams', '/bestxi.html'],
  ['Find the Next…', 'compare', '/findnext.html'],
  ['Player Cards', 'players', '/card.html'],
  ['Football DNA Map', 'archetypes', '/dnamap.html'],
  ['Match Preview', 'compare', '/preview.html'],
];

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
  ['My Profile', 'profile', '/profile.html'], ['My Players', 'myplayers', '/profile.html?tab=players'],
  ['My Comparisons', 'compare', '/profile.html?tab=comparisons'], ['Watchlist', 'watchlist', '/profile.html?tab=watchlist'],
];
// name, FotMob league id (for the crest), colour fallback if the logo 404s
const LEAGUES = [['Premier League', 47, '#3d195b'], ['La Liga', 87, '#e8a33d'],
                 ['Serie A', 55, '#0067b1'], ['Bundesliga', 54, '#d20515'], ['Ligue 1', 53, '#091c3e']];

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
  const leagues = LEAGUES.map(([n, id, c]) =>
    `<a href="/teams.html" class="navi league"><span class="lglogo"><img src="https://images.fotmob.com/image_resources/logo/leaguelogo/${id}.png" alt="" loading="lazy" onerror="this.parentElement.style.background='${c}';this.remove()"></span><span class="t">${n}</span></a>`).join('');
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
    <a href="/profile.html" class="sb-user"><span class="ava"${Store.profile().picture ? ` style="background-image:url('${Store.profile().picture}');background-size:cover;background-position:center;color:transparent"` : ''}>${initials(Store.name())}<i class="on"></i></span>
      <span class="u-tx"><b>${Store.name()}</b><span>View Profile</span></span><span class="chev">${svg('chevR')}</span></a>`;
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
