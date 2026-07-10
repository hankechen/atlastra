// The Feed — a global social timeline. Reuses the comment system's backend
// (auth-gated posts, likes, delete, rate-limiting) with a single global thread.
renderSidebar('feed');
attachSearchDropdown(document.getElementById('searchBox'));

(async () => {
  await mountComments('feed:global', document.getElementById('feed'), {
    title: 'Timeline',
    placeholder: "What's happening in football?",
  });
})();

// keep the feed fresh: re-pull the timeline every 30s while the tab is visible,
// unless the composer is mid-type (don't clobber a draft)
setInterval(() => {
  if (document.hidden) return;
  const ta = document.getElementById('cmtInput');
  if (ta && ta.value.trim()) return;
  const newest = document.querySelector('#cmtSorts .cmt-sort.active');
  if (newest && newest.dataset.s === 'new') newest.click();   // reloads the list
}, 30000);
