// Wave 35 (PR 288): sends the reading position inside the chapter to
// POST /reading-progress/tick, so another device opening this chapter picks it up
// (data-saved-paragraph, PR 287). tap-to-read.js and reader-progress.js announce every
// write of their localStorage entry as a "reader:position" event ({revealed, total});
// this only listens - neither reader script talks to the server itself.
//
// Throttled like activity-heartbeat.js keeps its own tick cheap: the first move after a
// quiet spell goes out right away, anything within THROTTLE_MS of the last request is
// folded into one trailing request carrying the latest position - a quick run of taps
// is one or two requests, not one per tap. A pending position is flushed when the page
// is hidden or left, so the last few paragraphs before switching devices aren't lost.
//
// Loaded for logged-in readers only (chapter.html) - a guest has nothing to sync.
(() => {
  const THROTTLE_MS = 5000;

  const article = document.querySelector('[data-role="chapter"]');
  if (!article) return;
  const { slugUrl, volume, number } = article.dataset;
  if (!slugUrl || !volume || !number) return;

  // What the server already holds for this chapter - re-announcing it (e.g. a server win
  // being copied into localStorage on load) isn't a change worth a request.
  let lastSent = Number(article.dataset.savedParagraph) || 0;
  let lastSentAt = 0;
  let pending = null;
  let timer = null;

  function send() {
    timer = null;
    const position = pending;
    pending = null;
    if (!position || position.revealed === lastSent) return;
    lastSent = position.revealed;
    lastSentAt = Date.now();
    fetch("/reading-progress/tick", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        slug_url: slugUrl,
        volume,
        number,
        paragraph: String(position.revealed),
        paragraph_total: String(position.total),
      }),
      keepalive: true,
    }).catch(() => {});
  }

  function flush() {
    if (timer === null) return;
    clearTimeout(timer);
    send();
  }

  document.addEventListener("reader:position", (event) => {
    const { revealed, total } = event.detail || {};
    if (!Number.isInteger(revealed) || !Number.isInteger(total) || revealed < 1) return;
    if (revealed > total) return;
    pending = { revealed, total };
    if (timer !== null) return;
    timer = setTimeout(send, Math.max(0, lastSentAt + THROTTLE_MS - Date.now()));
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) flush();
  });
  window.addEventListener("pagehide", flush);
})();
