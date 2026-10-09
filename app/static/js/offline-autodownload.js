// PR 334: auto-download of the next chapters for a title that's already downloaded for
// reading without a network (PR 330). While a chapter of such a title is read online, the
// next N chapters (readerSettings.autoDownloadNext: 0 - off, the default - or 3/5/10) are
// kept on the device, so the downloaded set moves along with the reader. Titles that
// aren't downloaded never download themselves.
//
// Only with the network allowed: Wi-Fi/ethernet where the browser says
// (navigator.connection.type); where it doesn't (iOS, desktop), only once the visitor
// said «на любой сети» (autoDownloadAnyNetwork, asked on /settings/reading); never with
// «Экономия трафика» (saveData).
//
// The same queue as the manager (offline-queue.js): strictly one chapter at a time, and
// one tab at a time (Web Locks, where available). A 429/503 stops it and holds off the
// next attempts for a while (COOLDOWN_MS) - no retrying on our own; a nearly full storage
// (PR 333's 80%) doesn't start it, running out of space stops it. Starts a few seconds
// after the chapter opens and only when the browser is idle; nothing on the page changes.
//
// Chapters with several translations take the «Вариант N» the visitor chose in the
// manager (offlineStore title.translationVariant); without one they're skipped - the site
// never picks a translation for the visitor.
//
// On /settings/reading the same file shows what the network allows right now.
(() => {
  const SETTINGS_KEY = "readerSettings";
  const COOLDOWN_KEY = "offlineAutoPausedUntil";
  const COOLDOWN_MS = { "rate-limit": 15 * 60 * 1000, blocked: 30 * 60 * 1000, quota: 60 * 60 * 1000 };
  const START_DELAY_MS = 5000;
  const CHOICES = new Set([0, 3, 5, 10]);
  const ALLOWED_TYPES = new Set(["wifi", "ethernet"]);
  const LOCK = "wn-offline-autodownload";

  function settings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {};
    } catch {
      return {};
    }
  }

  function count(value) {
    const n = Number(value);
    return CHOICES.has(n) ? n : 0;
  }

  // What the network allows: { ok, reason } - reason "save-data", "cellular" (a known
  // type that isn't Wi-Fi/ethernet), "unknown" (the browser doesn't say and the visitor
  // hasn't allowed any network) or "wifi"/"any" when ok.
  function networkStatus(connection, anyNetwork) {
    if (connection?.saveData) return { ok: false, reason: "save-data" };
    const type = connection?.type;
    if (type && type !== "unknown") {
      return ALLOWED_TYPES.has(type) ? { ok: true, reason: "wifi" } : { ok: false, reason: "cellular" };
    }
    return anyNetwork ? { ok: true, reason: "any" } : { ok: false, reason: "unknown" };
  }

  const key = (volume, number) => `${volume}--${number}`;

  // The next `n` chapters after the current one, in the SDK's order (`toc`: chapters with
  // volume/number), minus those already on the device. Empty when the current chapter
  // isn't in `toc`.
  function plan(toc, current, saved, n) {
    const index = toc.findIndex((c) => c.volume === current.volume && c.number === current.number);
    if (index < 0 || n < 1) return [];
    return toc.slice(index + 1, index + 1 + n).filter((c) => !saved.has(key(c.volume, c.number)));
  }

  // A chapter as the queue takes it - or null for one with several translations and no
  // «Вариант N» to follow.
  function queueItem(chapter, variant) {
    const branches = chapter.branches || [];
    let branchId = null;
    if (branches.length > 1) {
      if (variant == null || !branches[variant]) return null;
      branchId = branches[variant].branch_id;
    }
    return { volume: chapter.volume, number: chapter.number, name: chapter.name, branchId };
  }

  function coolingDown(now) {
    try {
      return Number(localStorage.getItem(COOLDOWN_KEY) || 0) > now;
    } catch {
      return false;
    }
  }

  function coolDown(reason, now) {
    if (!COOLDOWN_MS[reason]) return;
    try {
      localStorage.setItem(COOLDOWN_KEY, String(now + COOLDOWN_MS[reason]));
    } catch {
      // no storage: the next chapter page may try again
    }
  }

  const flatten = (manifest) => manifest.volumes.flatMap((volume) => volume.chapters);

  // Everything one chapter page does. `env` carries the browser APIs, so tests can run it
  // without a browser (tests/js/offline_autodownload_harness.mjs).
  async function runFor(current, env) {
    const { store, OfflineQueue, fetch, connection, now } = env;
    const own = settings();
    const n = count(own.autoDownloadNext);
    if (!n || !store?.supported()) return { skipped: "off" };
    const network = networkStatus(connection, own.autoDownloadAnyNetwork === true);
    if (!network.ok) return { skipped: network.reason };
    if (coolingDown(now())) return { skipped: "cooldown" };

    const title = (await store.listTitles()).find((t) => t.slug === current.slug);
    if (!title) return { skipped: "not-downloaded" };
    const storage = await store.storageState();
    if (storage?.nearlyFull) return { skipped: "storage" };

    const saved = await store.savedKeys(current.slug);
    // The order kept with the title (PR 331) answers "is anything missing?" without a
    // request; the manifest (fresh order, translations) only when something is, or when
    // the kept order may have ended before new chapters came out.
    const kept = (title.toc || []).map(([volume, number]) => ({ volume, number }));
    const keptIndex = kept.findIndex((c) => c.volume === current.volume && c.number === current.number);
    if (keptIndex >= 0 && keptIndex + n < kept.length && !plan(kept, current, saved, n).length) {
      return { skipped: "up-to-date" };
    }

    const response = await fetch(`/offline/titles/${encodeURIComponent(current.slug)}/manifest?size=false`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      coolDown({ 429: "rate-limit", 503: "blocked" }[response.status], now());
      return { skipped: `manifest-${response.status}` };
    }
    const chapters = flatten(await response.json());
    const items = plan(chapters, current, saved, n)
      .map((chapter) => queueItem(chapter, title.translationVariant))
      .filter(Boolean);
    if (!items.length) return { skipped: "nothing-to-download" };

    // What saveChapter() keeps with the title - the fresh order included.
    const titleRecord = {
      slug: current.slug,
      name: title.name,
      cover: title.cover,
      toc: chapters.map((c) => [c.volume, c.number]),
    };
    const queue = new OfflineQueue(items, {
      download: (item, signal) => OfflineQueue.downloadChapter(titleRecord, item, signal),
      onChange: (q) => {
        if (q.state === "paused") {
          coolDown(q.pauseReason, now());
          q.cancel();
        }
      },
    });
    await queue.start();
    return { downloaded: queue.completed, total: items.length, state: queue.state, pauseReason: queue.pauseReason };
  }

  // One tab at a time; another tab already at it - this one leaves it be.
  function exclusively(work) {
    if (!navigator.locks?.request) return work();
    return navigator.locks.request(LOCK, { ifAvailable: true }, (lock) => (lock ? work() : null));
  }

  window.offlineAuto = { networkStatus, plan, queueItem, runFor, count };

  // --- the chapter page ----------------------------------------------------------------
  const chapter = document.querySelector('[data-role="chapter"]');
  if (chapter && chapter.dataset.offlineCopy !== "1") {
    const { slugUrl, volume, number } = chapter.dataset;
    const start = () =>
      exclusively(() =>
        runFor(
          { slug: slugUrl, volume, number },
          {
            store: window.offlineStore,
            OfflineQueue: window.OfflineQueue,
            fetch: (...args) => window.fetch(...args),
            connection: navigator.connection,
            now: () => Date.now(),
          },
        ),
      ).catch(() => {});
    const whenIdle = (fn) =>
      "requestIdleCallback" in window ? requestIdleCallback(fn, { timeout: 10000 }) : setTimeout(fn, 0);
    const begin = () => setTimeout(() => whenIdle(start), START_DELAY_MS);
    if (count(settings().autoDownloadNext)) {
      if (!document.hidden) begin();
      else {
        const onVisible = () => {
          if (document.hidden) return;
          document.removeEventListener("visibilitychange", onVisible);
          begin();
        };
        document.addEventListener("visibilitychange", onVisible);
      }
    }
  }

  // --- /settings/reading ---------------------------------------------------------------
  const panel = document.querySelector('[data-role="offline-auto-settings"]');
  if (panel) {
    const statusText = panel.querySelector('[data-role="offline-auto-network"]');
    const askRow = panel.querySelector('[data-role="offline-auto-any-network"]');
    const TEXTS = {
      wifi: "Сейчас Wi-Fi — главы будут докачиваться.",
      any: "Тип сети браузер не сообщает — докачиваем на любой сети, как вы разрешили.",
      cellular: "Сейчас не Wi-Fi — главы докачаются, когда подключитесь к Wi-Fi.",
      "save-data": "Включена экономия трафика — автоскачивание не работает.",
      unknown: "Браузер не сообщает, Wi-Fi это или мобильная сеть.",
    };
    const render = () => {
      const own = settings();
      const on = count(own.autoDownloadNext) > 0;
      const connection = navigator.connection;
      const typeKnown = Boolean(connection?.type && connection.type !== "unknown");
      const status = networkStatus(connection, own.autoDownloadAnyNetwork === true);
      statusText.hidden = !on;
      statusText.textContent = TEXTS[status.reason];
      // Asked once, where it's needed: the browser can't tell Wi-Fi from mobile data.
      askRow.hidden = !on || typeKnown || Boolean(connection?.saveData);
    };
    render();
    document.addEventListener("reader-settings:change", render);
    navigator.connection?.addEventListener?.("change", render);
  }
})();
