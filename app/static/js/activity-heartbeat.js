// Sends a lightweight "still reading" tick to POST /activity/heartbeat every INTERVAL_MS,
// but only while the chapter page's tab is visible - a hidden/backgrounded tab isn't
// "active time" (see app/api/activity.py, app/db/activity.py).
(() => {
  const INTERVAL_MS = 30000;

  const article = document.querySelector('[data-role="chapter"]');
  if (!article) return;
  const slugUrl = article.dataset.slugUrl;
  if (!slugUrl) return;

  setInterval(() => {
    if (document.hidden) return;
    fetch("/activity/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ slug_url: slugUrl, seconds: String(INTERVAL_MS / 1000) }),
      keepalive: true,
    }).catch(() => {});
  }, INTERVAL_MS);
})();
