renderSidebar('My Profile');
attachSearchDropdown(document.getElementById('searchBox'));

const TABS = [
  ['overview', 'Overview'], ['players', 'My Players'], ['teams', 'My Teams'],
  ['comparisons', 'Comparisons'], ['watchlist', 'Watchlist'],
];
let tab = new URLSearchParams(location.search).get('tab') || 'overview';
let editing = false, draft = null;

const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const sinceLabel = (ts) => new Date(ts).toLocaleDateString([], { month: 'long', year: 'numeric' });

// ---------- header (view mode) ----------
function renderHead() {
  if (editing) return renderEdit();
  const p = Store.profile();
  const counts = { players: Store.count('players'), teams: Store.count('teams'),
    comparisons: Store.count('comparisons'), watchlist: Store.count('watchlist') };
  const loc = [p.city, p.country].filter(Boolean).join(', ');
  const ava = p.picture ? `<img src="${p.picture}" alt="">` : initials(p.name);
  const favClubChip = (c) => `<a class="pf-favchip" href="/team.html?name=${encodeURIComponent(c.name)}">
    ${c.crest ? `<img src="${c.crest}" alt="">` : '🛡️'}<span>${esc(c.name)}</span></a>`;
  const favPlayerChip = (pl) => `<a class="pf-favchip" href="/player.html?name=${encodeURIComponent(pl.name)}">
    ${pl.photo ? `<img src="${pl.photo}" alt="">` : `<i>${initials(pl.name)}</i>`}<span>${esc(pl.name)}</span></a>`;
  const favNationChip = (n) => `<a class="pf-favchip"${n.natId ? ` href="/nat.html?id=${n.natId}"` : ''}>
    <i>${flagISO2(n.cc) || initials(n.name)}</i><span>${esc(n.name)}</span></a>`;
  const favNations = p.favNations || [];

  document.getElementById('pfHead').innerHTML = `
    <div class="pf-ava">${ava}</div>
    <div class="pf-id">
      <div class="pf-id-top">
        <h2>${esc(p.name)}</h2>
        ${p.username ? `<span class="pf-handle">@${esc(p.username)}</span>` : ''}
        <button class="btn btn-ghost pf-editbtn" id="pfEditBtn">✎ Edit profile</button>
      </div>
      <div class="pf-loc">${loc ? `📍 ${esc(loc)} &nbsp;·&nbsp; ` : ''}Member since ${sinceLabel(p.memberSince)}</div>
      ${p.bio ? `<p class="pf-bio">${esc(p.bio)}</p>` : ''}
      ${p.favClubs.length ? `<div class="pf-fav"><span class="pf-fav-lbl">Favourite ${p.favClubs.length > 1 ? 'clubs' : 'club'}</span><div class="pf-favchips">${p.favClubs.map(favClubChip).join('')}</div></div>` : ''}
      ${p.favPlayers.length ? `<div class="pf-fav"><span class="pf-fav-lbl">Favourite ${p.favPlayers.length > 1 ? 'players' : 'player'}</span><div class="pf-favchips">${p.favPlayers.map(favPlayerChip).join('')}</div></div>` : ''}
      ${favNations.length ? `<div class="pf-fav"><span class="pf-fav-lbl">Favourite national ${favNations.length > 1 ? 'teams' : 'team'}</span><div class="pf-favchips">${favNations.map(favNationChip).join('')}</div></div>` : ''}
      <div class="pf-stats">
        <div class="pf-stat"><b>${counts.players}</b><span>Following</span></div>
        <div class="pf-stat"><b>${counts.teams}</b><span>Teams</span></div>
        <div class="pf-stat"><b>${counts.comparisons}</b><span>Comparisons</span></div>
        <div class="pf-stat"><b>${counts.watchlist}</b><span>Watchlist</span></div>
      </div>
    </div>`;
  document.getElementById('pfEditBtn').onclick = () => { editing = true; draft = JSON.parse(JSON.stringify(Store.profile())); renderHead(); };
}

