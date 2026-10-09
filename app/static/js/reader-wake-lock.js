// PR 338: «Не гасить экран при чтении» - the keepScreenOn reader setting (reader-settings.js,
// off by default; switches in /settings/reading and the reader's Aa panel).
//
// On a chapter page, with the setting on and the tab visible, it holds a screen Wake Lock
// (navigator.wakeLock.request("screen")). The system drops the lock whenever the tab is
// hidden or the screen is locked, so it's asked for again once the tab is visible; it's
// let go when the setting goes off and when the reader is left (pagehide), and taken again
// if the page comes back from the back/forward cache. Where the API is missing or the
// request is refused (battery saver, a page not focused) nothing happens - the screen
// simply dims as usual; the switch's hint ([data-role="keep-screen-on-unsupported"]) says
// so on browsers without the API.
(() => {
  if (window.readerWakeLock) return;
  const supported = "wakeLock" in navigator && typeof navigator.wakeLock?.request === "function";

  for (const hint of document.querySelectorAll('[data-role="keep-screen-on-unsupported"]')) {
    hint.hidden = supported;
  }

  const reader = document.querySelector('[data-role="chapter"]');
  if (!supported || !reader) {
    window.readerWakeLock = { supported, held: () => false };
    return;
  }

  let sentinel = null;
  let requesting = null;
  let leaving = false;

  function enabled() {
    try {
      return Boolean(window.readerSettings?.get().keepScreenOn);
    } catch {
      return false;
    }
  }

  const wanted = () => !leaving && enabled() && document.visibilityState === "visible";

  async function acquire() {
    if (sentinel?.released) sentinel = null; // dropped by the system, no event seen
    if (sentinel || requesting || !wanted()) return;
    requesting = (async () => {
      try {
        const lock = await navigator.wakeLock.request("screen");
        lock.addEventListener?.("release", () => {
          if (sentinel === lock) sentinel = null;
        });
        sentinel = lock;
        // Turned off, hidden or left while the request was pending.
        if (!wanted()) await release();
      } catch {
        // refused or not allowed right now - the screen dims as usual
      }
    })();
    try {
      await requesting;
    } finally {
      requesting = null;
    }
  }

  async function release() {
    const lock = sentinel;
    sentinel = null;
    if (!lock) return;
    try {
      await lock.release();
    } catch {
      // already released by the system
    }
  }

  function update() {
    return wanted() ? acquire() : release();
  }

  document.addEventListener("visibilitychange", update);
  document.addEventListener("reader-settings:change", (event) => {
    const key = event.detail?.key;
    if (key === "keepScreenOn" || key === null) update();
  });
  // The setting changed in another tab (reader-settings.js has reloaded it by now).
  window.addEventListener("storage", (event) => {
    if (event.key === "readerSettings") update();
  });
  window.addEventListener("pagehide", () => {
    leaving = true;
    release();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    leaving = false;
    update();
  });

  window.readerWakeLock = { supported, held: () => sentinel !== null, update };
  update();
})();
