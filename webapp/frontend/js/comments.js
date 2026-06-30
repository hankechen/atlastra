// Reusable comment thread widget.
//   mountComments('player:Lionel Messi', document.getElementById('comments'))
// Public read; posting / liking / deleting require a signed-in account (Auth).
// Threads are keyed by a free-form `target` string so this drops onto any page.
(function () {
  const escMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => escMap[c]);
  const nl2br = (s) => esc(s).replace(/\n/g, '<br>');

  async function mountComments(target, el, opts = {}) {
    if (!el || !target) return;
    if (Auth.user == null) { try { await Auth.me(); } catch { /* guest */ } }
    let sort = 'new';
    el.classList.add('card', 'cmts');
    el.innerHTML = `
      <div class="card-h">
        <h3>${esc(opts.title || 'Comments')} <span class="cmt-count" id="cmtCount"></span></h3>
        <div class="cmt-sorts" id="cmtSorts">
          <span class="cmt-sort active" data-s="new">Newest</span>
          <span class="cmt-sort" data-s="top">Top</span>
          <span class="cmt-sort" data-s="old">Oldest</span>
        </div>
      </div>
      <div id="cmtCompose"></div>
      <div id="cmtList" class="cmt-list"><div class="cmt-empty">Loading…</div></div>`;

    const $count = el.querySelector('#cmtCount');
    const $list = el.querySelector('#cmtList');
    const $compose = el.querySelector('#cmtCompose');

    function composer() {
      if (!Auth.user) {
        $compose.innerHTML = `<div class="cmt-signin">
          <span>Sign in to join the conversation.</span>
          <button class="btn btn-primary btn-sm" id="cmtSignin">Sign in</button></div>`;
        el.querySelector('#cmtSignin').onclick = () => openAuthModal();
        return;
      }
      $compose.innerHTML = `<div class="cmt-box">
        <span class="cmt-av">${avatarHTML(null, Auth.user.username)}</span>
        <div class="cmt-boxmain">
          <textarea id="cmtInput" rows="2" maxlength="1500"
            placeholder="Share your take${opts.subject ? ' on ' + esc(opts.subject) : ''}…"></textarea>
          <div class="cmt-actions">
            <span class="cmt-err" id="cmtErr"></span>
            <button class="btn btn-primary btn-sm" id="cmtPost">Post</button>
          </div>
        </div></div>`;
      const ta = el.querySelector('#cmtInput');
      const post = el.querySelector('#cmtPost');
      const err = el.querySelector('#cmtErr');
      const submit = async () => {
        const body = ta.value.trim();
        if (!body) return;
        post.disabled = true; err.textContent = '';
        try {
          await apiPost('/api/comments', { target, body });
          ta.value = '';
          await load();
        } catch (e) { err.textContent = e.message || 'Could not post.'; }
        finally { post.disabled = false; }
      };
      post.onclick = submit;
      ta.addEventListener('keydown', (e) => {            // Ctrl/Cmd+Enter to post
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
      });
    }

    function row(c) {
      return `<div class="cmt" data-id="${c.id}">
        <span class="cmt-av">${avatarHTML(null, c.username)}</span>
        <div class="cmt-main">
          <div class="cmt-head"><b>${esc(c.username)}</b>
            <span class="cmt-time">${timeAgo((c.created || 0) * 1000)}</span></div>
          <div class="cmt-body">${nl2br(c.body)}</div>
          <div class="cmt-foot">
            <button class="cmt-like${c.liked ? ' on' : ''}" data-act="like" data-id="${c.id}"
              title="Like">♥ <span>${c.likes || ''}</span></button>
            ${c.mine ? `<button class="cmt-del" data-act="del" data-id="${c.id}">Delete</button>` : ''}
          </div>
        </div></div>`;
    }

    async function load() {
      let data;
      try { data = await api(`/api/comments?target=${encodeURIComponent(target)}&sort=${sort}`); }
      catch { $list.innerHTML = `<div class="cmt-empty">Couldn't load comments.</div>`; return; }
      $count.textContent = data.total ? `(${data.total})` : '';
      $list.innerHTML = data.comments.length
        ? data.comments.map(row).join('')
        : `<div class="cmt-empty">No comments yet — be the first.</div>`;
    }

    // event delegation: like + delete
    $list.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      const id = +btn.dataset.id;
      if (btn.dataset.act === 'like') {
        if (!Auth.user) return openAuthModal();
        try {
          const r = await apiPost('/api/comments/like', { id });
          btn.classList.toggle('on', r.liked);
          btn.querySelector('span').textContent = r.likes || '';
        } catch (err) { if (/sign in/i.test(err.message)) openAuthModal(); }
      } else if (btn.dataset.act === 'del') {
        if (!confirm('Delete this comment?')) return;
        try {
          await apiPost('/api/comments/delete', { id });
          await load();
        } catch { /* ignore */ }
      }
    });

    el.querySelector('#cmtSorts').addEventListener('click', (e) => {
      const t = e.target.closest('.cmt-sort');
      if (!t) return;
      el.querySelectorAll('.cmt-sort').forEach((x) => x.classList.remove('active'));
      t.classList.add('active'); sort = t.dataset.s; load();
    });

    composer();
    await load();
  }

  window.mountComments = mountComments;
})();