// ---------- header (edit mode) ----------
function renderEdit() {
  const ava = draft.picture ? `<img src="${draft.picture}" alt="" id="edPicImg">` : `<span id="edPicImg">${initials(draft.name)}</span>`;
  document.getElementById('pfHead').innerHTML = `
    <div class="pf-edit">
      <div class="pf-edit-pic">
        <div class="pf-ava" id="edPic">${ava}</div>
        <label class="pf-pic-btn">Upload<input type="file" id="edFile" accept="image/*" hidden></label>
        ${draft.picture ? `<button class="pf-pic-rm" id="edPicRm">Remove</button>` : ''}
      </div>
      <div class="pf-edit-fields">
        <div class="pf-row">
          <div class="pf-field"><label>Display name</label><input id="edName" maxlength="40" value="${esc(draft.name)}"></div>
          <div class="pf-field"><label>Username</label><input id="edUser" maxlength="24" placeholder="handle" value="${esc(draft.username)}"></div>
        </div>
        <div class="pf-field"><label>Bio</label><textarea id="edBio" maxlength="240" rows="2" placeholder="A short bio…">${esc(draft.bio)}</textarea></div>
        <div class="pf-row">
          <div class="pf-field"><label>City</label><input id="edCity" maxlength="40" value="${esc(draft.city)}"></div>
          <div class="pf-field"><label>Country</label><input id="edCountry" maxlength="40" value="${esc(draft.country)}"></div>
        </div>
        <div class="pf-field"><label>Favourite clubs</label>
          <div class="pf-favchips" id="edClubs"></div>
          <div class="pf-fav-add"><input id="edClubSearch" placeholder="Add a club…"><div class="card-dd" id="edClubDD"></div></div>
        </div>
        <div class="pf-field"><label>Favourite players</label>
          <div class="pf-favchips" id="edPlayers"></div>
          <div class="pf-fav-add"><input id="edPlayerSearch" placeholder="Add a player…"><div class="card-dd" id="edPlayerDD"></div></div>
        </div>
        <div class="pf-field"><label>Favourite national teams</label>
          <div class="pf-favchips" id="edNations"></div>
          <div class="pf-fav-add"><input id="edNationSearch" placeholder="Add a national team…"><div class="card-dd" id="edNationDD"></div></div>
        </div>
        <div class="pf-edit-actions">
          <button class="btn btn-primary" id="edSave">Save profile</button>
          <button class="btn btn-ghost" id="edCancel">Cancel</button>
        </div>
      </div>
    </div>`;

  const bind = (id, key) => { document.getElementById(id).oninput = (e) => { draft[key] = e.target.value; }; };
  bind('edName', 'name'); bind('edBio', 'bio'); bind('edCity', 'city'); bind('edCountry', 'country');
  document.getElementById('edUser').oninput = (e) => { draft.username = e.target.value.replace(/[^A-Za-z0-9_.]/g, ''); e.target.value = draft.username; };

  // picture upload (downscaled to 256px → small data URL)
  document.getElementById('edFile').onchange = (e) => {
    const f = e.target.files[0]; if (!f) return;
    const r = new FileReader();
    r.onload = () => {
      const img = new Image();
      img.onload = () => {
        const max = 256, sc = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * sc); c.height = Math.round(img.height * sc);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        draft.picture = c.toDataURL('image/jpeg', 0.85);
        renderEdit();
      };
      img.src = r.result;
    };
    r.readAsDataURL(f);
  };
  const rm = document.getElementById('edPicRm');
  if (rm) rm.onclick = () => { draft.picture = ''; renderEdit(); };

  renderFavChips();
  favSearch('edClubSearch', 'edClubDD', 'teams', 'favClubs');
  favSearch('edPlayerSearch', 'edPlayerDD', 'players', 'favPlayers');
  favSearch('edNationSearch', 'edNationDD', 'national', 'favNations');

  document.getElementById('edCancel').onclick = () => { editing = false; draft = null; renderHead(); };
  document.getElementById('edSave').onclick = () => {
    Store.setProfile({ name: draft.name.trim() || 'Guest Scout', username: draft.username.trim(),
      bio: draft.bio.trim(), city: draft.city.trim(), country: draft.country.trim(),
      picture: draft.picture, favClubs: draft.favClubs, favPlayers: draft.favPlayers,
      favNations: draft.favNations || [] });
    editing = false; draft = null; renderHead(); renderTabs(); renderBody(); renderSidebar('My Profile');
  };
}

