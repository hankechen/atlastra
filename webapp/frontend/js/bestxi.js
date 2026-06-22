renderSidebar('Best XI on a Budget');
attachSearchDropdown(document.getElementById('searchBox'));

const FORMATIONS = ['4-3-3', '4-4-2', '3-5-2', '3-4-3'];
let formation = '4-3-3';

const fmtM = (m) => '€' + m + 'M';
const bxiRatColor = (r) => r >= 85 ? '#2fbf71' : r >= 75 ? '#7d9f3a' : r >= 65 ? '#c9a227' : '#c97a27';

function renderForms() {
  document.getElementById('forms').innerHTML = FORMATIONS.map(f =>
    `<button class="bxi-fbtn ${f === formation ? 'active' : ''}" data-f="${f}">${f}</button>`).join('');
  document.querySelectorAll('.bxi-fbtn').forEach(b => b.onclick = () => {
    formation = b.dataset.f; renderForms();
  });
}

// keep slider and number box in sync
const range = document.getElementById('budgetRange');
const num = document.getElementById('budgetNum');
range.oninput = () => { num.value = range.value; };
num.oninput = () => { range.value = Math.min(range.max, Math.max(range.min, num.value || 0)); };

function chip(p) {
  const ph = p.photo ? `style="background-image:url('${p.photo}')"` : '';
  return `<a class="bxi-card" href="/player.html?name=${encodeURIComponent(p.player)}">
    <div class="bxi-rat" style="background:${bxiRatColor(p.rating)}">${p.rating}</div>
    <div class="bxi-photo" ${ph}></div>
    <div class="bxi-name">${p.player}</div>
    <div class="bxi-sub">${p.position} · ${p.team || ''}</div>
    <div class="bxi-val">${fmtM(p.value_m)}</div>
  </a>`;
}

// pitch rows top(attack) -> bottom(GK)
const ROWS = ['ST', 'W', 'MID', 'DEF', 'GK'];
function pitch(xi) {
  const by = { ST: [], W: [], MID: [], DEF: [], GK: [] };
  for (const p of xi) by[p.cat === 'CB' || p.cat === 'FB' ? 'DEF' : p.cat].push(p);
  // order the defence row CB(s) centre, FB(s) outside-ish -> just CBs then FBs
  by.DEF.sort((a, b) => (a.cat === 'FB') - (b.cat === 'FB'));
  return `<div class="bxi-pitch">${ROWS.filter(r => by[r].length).map(r =>
    `<div class="bxi-row">${by[r].map(chip).join('')}</div>`).join('')}</div>`;
}

async function build() {
  const out = document.getElementById('bxi-out');
  const budget = Math.round(+num.value || 200);
  out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-spinner"></div>
    <p>Signing the best ${formation} for ${fmtM(budget)}…</p></div></section>`;
  let d;
  try { d = await api(`/api/best_xi?budget=${budget}&formation=${formation}`); }
  catch { out.innerHTML = '<section class="card"><div class="sr-empty"><p>Could not reach the builder.</p></div></section>'; return; }
  if (!d.available) {
    out.innerHTML = `<section class="card"><div class="sr-empty"><div class="sr-empty-ic">💸</div>
      <p>${(d.error || 'No valid XI.').replace(/</g, '&lt;')}</p>
      ${d.min_budget ? `<button class="btn btn-ghost" id="bumpBudget">Set budget to €${d.min_budget}M</button>` : ''}</div></section>`;
    const bump = document.getElementById('bumpBudget');
    if (bump) bump.onclick = () => { num.value = d.min_budget; range.value = Math.min(range.max, d.min_budget); build(); };
    return;
  }
  const pct = Math.round(d.spent_m / d.budget_m * 100);
  out.innerHTML = `
    <section class="card bxi-summary">
      <div class="bxi-stat"><span>Formation</span><b>${d.formation}</b></div>
      <div class="bxi-stat"><span>Squad rating</span><b>${d.total_rating} <small>avg ${d.avg_rating}</small></b></div>
      <div class="bxi-stat"><span>Spent</span><b>${fmtM(d.spent_m)} <small>of ${fmtM(d.budget_m)}</small></b></div>
      <div class="bxi-stat"><span>In the bank</span><b>${fmtM(d.remaining_m)}</b></div>
      <div class="bxi-spend"><div class="bxi-spend-bar" style="width:${Math.min(100, pct)}%"></div></div>
    </section>
    <section class="card bxi-pitchcard">${pitch(d.xi)}</section>`;
}

renderForms();
document.getElementById('build').onclick = build;
build();
