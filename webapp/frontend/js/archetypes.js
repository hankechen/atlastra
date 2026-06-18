renderSidebar('Archetypes');
attachSearchDropdown(document.getElementById('searchBox'));

let current = null;

async function loadRole(name) {
  current = name;
  document.querySelectorAll('.arch-nav .role').forEach(b =>
    b.classList.toggle('active', b.dataset.role === name));
  history.replaceState(null, '', '/archetypes.html?role=' + encodeURIComponent(name));
  const d = await api('/api/archetype?name=' + encodeURIComponent(name));
  const detail = document.getElementById('archDetail');
  if (!d.name) { detail.innerHTML = '<div class="empty">Role not found.</div>'; return; }
  detail.innerHTML = `
    <div class="ad-head">
      <div><div class="ad-grp">${d.group_label}</div><h2 class="ad-name">${d.name}</h2>
        <p class="ad-blurb">${d.blurb}</p></div>
    </div>
    <div class="ad-sig"><span class="ad-sig-l">Signature</span>${d.signature.map(s => `<span class="trait">${s}</span>`).join('')}</div>
    <div class="ad-grid-h">Top ${d.players.length} ${d.name}s</div>
    <div class="pgrid">${d.players.map(p => `
      <div class="pcard" onclick="location.href='${pHref(p.player)}'">
        <div class="top"><div class="photo">${avatarHTML(p.photo, p.player)}</div>
          <div class="rt">${p.rating ?? '—'}</div></div>
        <div class="nm">${p.player}</div>
        <div class="sub">${crestHTML(p.team_logo, 'crest-sm')}${p.team || ''} · ${p.position || ''}</div>
        <div class="fitbar"><i style="width:${p.fit ?? 0}%"></i></div>
        <div class="sub" style="margin-top:5px">${p.fit ?? '—'}% fit to role</div>
      </div>`).join('')}</div>`;
}

(async () => {
  const groups = await api('/api/archetypes');
  document.getElementById('archNav').innerHTML = groups.map(g => `
    <div class="arch-nav-grp">
      <div class="nav-label" style="padding:8px 6px 6px">${g.group_label}</div>
      ${g.archetypes.map(a => `
        <button class="role" data-role="${a.name}">
          <span class="rn">${a.name}</span><span class="rc">${a.count}</span></button>`).join('')}
    </div>`).join('');
  document.querySelectorAll('.arch-nav .role').forEach(b => b.onclick = () => loadRole(b.dataset.role));
  const want = new URLSearchParams(location.search).get('role');
  loadRole(want || groups[0].archetypes[0].name);
})();