function renderFavChips() {
  const chip = (item, key) => `<span class="pf-favchip editable">
    ${item.crest || item.photo ? `<img src="${item.crest || item.photo}" alt="">`
      : `<i>${(item.cc ? flagISO2(item.cc) : '') || initials(item.name)}</i>`}
    <span>${esc(item.name)}</span>
    <button class="pf-favchip-rm" data-key="${key}" data-id="${esc(item.name)}">✕</button></span>`;
  document.getElementById('edClubs').innerHTML = draft.favClubs.map(c => chip(c, 'favClubs')).join('') || '<span class="pf-fav-none">None yet</span>';
  document.getElementById('edPlayers').innerHTML = draft.favPlayers.map(p => chip(p, 'favPlayers')).join('') || '<span class="pf-fav-none">None yet</span>';
  document.getElementById('edNations').innerHTML = (draft.favNations || []).map(n => chip(n, 'favNations')).join('') || '<span class="pf-fav-none">None yet</span>';
  document.querySelectorAll('.pf-favchip-rm').forEach(b => b.onclick = () => {
    draft[b.dataset.key] = draft[b.dataset.key].filter(x => x.name !== b.dataset.id); renderFavChips();
  });
}

function favSearch(inputId, ddId, kind, listKey) {
  const input = document.getElementById(inputId), dd = document.getElementById(ddId);
  const hide = () => { dd.style.display = 'none'; };
  input.oninput = () => {
    const q = input.value.trim();
    if (q.length < 2) { hide(); return; }
    api('/api/search?q=' + encodeURIComponent(q)).then(r => {
      const rows = (kind === 'teams' ? (r.teams || []) : kind === 'national' ? (r.national || [])
        : (r.players || [])).slice(0, 6);
      if (!rows.length) { hide(); return; }
      dd.innerHTML = rows.map(x => {
        const name = x.team || x.player, img = x.team_logo || x.photo || '';
        const sub = kind === 'national' ? 'National team' : (x.league || [x.team, x.position].filter(Boolean).join(' · '));
        return `<div class="card-dd-it" data-n="${esc(name)}" data-img="${esc(img)}" data-cc="${esc(x.cc || '')}" data-nat="${esc(x.team_id || '')}">
          <b>${esc(name)}</b><span>${esc(sub)}</span></div>`;
      }).join('');
      dd.style.display = 'block';
      dd.querySelectorAll('.card-dd-it').forEach(el => el.onclick = () => {
        const name = el.dataset.n;
        if (!draft[listKey].some(x => x.name === name)) {
          draft[listKey].push(
            kind === 'teams' ? { name, crest: el.dataset.img }
            : kind === 'national' ? { name, cc: el.dataset.cc, natId: el.dataset.nat }
            : { name, photo: el.dataset.img });
        }
        input.value = ''; hide(); renderFavChips();
      });
    }).catch(() => {});
  };
  document.addEventListener('click', (e) => { if (!input.contains(e.target) && !dd.contains(e.target)) hide(); });
}

// ---------- tabs + lists (unchanged behaviour) ----------
function renderTabs() {
  document.getElementById('pfTabs').innerHTML = TABS.map(([k, label]) => {
    const n = k === 'overview' ? '' : ` <i>${Store.count(k)}</i>`;
    return `<button class="pf-tab ${k === tab ? 'active' : ''}" data-k="${k}">${label}${n}</button>`;
  }).join('');
  document.querySelectorAll('.pf-tab').forEach(b => b.onclick = () => {
    tab = b.dataset.k; history.replaceState(null, '', '?tab=' + tab); renderTabs(); renderBody();
  });
}

const emptyState = (icon, msg, cta) =>
  `<div class="pf-empty"><div class="pf-empty-ic">${icon}</div><p>${msg}</p>${cta || ''}</div>`;

