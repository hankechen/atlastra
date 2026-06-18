renderSidebar('Teams');

const formPills = (form) => (form || []).map(f => `<span class="form-pill form-${f}">${f}</span>`).join('');
const teamHref = (name) => '/team.html?name=' + encodeURIComponent(name);

// promotion/relegation tint by position (top 4 = UCL-ish, bottom 3 = drop)
function posClass(pos, n) {
  if (pos <= 4) return 'ucl';
  if (pos > n - 3) return 'rel';
  return '';
}

async function loadTable(key) {
  const rows = await api('/api/league_table?league=' + encodeURIComponent(key));
  const n = rows.length;
  document.getElementById('table').innerHTML = rows.map(t => `
    <tr class="ltbl-row ${posClass(t.pos, n)}" onclick="location.href='${teamHref(t.team)}'">
      <td class="pos">${t.pos}</td>
      <td class="tcell"><span class="crest-w">${crestHTML(t.team_logo, 'crest-md')}</span><span class="tn">${t.team}</span></td>
      <td>${t.p}</td><td>${t.w}</td><td>${t.d}</td><td>${t.l}</td>
      <td>${t.gf}</td><td>${t.ga}</td><td class="${t.gd > 0 ? 'pos-n' : t.gd < 0 ? 'neg-n' : ''}">${t.gd > 0 ? '+' : ''}${t.gd}</td>
      <td class="muted">${t.xg_for ?? '—'}</td><td class="muted">${t.xg_against ?? '—'}</td>
      <td class="muted">${t.xpts ?? '—'}</td>
      <td><b>${t.pts}</b></td>
      <td class="formcell">${formPills(t.form)}</td>
    </tr>`).join('');
}

(async () => {
  const leagues = await api('/api/leagues');
  const tabsEl = document.getElementById('leagueTabs');
  tabsEl.innerHTML = leagues.map((l, i) =>
    `<span class="tab ${i ? '' : 'active'}" data-k="${l.key}">${l.name}</span>`).join('');
  tabsEl.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    tabsEl.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active'); loadTable(t.dataset.k);
  });
  loadTable(leagues[0].key);
})();

// search -> jump to a team page on Enter
document.getElementById('searchBox').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) location.href = teamHref(e.target.value.trim());
});
