renderSidebar('Manager Career');
attachSearchDropdown(document.getElementById('searchBox'));

const esc = (s) => (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const body = () => document.getElementById('crBody');

// Same icon/formatting convention as Tactics Lab's match-result card (webapp/frontend/js/
// tactics.js's matchHTML/evText/statRow) -- reusing its CSS classes (.tl-*) for visual
// consistency, but a small local copy here rather than importing that page's script: it's
// one big page-local closure with no exported pieces (see the Manager Career Mode plan).
const _EVICON = { goal: '⚽', yellow: '🟨', red: '🟥', miss: '❌', save: '🧤' };
function evText(e) {
  if (e.type === 'goal') {
    const how = e.how && e.how !== 'open play' ? ` <i class="tl-evhow">${esc(e.how)}</i>` : '';
    const as = e.assist ? `<span class="tl-evas">assist: ${esc(e.assist)}</span>` : '';
    return `<b>${esc(e.player)}</b>${how}${as}`;
  }
  const note = e.type === 'red' ? 'sent off' : e.type === 'yellow' ? 'booked' : (e.how || '');
  return `<span>${esc(e.player)}</span> <i class="tl-evhow">${esc(note)}</i>`;
}
function statRow(s) {
  const h = +s.home, a = +s.away, tot = (h + a) || 1, hp = Math.round(h / tot * 100);
  const fmt = (v) => s.dp ? v.toFixed(s.dp) : Math.round(v);
  return `<div class="tl-mstat"><b>${fmt(h)}</b><span class="tl-mslbl">${esc(s.label)}</span><b>${fmt(a)}</b>
      <span class="tl-msbar"><i class="you" style="width:${hp}%"></i><i class="opp" style="width:${100 - hp}%"></i></span></div>`;
}

const TACTIC_LABELS = { tempo: 'Tempo', width: 'Width', directness: 'Directness',
  patience: 'Final-third patience', counter: 'Counter-press', line_height: 'Defensive line',
  press: 'Press intensity', compactness: 'Compactness' };

let STATE = null;        // last /api/career/state (or /advance) payload
let SQUAD = null;        // {squad, roles, role_defaults, tactic_keys} for the lineup editor
let XI = null;           // editable copy of the xi wire array
let TACTICS = null;      // editable copy of the tactics body

// ---------------- start screen ----------------
async function renderStart() {
  let teams = [];
  try { teams = await api('/api/team_options'); } catch { teams = []; }
  body().innerHTML = `<section class="card" style="padding:24px;max-width:480px">
      <h3 style="margin-top:0">Start a career</h3>
      <p class="muted">Pick a club. Your save forks from its real current standing and
        remaining fixtures — today's results, not a fresh slate.</p>
      <label style="display:block;margin:14px 0">Club
        <select id="crTeam" style="width:100%;margin-top:6px">
          ${teams.map((t) => `<option value="${esc(t.team)}">${esc(t.team)}</option>`).join('')}
        </select>
      </label>
      <button class="btn btn-primary" id="crStart">Start Career</button>
      <div id="crStartErr" class="muted" style="margin-top:10px;color:var(--red)"></div>
    </section>`;
  document.getElementById('crStart').onclick = async () => {
    const team = document.getElementById('crTeam').value;
    const btn = document.getElementById('crStart');
    btn.disabled = true; btn.textContent = 'Starting…';
    try {
      const res = await apiPost('/api/career/start', { team });
      if (!res.available) throw new Error(res.error || 'Could not start a career.');
      STATE = res; await renderDashboard();
    } catch (e) {
      document.getElementById('crStartErr').textContent = e.message;
      btn.disabled = false; btn.textContent = 'Start Career';
    }
  };
}

// ---------------- standings table ----------------
function standingsTable(rows, highlightId) {
  return `<div class="ltbl-wrap"><table class="ltbl"><thead><tr>
      <th>#</th><th>Team</th><th>Pld</th><th>GD</th><th>Pts</th><th>Form</th>
    </tr></thead><tbody>${rows.map((r, i) =>
      `<tr class="ltbl-row${r.team_id === highlightId ? ' ucl' : ''}" style="cursor:default">
        <td class="pos">${i + 1}</td>
        <td class="tcell"><span class="tn">${esc(r.team)}</span></td>
        <td>${r.played ?? 0}</td>
        <td class="${r.gd > 0 ? 'pos-n' : r.gd < 0 ? 'neg-n' : ''}">${r.gd > 0 ? '+' : ''}${r.gd}</td>
        <td><b>${r.pts}</b></td>
        <td class="formcell">${(r.form || []).map((f) =>
          `<span class="form-pill form-${f}">${f}</span>`).join('')}</td>
      </tr>`
    ).join('')}</tbody></table></div>`;
}

// ---------------- lineup editor ----------------
function roleOptions(family, selected) {
  const names = Object.keys((SQUAD.roles || {})[family] || {});
  return names.map((n) => `<option value="${esc(n)}"${n === selected ? ' selected' : ''}>${esc(n)}</option>`).join('');
}
function playerOptions(selectedName) {
  return (SQUAD.squad || []).map((p) =>
    `<option value="${esc(p.player)}"${p.player === selectedName ? ' selected' : ''}>${esc(p.player)} (${p.rating})</option>`
  ).join('');
}
function lineupHTML() {
  const rows = XI.map((slot, i) => {
    const name = slot.player ? slot.player.player : '';
    return `<div class="cr-slot">
        <span class="cr-slot-pos">${esc(slot.family)}</span>
        <select data-i="${i}" class="cr-slot-player">
          <option value="">— empty —</option>${playerOptions(name)}
        </select>
        <select data-i="${i}" class="cr-slot-role">${roleOptions(slot.family, slot.role)}</select>
      </div>`;
  }).join('');
  const sliders = (SQUAD.tactic_keys || []).map((k) => `<div class="tl-slider">
      <div class="tl-slabel"><span>${TACTIC_LABELS[k] || k}</span><b id="crVal_${k}">${TACTICS[k]}</b></div>
      <input type="range" min="0" max="100" value="${TACTICS[k]}" data-k="${k}" class="cr-tactic">
    </div>`).join('');
  return `<div class="cr-lineup">
      <div class="cr-lineup-xi">${rows}</div>
      <div class="cr-lineup-tac">${sliders}</div>
    </div>`;
}
function wireLineupEditor() {
  document.querySelectorAll('.cr-slot-player').forEach((sel) => {
    sel.onchange = () => {
      const i = +sel.dataset.i;
      const name = sel.value;
      const p = (SQUAD.squad || []).find((x) => x.player === name);
      XI[i].player = p ? { player: p.player, rating: p.rating, position: p.position,
        photo: p.photo, breakout: p.breakout } : null;
    };
  });
  document.querySelectorAll('.cr-slot-role').forEach((sel) => {
    sel.onchange = () => { XI[+sel.dataset.i].role = sel.value; };
  });
  document.querySelectorAll('.cr-tactic').forEach((inp) => {
    inp.oninput = () => {
      TACTICS[inp.dataset.k] = +inp.value;
      document.getElementById('crVal_' + inp.dataset.k).textContent = inp.value;
    };
  });
}

// ---------------- dashboard ----------------
async function renderDashboard() {
  const s = STATE;
  if (s.season_complete) { renderSeasonComplete(s); return; }

  // Seed the lineup editor from the last lineup played, or a fresh auto-XI the first time.
  if (!SQUAD) {
    let sq;
    try { sq = await api('/api/tactics/squad?team=' + encodeURIComponent(s.team)); }
    catch { sq = null; }
    if (!sq || !sq.available) {
      body().innerHTML = `<div class="empty-state">Could not load ${esc(s.team)}'s squad right now — try again shortly.</div>`;
      return;
    }
    SQUAD = sq;
    XI = s.last_xi || sq.xi;
    TACTICS = s.last_tactics || sq.tactic_defaults;
  }

  const nf = s.next_fixture;
  const fixtureCard = nf
    ? `<div class="card" style="padding:16px;margin-bottom:16px">
        <div class="muted">Matchday ${s.matchday + 1} of ${s.total_matchdays}</div>
        <h3 style="margin:4px 0">${nf.is_home ? esc(s.team) + ' vs ' + esc(nf.opponent)
                                               : esc(nf.opponent) + ' vs ' + esc(s.team)}</h3>
      </div>`
    : '';

  body().innerHTML = `
    <div class="cmp-board">
      <div>
        ${fixtureCard}
        <div class="card" style="padding:16px">
          <h3 style="margin-top:0">Lineup &amp; Tactics</h3>
          ${lineupHTML()}
          <button class="btn btn-primary" id="crPlay" style="margin-top:14px">▶ Play Matchday</button>
          <div id="crPlayErr" class="muted" style="margin-top:10px;color:var(--red)"></div>
        </div>
      </div>
      <div class="card" style="padding:16px">
        <h3 style="margin-top:0">${esc(s.league_key)}</h3>
        ${standingsTable(s.standings, s.team_id)}
        <button class="btn btn-ghost btn-sm" id="crDelete" style="margin-top:14px">Delete career &amp; restart</button>
      </div>
    </div>
    <div id="crResult"></div>`;
  wireLineupEditor();
  document.getElementById('crPlay').onclick = playMatchday;
  document.getElementById('crDelete').onclick = async () => {
    if (!confirm('Delete this career? This can’t be undone.')) return;
    await apiPost('/api/career/delete', {});
    STATE = null; SQUAD = null; XI = null; TACTICS = null;
    await renderStart();
  };
}

async function playMatchday() {
  const btn = document.getElementById('crPlay');
  btn.disabled = true; btn.textContent = 'Simulating…';
  let res;
  try {
    res = await apiPost('/api/career/advance', { xi: XI, tactics: TACTICS });
    if (!res.available) throw new Error(res.error || 'Could not play the matchday.');
  } catch (e) {
    document.getElementById('crPlayErr').textContent = e.message;
    btn.disabled = false; btn.textContent = '▶ Play Matchday';
    return;
  }
  renderResult(res);
}

// ---------------- result screen ----------------
function otherResultsHTML(others, standingsByIdBefore) {
  if (!others.length) return '';
  return `<div class="cr-others">${others.map((o) => {
    const h = (standingsByIdBefore[o.home_team_id] || {}).team || 'Home';
    const a = (standingsByIdBefore[o.away_team_id] || {}).team || 'Away';
    return `<div class="cr-other-row"><span>${esc(h)}</span><b>${o.home_goals}–${o.away_goals}</b><span>${esc(a)}</span></div>`;
  }).join('')}</div>`;
}

function renderResult(res) {
  const r = res.user_result;
  const byId = {}; (STATE.standings || []).forEach((row) => { byId[row.team_id] = row; });
  const evs = (r.events || []).length ? r.events.map((e) => `<div class="tl-ev ${esc(e.side)} ${esc(e.type)}">
        <span class="tl-evl">${e.side === 'home' ? evText(e) : ''}</span>
        <span class="tl-evm">${_EVICON[e.type] || '•'}<i>${esc(e.label)}</i></span>
        <span class="tl-evr">${e.side === 'away' ? evText(e) : ''}</span></div>`).join('')
    : '<div class="tl-noweak">A goalless, incident-free 90 minutes.</div>';
  const m = r.motm;
  const motm = m ? `<div class="tl-motm"><span class="tl-lph">${m.photo ? `<img src="${esc(m.photo)}" alt="" loading="lazy">` : ''}</span>
      <div><div class="tl-motmt">⭐ Man of the match</div>
        <b>${esc(m.player)}</b> <span class="muted">${m.goals ? `${m.goals} goal${m.goals > 1 ? 's' : ''}` : ''}${m.assists ? ` · ${m.assists} assist${m.assists > 1 ? 's' : ''}` : ''}</span></div></div>` : '';

  document.getElementById('crResult').innerHTML = `<section class="card" style="padding:16px;margin-top:16px">
      <div class="tl-mscore"><span class="tl-mteam you">${esc(r.home)}</span>
        <span class="tl-mnum">${r.score.home}<i>–</i>${r.score.away}</span>
        <span class="tl-mteam opp">${esc(r.away)}</span></div>
      <div class="tl-mstory">${esc(r.story || '')}</div>
      ${motm}
      <div class="tl-sechdr">⏱ Match events</div>
      <div class="tl-evs">${evs}</div>
      <div class="tl-sechdr">📊 Match stats</div>
      <div class="tl-mstats">${(r.stats || []).map(statRow).join('')}</div>
      <div class="tl-sechdr">Elsewhere this matchday</div>
      ${otherResultsHTML(res.other_results || [], byId)}
      <button class="btn btn-primary" id="crContinue" style="margin-top:16px">
        ${res.season_complete ? 'See final standings →' : 'Continue to next matchday →'}</button>
    </section>`;

  SQUAD = null; XI = null; TACTICS = null;     // re-seed from the freshly-played lineup next render
  document.getElementById('crContinue').onclick = async () => {
    // Re-fetch rather than patch STATE in place: simpler than threading the
    // just-played xi/tactics back into a synthetic state object, and this
    // button is clicked once per matchday, not a hot path.
    let st;
    try { st = await api('/api/career/state'); } catch { st = null; }
    if (!st || !st.available) { await renderStart(); return; }
    STATE = st;
    await renderDashboard();
  };
  document.getElementById('crResult').scrollIntoView({ behavior: 'smooth' });
}

function renderSeasonComplete(s) {
  body().innerHTML = `<section class="card" style="padding:24px">
      <h3 style="margin-top:0">🏆 Season complete</h3>
      <p class="muted">Final standing for ${esc(s.team)}.</p>
      ${standingsTable(s.standings, s.team_id)}
      <button class="btn btn-primary" id="crRestart" style="margin-top:16px">Start a new career</button>
    </section>`;
  document.getElementById('crRestart').onclick = async () => {
    await apiPost('/api/career/delete', {});
    STATE = null; SQUAD = null; XI = null; TACTICS = null;
    await renderStart();
  };
}

// ---------------- boot ----------------
async function boot() {
  await Auth.me();
  if (!Auth.user) {
    body().innerHTML = `<section class="card" style="padding:24px;max-width:480px">
        <h3 style="margin-top:0">Sign in to play</h3>
        <p class="muted">A career is a persistent save tied to your account.</p>
        <button class="btn btn-primary" id="crSignIn">Sign in</button>
      </section>`;
    document.getElementById('crSignIn').onclick = () => openAuthModal('login');
    return;
  }
  let st;
  try { st = await api('/api/career/state'); } catch { st = { available: false }; }
  if (!st.available) { await renderStart(); return; }
  STATE = st;
  await renderDashboard();
}
boot();