function playerCard(p, listKey) {
  return `<div class="pf-pcard">
    <a class="pf-pcard-main" href="/player.html?name=${encodeURIComponent(p.name)}">
      <span class="pf-ava-sm">${avatarHTML(p.photo, p.name)}</span>
      <span class="pf-pc-tx"><b>${esc(p.name)}</b><span>${[p.position, p.team].filter(Boolean).join(' · ')}</span></span>
      ${p.rating != null ? `<span class="pf-pc-rat">${p.rating}</span>` : ''}
    </a>
    <button class="pf-rm" data-k="${listKey}" data-id="${esc(p.id || p.name)}" title="Remove">✕</button>
  </div>`;
}
function teamCard(t) {
  const href = t.isNat ? `/nat.html?id=${t.natId}` : `/team.html?name=${encodeURIComponent(t.name)}`;
  const crest = t.isNat ? `<span class="pf-crest" style="font-size:22px">${flagISO2(t.cc) || '🏳️'}</span>`
    : `<span class="pf-crest">${crestHTML(t.crest, 'crest') || '🛡️'}</span>`;
  return `<div class="pf-pcard">
    <a class="pf-pcard-main" href="${href}">
      ${crest}
      <span class="pf-pc-tx"><b>${esc(t.name)}</b><span>${esc(t.isNat ? 'National team' : (t.league || ''))}</span></span>
    </a>
    <button class="pf-rm" data-k="teams" data-id="${esc(t.id || t.name)}" title="Remove">✕</button>
  </div>`;
}
function cmpRow(c) {
  const qs = (c.names || []).map(n => 'name=' + encodeURIComponent(n))
    .concat((c.stats || []).map(s => 'stat=' + encodeURIComponent(s))).join('&');
  return `<div class="pf-cmp">
    <a href="/compare.html?${qs}"><span class="pf-cmp-ic">⇄</span><b>${esc(c.label || (c.names || []).join(' vs '))}</b></a>
    <button class="pf-rm" data-k="comparisons" data-id="${esc(c.id || '')}" title="Remove">✕</button>
  </div>`;
}
const grid = (items, html) => `<div class="pf-grid">${items.map(html).join('')}</div>`;

function renderBody() {
  const out = document.getElementById('pf-out');
  const P = Store.list('players'), T = Store.list('teams'),
    C = Store.list('comparisons'), Wl = Store.list('watchlist');
  if (tab === 'players') {
    out.innerHTML = `<section class="card">${P.length ? grid(P, p => playerCard(p, 'players'))
      : emptyState('👤', "You're not following any players yet.", '<a class="btn btn-primary" href="/players.html">Browse players</a>')}</section>`;
  } else if (tab === 'teams') {
    out.innerHTML = `<section class="card">${T.length ? grid(T, teamCard)
      : emptyState('🛡️', 'No followed teams yet.', '<a class="btn btn-primary" href="/teams.html">Browse teams</a>')}</section>`;
  } else if (tab === 'comparisons') {
    out.innerHTML = `<section class="card">${C.length ? `<div class="pf-cmps">${C.map(cmpRow).join('')}</div>`
      : emptyState('⇄', 'No saved comparisons.', '<a class="btn btn-primary" href="/compare.html">Build a comparison</a>')}</section>`;
  } else if (tab === 'watchlist') {
    out.innerHTML = `<section class="card">${Wl.length ? grid(Wl, p => playerCard(p, 'watchlist'))
      : emptyState('🔖', 'Your watchlist is empty. Tap “Watch” on any player profile to add a scouting target.', '<a class="btn btn-primary" href="/players.html">Browse players</a>')}</section>`;
  } else {
    const sec = (title, body, more) => `<section class="card pf-ov"><div class="card-h"><h3>${title}</h3>${more || ''}</div>${body}</section>`;
    out.innerHTML = `
      ${sec('Players you follow', P.length ? grid(P.slice(0, 8), p => playerCard(p, 'players'))
        : emptyState('👤', 'Follow players to see them here.', '<a class="btn btn-ghost" href="/players.html">Browse players</a>'),
        P.length > 8 ? `<button class="see pf-jump" data-k="players">See all ${P.length} →</button>` : '')}
      ${sec('Teams you follow', T.length ? grid(T, teamCard)
        : emptyState('🛡️', 'Follow teams to see them here.', '<a class="btn btn-ghost" href="/teams.html">Browse teams</a>'))}
      ${sec('Saved comparisons', C.length ? `<div class="pf-cmps">${C.slice(0, 6).map(cmpRow).join('')}</div>`
        : emptyState('⇄', 'Save a comparison to revisit it later.', '<a class="btn btn-ghost" href="/compare.html">Build a comparison</a>'))}
      ${sec('Watchlist', Wl.length ? grid(Wl.slice(0, 8), p => playerCard(p, 'watchlist'))
        : emptyState('🔖', 'Add scouting targets with the Watch button on any player.', ''))}`;
  }
  out.querySelectorAll('.pf-rm').forEach(b => b.onclick = () => {
    Store.remove(b.dataset.k, b.dataset.id); renderHead(); renderTabs(); renderBody();
  });
  out.querySelectorAll('.pf-jump').forEach(b => b.onclick = () => {
    tab = b.dataset.k; history.replaceState(null, '', '?tab=' + tab); renderTabs(); renderBody();
  });
}

renderHead();
renderTabs();
renderBody();
