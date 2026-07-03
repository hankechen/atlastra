// Admin dashboard: usage rates, sign-ups, top pages, engagement.
// Data comes from /api/admin/overview (server-side admin-session gated). This page
// is not secret — a non-admin just gets a friendly "admins only" message.
renderSidebar('Dashboard');

const OUT = document.getElementById('adm-out');
let charts = [];

function cssv(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function fmt(n) { return (n ?? 0).toLocaleString(); }
function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}
function esc(s) { return (s ?? '').toString().replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function tile(icon, value, label) {
  return `<div class="tile"><div class="ic">${icon}</div><b>${value}</b><span>${label}</span></div>`;
}
function tblRows(rows) {
  return rows.length
    ? rows.map(r => `<tr><td class="p">${esc(r.path)}</td><td>${fmt(r.count)}</td></tr>`).join('')
    : `<tr><td class="p muted">No data yet</td><td>—</td></tr>`;
}

function render(d) {
  const u = d.users, t = d.traffic, c = d.content;
  OUT.innerHTML = `
    <div class="card-h" style="margin-bottom:12px"><h3>Traffic</h3></div>
    <div class="tiles" style="margin-bottom:18px">
      ${tile('👥', fmt(t.uniq_1d), 'Unique visitors · 24h')}
      ${tile('📈', fmt(t.hits_1d), 'Requests · 24h')}
      ${tile('📄', fmt(t.page_1d), 'Page views · 24h')}
      ${tile('🔌', fmt(t.api_1d), 'API calls · 24h')}
      ${tile('🗓️', fmt(t.uniq_7d), 'Unique visitors · 7d')}
      ${tile('∑', fmt(t.hits_total), 'Requests · all time')}
    </div>

    <div class="adm-grid" style="margin-bottom:18px">
      <div class="card"><div class="card-h"><h3>Requests · last 24 hours</h3></div>
        <div class="adm-chart"><canvas id="chHourly"></canvas></div></div>
      <div class="card"><div class="card-h"><h3>Requests · last 30 days</h3></div>
        <div class="adm-chart"><canvas id="chDaily"></canvas></div></div>
    </div>

    <div class="card-h" style="margin-bottom:12px"><h3>Accounts</h3></div>
    <div class="tiles" style="margin-bottom:18px">
      ${tile('🧑', fmt(u.total), 'Registered users')}
      ${tile('🟢', fmt(u.active_sessions), 'Active sessions')}
      ${tile('✨', fmt(u.new_1d), 'New · 24h')}
      ${tile('📅', fmt(u.new_7d), 'New · 7d')}
      ${tile('🔑', fmt(u.password), 'Password logins')}
      ${tile('🇬', fmt(u.google), 'Google logins')}
    </div>

    <div class="adm-grid" style="margin-bottom:18px">
      <div class="card"><div class="card-h"><h3>Sign-ups · last 30 days</h3></div>
        <div class="adm-chart"><canvas id="chSignup"></canvas></div></div>
      <div class="card"><div class="card-h"><h3>Newest users</h3></div>
        <div class="adm-list">${
          u.recent.length ? u.recent.map(r => `
            <div class="adm-u"><span class="who">
              <b>${esc(r.username)}</b>
              ${r.admin ? '<span class="tag a">admin</span>' : ''}
              ${r.google ? '<span class="tag g">google</span>' : ''}
            </span><span class="when">${ago(r.created)}</span></div>`).join('')
          : '<div class="adm-err">No users yet</div>'
        }</div></div>
    </div>

    <div class="adm-grid" style="margin-bottom:18px">
      <div class="card"><div class="card-h"><h3>Top pages · 30d</h3></div>
        <table class="adm-tbl"><tbody>${tblRows(t.top_pages)}</tbody></table></div>
      <div class="card"><div class="card-h"><h3>Top API endpoints · 30d</h3></div>
        <table class="adm-tbl"><tbody>${tblRows(t.top_api)}</tbody></table></div>
    </div>

    <div class="card-h" style="margin-bottom:12px"><h3>Engagement</h3></div>
    <div class="tiles" style="margin-bottom:18px">
      ${tile('💬', fmt(c.comments), 'Comments')}
      ${tile('🆕', fmt(c.comments_7d), 'Comments · 7d')}
      ${tile('🎮', fmt(c.scores), 'Game scores')}
      ${tile('🏆', fmt(u.admins), 'Admins')}
    </div>
    <div class="card"><div class="card-h"><h3>Games played</h3></div>
      <table class="adm-tbl"><tbody>${
        c.games.length ? c.games.map(g => `<tr><td class="p">${esc(g.game)}</td>
          <td>${fmt(g.plays)} plays · ${fmt(g.players)} players</td></tr>`).join('')
        : '<tr><td class="p muted">No games played yet</td><td>—</td></tr>'
      }</tbody></table></div>`;

  drawCharts(d);
}

function drawCharts(d) {
  charts.forEach(ch => ch.destroy());
  charts = [];
  const accent = cssv('--accent') || '#5570f0';
  const grid = cssv('--line') || 'rgba(150,158,178,.22)';
  const muted = cssv('--muted') || '#9aa4ba';
  const green = cssv('--green') || '#34c46a';

  const bar = (id, labels, data, color) => {
    const el = document.getElementById(id);
    if (!el) return;
    charts.push(new Chart(el, {
      type: 'bar',
      data: { labels, datasets: [{ data, backgroundColor: color, borderRadius: 4,
        maxBarThickness: 26 }] },
      options: {
        maintainAspectRatio: false, plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, font: { size: 10 },
               maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: { beginAtZero: true, grid: { color: grid }, ticks: { color: muted,
               precision: 0, maxTicksLimit: 5 } },
        },
      },
    }));
  };

  const h = d.traffic.hourly_series;
  bar('chHourly', h.map(x => x.hours_ago === 0 ? 'now' : `-${x.hours_ago}h`),
      h.map(x => x.count), accent);

  const dd = d.traffic.daily_series;
  bar('chDaily', dd.map(x => x.days_ago === 0 ? 'today' : `-${x.days_ago}d`),
      dd.map(x => x.count), accent);

  const s = d.users.signups_series;
  bar('chSignup', s.map(x => x.days_ago === 0 ? 'today' : `-${x.days_ago}d`),
      s.map(x => x.count), green);
}

async function load() {
  try {
    render(await api('/api/admin/overview'));
  } catch (e) {
    OUT.innerHTML = `<div class="adm-err">${
      /admin/i.test(e.message) ? '🔒 Admins only. Sign in with an admin account to view this dashboard.'
                               : 'Couldn’t load dashboard: ' + esc(e.message)}</div>`;
  }
}

load();
setInterval(load, 30000);   // live-ish refresh
