renderSidebar('Best XI on a Budget');
attachSearchDropdown(document.getElementById('searchBox'));

const FORMATIONS = ['4-3-3', '4-4-2', '4-2-3-1', '4-1-4-1', '4-3-1-2', '4-5-1',
  '3-5-2', '3-4-3', '3-4-2-1', '5-3-2'];
const PRESETS = [100, 250, 500, 1000];
let formation = '4-3-3';

const fmtM = (m) => '€' + m + 'M';
const bxiRatColor = (r) => r >= 85 ? '#39d07f' : r >= 75 ? '#a6d14a' : r >= 65 ? '#e7c14a' : '#e79a4a';
const bxiInitials = (n) => n.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();

// little dot pictogram of the formation (attack on top, keeper at the base)
function formPic(f) {
  const rows = f.split('-').map(Number).reverse().concat([1]);
  return `<div class="bxi-pic">${rows.map(n => `<div class="bxi-pic-r">${'<i></i>'.repeat(n)}</div>`).join('')}</div>`;
}

function renderForms() {
  document.getElementById('forms').innerHTML = FORMATIONS.map(f =>
    `<button class="bxi-fbtn ${f === formation ? 'active' : ''}" data-f="${f}">${formPic(f)}<span>${f}</span></button>`).join('');
  document.querySelectorAll('.bxi-fbtn').forEach(b => b.onclick = () => { formation = b.dataset.f; renderForms(); });
}

const range = document.getElementById('budgetRange');
const num = document.getElementById('budgetNum');
function fillRange() {
  const p = (range.value - range.min) / (range.max - range.min) * 100;
  range.style.background = `linear-gradient(90deg,var(--accent) ${p}%,var(--border2) ${p}%)`;
}
function setBudget(v, fromNum) {
  v = Math.max(+range.min, Math.min(3000, Math.round(+v || 0)));
  num.value = v;
  range.value = Math.min(+range.max, v);
  fillRange();
}
range.oninput = () => { num.value = range.value; fillRange(); };
num.oninput = () => { range.value = Math.min(+range.max, Math.max(+range.min, num.value || 0)); fillRange(); };

function renderPresets() {
  const el = document.getElementById('presets');
  if (!el) return;
  el.innerHTML = PRESETS.map(p => `<button class="bxi-preset" data-v="${p}">${fmtM(p)}</button>`).join('');
  el.querySelectorAll('.bxi-preset').forEach(b => b.onclick = () => { setBudget(b.dataset.v); build(); });
}

function tok(p) {
  const av = p.photo
    ? `<div class="bxi-av" style="background-image:url('${p.photo}')"></div>`
    : `<div class="bxi-av bxi-av-ini">${bxiInitials(p.player)}</div>`;
  return `<a class="bxi-tok" href="/player.html?name=${encodeURIComponent(p.player)}" style="--rc:${bxiRatColor(p.rating)}">
    <div class="bxi-avwrap">${av}<span class="bxi-rat">${p.rating}</span></div>
    <div class="bxi-name">${p.player}</div>
    <div class="bxi-meta"><span class="bxi-pos">${p.position}</span><span class="bxi-val">${fmtM(p.value_m)}</span></div>
  </a>`;
}

// d.lines is back -> front (GK first); render with the attacking line on top.
function pitch(d) {
  const lines = (d.lines || []).slice().reverse();
  const rows = lines.map(line => `<div class="bxi-row">${line.map(tok).join('')}</div>`).join('');
  return `<div class="bxi-pitch">
    <div class="bxi-markings">
      <span class="m-half"></span><span class="m-circle"></span><span class="m-spot"></span>
      <span class="m-box m-box-t"></span><span class="m-box m-box-b"></span>
      <span class="m-six m-six-t"></span><span class="m-six m-six-b"></span>
      <span class="m-pspot m-pspot-t"></span><span class="m-pspot m-pspot-b"></span>
    </div>
    <div class="bxi-rows">${rows}</div>
  </div>`;
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
    if (bump) bump.onclick = () => { setBudget(d.min_budget); build(); };
    return;
  }
  const pct = Math.min(100, Math.round(d.spent_m / d.budget_m * 100));
  const deg = Math.max(0, Math.min(360, Math.round(d.avg_rating / 99 * 360)));
  out.innerHTML = `
    <section class="card bxi-summary">
      <div class="bxi-gauge" style="--rc:${bxiRatColor(d.avg_rating)};--deg:${deg}deg">
        <div class="bxi-gauge-in"><b>${d.avg_rating}</b><span>AVG</span></div>
      </div>
      <div class="bxi-sumstats">
        <div class="bxi-stat"><span>Formation</span><b>${d.formation}</b></div>
        <div class="bxi-stat"><span>Squad rating</span><b>${d.total_rating}</b></div>
        <div class="bxi-stat"><span>Squad value</span><b>${fmtM(d.spent_m)}</b></div>
        <div class="bxi-stat"><span>In the bank</span><b>${fmtM(d.remaining_m)}</b></div>
      </div>
      <div class="bxi-spendwrap">
        <div class="bxi-spend-top"><span>Spent <b>${fmtM(d.spent_m)}</b></span><span><b>${fmtM(d.budget_m)}</b> budget</span></div>
        <div class="bxi-spend"><div class="bxi-spend-bar" style="width:${pct}%"></div></div>
      </div>
    </section>
    <section class="card bxi-pitchcard">${pitch(d)}</section>`;
}

renderForms();
renderPresets();
fillRange();
document.getElementById('build').onclick = build;
build();
