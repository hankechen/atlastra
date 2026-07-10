// Coach / manager profile — career (teams managed, with dates) + trophies (FotMob).
renderSidebar('Live Matches');
attachSearchDropdown(document.getElementById('searchBox'));

const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const yr = (d) => d ? d.slice(0, 4) : '';
const cid = new URLSearchParams(location.search).get('id');

(async () => {
  let c;
  try { c = await api('/api/coach?id=' + encodeURIComponent(cid)); } catch { c = null; }
  if (!c || !c.available) {
    document.getElementById('hero').innerHTML = '<div class="placeholder-note">Coach not found.</div>';
    return;
  }
  document.title = `Atlastra — ${c.name}`;

  // hero: photo + name + current team + coaching-year span
  const first = c.career[c.career.length - 1], firstYr = first ? yr(first.start) : '';
  document.getElementById('hero').innerHTML = `
    <div class="ph">
      <div class="ph-img"><div class="photo">${avatarHTML(c.photo, c.name)}</div></div>
      <div>
        <h1>${esc(c.name)}</h1>
        <div class="team">👔 Manager${c.current_team ? ` · <a href="/team.html?name=${encodeURIComponent(c.current_team)}">${esc(c.current_team)}</a>` : ''}</div>
        <div class="meta">
          ${c.country ? `<div class="m"><span>Country</span><b>${esc(c.country)}</b></div>` : ''}
          <div class="m"><span>Clubs/teams managed</span><b>${c.career.length}</b></div>
          ${firstYr ? `<div class="m"><span>Managing since</span><b>${firstYr}</b></div>` : ''}
          <div class="m"><span>Trophies</span><b>${c.trophies.reduce((n, t) => n + t.count, 0)}</b></div>
        </div>
      </div>
    </div>`;

  // career timeline (newest first)
  document.getElementById('career').innerHTML = c.career.length ? c.career.map(x => `
    <div class="cc-row">
      <div class="cc-team">${crestHTML(x.team_id ? `https://images.fotmob.com/image_resources/logo/teamlogo/${x.team_id}.png` : null, 'crest-sm')}
        <a href="/team.html?name=${encodeURIComponent(x.team)}">${esc(x.team)}</a></div>
      <div class="cc-when">${yr(x.start) || '—'} – ${x.active ? '<b class="cc-now">Present</b>' : (yr(x.end) || '—')}</div>
    </div>`).join('') : '<div class="placeholder-note">No managerial career on record.</div>';

  // trophies
  document.getElementById('trophies').innerHTML = c.trophies.length ? c.trophies.map(t => `
    <div class="ct-row">
      <div class="ct-comp"><b>${esc(t.competition)}</b><span>${esc(t.team)}</span></div>
      <div class="ct-count">×${t.count}</div>
    </div>`).join('') : '<div class="placeholder-note">No trophies listed for this coach.</div>';
})();
