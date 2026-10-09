// Sends a lightweight "still reading" tick to POST /activity/heartbeat every INTERVAL_MS,
// but only while the chapter page's tab is visible - a hidden/backgrounded tab isn't
// "active time" (see app/api/activity.py, app/db/activity.py). PR 332: through the
// device's queue (sync-queue.js) - read offline, the ticks wait there for the network,
// each with its own id so a resend isn't counted twice.
(() => {
  const INTERVAL_MS = 30000;

  const article = document.querySelector('[data-role="chapter"]');
  if (!article) return;
  const slugUrl = article.dataset.slugUrl;
  if (!slugUrl) return;

  setInterval(() => {
    if (document.hidden) return;
    window.syncQueue?.send("/activity/heartbeat", {
      slug_url: slugUrl,
      seconds: String(INTERVAL_MS / 1000),
    });
  }, INTERVAL_MS);
})();
