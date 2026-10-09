// PR 330: what's downloaded for reading without a network, kept only on this device.
//
//   Cache Storage "wn-offline" - the bytes: each chapter's reader page (PR 331, GET
//     /offline/titles/{slug}/chapters/{volume}/{number}/page) under the reader's own URL,
//     /titles/{slug}/chapters/{volume}/{number} without a query - what the service worker
//     answers that URL with when there's no network - and every image it points at
//     (/images/view?url=...), plus the title's cover the same way.
//   IndexedDB "wn-offline" - the index: titles (slug, name, cover, dates) and chapters
//     (slug, volume, number, name, branch, bytes, images, date), for «Скачано», sizes and
//     "is this one already here?".
//
// The service worker (PR 328) only ever clears its own "wn-static-*" caches, so this one
// survives worker updates. Nothing here talks to ranobelib.me: chapters come through this
// site's own endpoints, one at a time (offline-queue.js).
(() => {
  // PR 333: base.html also loads this on every signed-in page (logout asks what to do
  // with the downloads) - a page that loads it itself as well gets the same one.
  if (window.offlineStore) return;
  const DB_NAME = "wn-offline";
  const CACHE_NAME = "wn-offline";

  // One function per schema version: MIGRATIONS[n] takes an IndexedDB at version n to
  // n + 1. Never edit a shipped step - append a new one (a device may still be at any
  // older version).
  const MIGRATIONS = [
    (db) => {
      db.createObjectStore("titles", { keyPath: "slug" });
      const chapters = db.createObjectStore("chapters", { keyPath: ["slug", "volume", "number"] });
      chapters.createIndex("slug", "slug");
    },
  ];
  const DB_VERSION = MIGRATIONS.length;

  function upgrade(db, oldVersion) {
    for (let version = oldVersion; version < MIGRATIONS.length; version += 1) {
      MIGRATIONS[version](db);
    }
  }

  const supported = () => "indexedDB" in window && "caches" in window;

  let opening = null;
  function openDb() {
    opening ||= new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = (event) => upgrade(request.result, event.oldVersion);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return opening;
  }

  const done = (request) =>
    new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });

  const finished = (tx) =>
    new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });

  const chapterUrl = (slug, volume, number) =>
    `/offline/titles/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(volume)}/${encodeURIComponent(number)}`;

  // Where a chapter's page is kept: the reader URL itself (the worker looks it up by path).
  const readerUrl = (slug, volume, number) =>
    `/titles/${encodeURIComponent(slug)}/chapters/${encodeURIComponent(volume)}/${encodeURIComponent(number)}`;

  const coverUrl = (url) => (url ? `/images/view?url=${encodeURIComponent(url)}` : null);

  async function chaptersOf(slug) {
    const db = await openDb();
    return done(db.transaction("chapters").objectStore("chapters").index("slug").getAll(slug));
  }

  // Volume--number keys of the chapters of `slug` already on the device.
  async function savedKeys(slug) {
    return new Set((await chaptersOf(slug)).map((chapter) => `${chapter.volume}--${chapter.number}`));
  }

  // Stores one downloaded chapter: `fragment` is the PR 329 JSON (what the index needs),
  // `page` the reader page's HTML (PR 331), `images` the [{ url, response }] of those of
  // fragment.images that could be fetched (images are best effort, as in the SDK's own
  // exports). Returns the bytes it took.
  async function saveChapter(title, fragment, page, images) {
    const cache = await caches.open(CACHE_NAME);
    const body = new Blob([page], { type: "text/html; charset=utf-8" });
    let bytes = body.size;
    const stored = [];
    for (const { url, response } of images) {
      const blob = await response.blob();
      bytes += blob.size;
      await cache.put(url, new Response(blob, { headers: response.headers }));
      stored.push(url);
    }
    await cache.put(
      readerUrl(fragment.slug_url, fragment.volume, fragment.number),
      new Response(body, { headers: { "Content-Type": "text/html; charset=utf-8" } }),
    );

    const db = await openDb();
    const tx = db.transaction(["titles", "chapters"], "readwrite");
    const titles = tx.objectStore("titles");
    const now = new Date().toISOString();
    const existing = await done(titles.get(fragment.slug_url));
    titles.put({
      // What else the title keeps - when it was last opened and which chapter (PR 335/
      // 336, touchTitle) - stays: downloading the next chapters doesn't open the title.
      ...existing,
      slug: fragment.slug_url,
      name: title.name || existing?.name || fragment.slug_url,
      cover: title.cover || existing?.cover || null,
      // PR 331: the title's chapter order from the manifest ([volume, number] in SDK
      // order) - how the offline page lists what's downloaded without sorting numbers.
      toc: title.toc || existing?.toc || null,
      // PR 334: the «Вариант N» picked for chapters with several translations, for
      // auto-download to follow (never picked for the visitor - null: none asked yet).
      translationVariant: title.translationVariant ?? existing?.translationVariant ?? null,
      savedAt: existing?.savedAt || now,
      updatedAt: now,
    });
    tx.objectStore("chapters").put({
      slug: fragment.slug_url,
      volume: fragment.volume,
      number: fragment.number,
      name: fragment.name,
      branchId: fragment.branch_id,
      bytes,
      images: stored,
      savedAt: now,
    });
    await finished(tx);
    return bytes;
  }

  // The cover through the same-origin proxy, so «Скачано» shows it offline too. Best
  // effort: a missing cover never fails a download.
  async function saveCover(url) {
    const proxied = coverUrl(url);
    if (!proxied) return null;
    try {
      const cache = await caches.open(CACHE_NAME);
      if (!(await cache.match(proxied))) {
        const response = await fetch(proxied);
        if (response.ok) await cache.put(proxied, response);
      }
    } catch {
      // keep going without it
    }
    return proxied;
  }

  // «Скачано»: every title with its chapter count and size, newest first.
  async function listTitles() {
    const db = await openDb();
    const tx = db.transaction(["titles", "chapters"]);
    const [titles, chapters] = await Promise.all([
      done(tx.objectStore("titles").getAll()),
      done(tx.objectStore("chapters").getAll()),
    ]);
    return titles
      .map((title) => {
        const own = chapters.filter((chapter) => chapter.slug === title.slug);
        return {
          ...title,
          chapters: own.length,
          bytes: own.reduce((sum, chapter) => sum + (chapter.bytes || 0), 0),
        };
      })
      .filter((title) => title.chapters > 0)
      .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  }

  // Removes a title's chapters, their images and its cover from the device. An image
  // another title's chapter still uses stays.
  async function deleteTitle(slug) {
    await deleteChapters(slug, null);
  }

  // PR 335: removes some of a title's chapters (`keys`: volume--number; null - all of
  // them) with their images - an image another chapter still uses stays. With no chapter
  // left the title and its cover go too. Returns { chapters, bytes } freed.
  async function deleteChapters(slug, keys) {
    const db = await openDb();
    const all = await done(db.transaction("chapters").objectStore("chapters").getAll());
    const goes = (chapter) =>
      chapter.slug === slug && (keys === null || keys.has(`${chapter.volume}--${chapter.number}`));
    const gone = all.filter(goes);
    const stays = all.filter((chapter) => !goes(chapter));
    const keep = new Set(stays.flatMap((chapter) => chapter.images || []));
    const lastOne = !stays.some((chapter) => chapter.slug === slug);
    const title = await done(db.transaction("titles").objectStore("titles").get(slug));

    const cache = await caches.open(CACHE_NAME);
    for (const chapter of gone) {
      await cache.delete(readerUrl(slug, chapter.volume, chapter.number));
      // PR 330 kept the JSON fragment here instead of the page.
      await cache.delete(chapterUrl(slug, chapter.volume, chapter.number));
      for (const image of chapter.images || []) if (!keep.has(image)) await cache.delete(image);
    }
    if (lastOne && title?.cover) await cache.delete(title.cover);

    const tx = db.transaction(["titles", "chapters"], "readwrite");
    if (lastOne) tx.objectStore("titles").delete(slug);
    for (const chapter of gone) tx.objectStore("chapters").delete([slug, chapter.volume, chapter.number]);
    await finished(tx);
    return { chapters: gone.length, bytes: gone.reduce((sum, chapter) => sum + (chapter.bytes || 0), 0) };
  }

  // PR 335: the chapters read already - those more than `keep` chapters behind `current`
  // in the title's order (`toc`: [volume, number] in SDK order, kept with the title).
  // Never `current` itself or anything after it; nothing when `current` isn't in `toc`
  // (no telling what's behind it).
  function readKeys(toc, current, keep) {
    const index = (toc || []).findIndex(([volume, number]) => volume === current.volume && number === current.number);
    if (index < 0) return new Set();
    return new Set(toc.slice(0, Math.max(0, index - keep)).map(([volume, number]) => `${volume}--${number}`));
  }

  // The chapter of `slug` opened last on this device (reader-offline.js) - the reader's
  // «current» one - or null.
  function lastOpened(slug) {
    try {
      const value = localStorage.getItem(`readerLastChapter:${slug}`);
      if (!value) return null;
      const [volume, number] = value.split("--");
      return { volume, number };
    } catch {
      return null;
    }
  }

  // PR 335: the chapters of `slug` on the device that are read - see readKeys().
  // `current` defaults to the chapter opened last; none known - nothing is read.
  async function readChapters(slug, keep = 0, current = lastOpened(slug)) {
    if (!current) return [];
    const db = await openDb();
    const title = await done(db.transaction("titles").objectStore("titles").get(slug));
    const keys = readKeys(title?.toc, current, keep);
    return (await chaptersOf(slug)).filter((chapter) => keys.has(`${chapter.volume}--${chapter.number}`));
  }

  // PR 335: frees what's read of a title. Returns { chapters, bytes } freed.
  async function deleteRead(slug, keep = 0, current = lastOpened(slug)) {
    const read = await readChapters(slug, keep, current);
    if (!read.length) return { chapters: 0, bytes: 0 };
    return deleteChapters(slug, new Set(read.map((chapter) => `${chapter.volume}--${chapter.number}`)));
  }

  // PR 335: the device's offline settings («Офлайн» in the settings) - not personal, they
  // stay through a logout. cleanBehind: chapters kept behind the one being read before the
  // older ones are removed (0 - off); limitMb: the most downloads may take (0 - no limit).
  const SETTINGS_KEY = "offlineSettings";
  const CLEAN_CHOICES = [0, 5, 10, 25];
  const LIMIT_CHOICES = [0, 100, 250, 500, 1000];
  function settings() {
    let stored = {};
    try {
      stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {};
    } catch {
      // no storage, or garbage in it: the defaults
    }
    const pick = (value, choices) => (choices.includes(Number(value)) ? Number(value) : 0);
    return { cleanBehind: pick(stored.cleanBehind, CLEAN_CHOICES), limitMb: pick(stored.limitMb, LIMIT_CHOICES) };
  }

  // PR 335: how much of the limit (settings().limitMb) the downloads take - their own
  // bytes in the index, not estimate(): the limit is the app's, not the browser's. Past
  // it auto-download stops (offline-autodownload.js); null when there's no limit.
  async function limitState() {
    const { limitMb } = settings();
    if (!limitMb) return null;
    const db = await openDb();
    const chapters = await done(db.transaction("chapters").objectStore("chapters").getAll());
    const used = chapters.reduce((sum, chapter) => sum + (chapter.bytes || 0), 0);
    const limit = limitMb * 1024 * 1024;
    return { used, limit, percent: Math.min(100, Math.round((used / limit) * 100)), reached: used >= limit };
  }

  function saveSettings(changes) {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...settings(), ...changes }));
    } catch {
      // no storage: the defaults again next time
    }
  }

  // PR 335: a chapter of a downloaded title was opened (online or from the copy) - when,
  // for «Офлайн» in the settings. A title that isn't downloaded is left alone.
  // PR 336: and which chapter ({ volume, number }) - where «Продолжить чтение» (/continue)
  // leads without a network (the service worker reads it).
  async function touchTitle(slug, now = new Date(), chapter = null) {
    const db = await openDb();
    const tx = db.transaction("titles", "readwrite");
    const titles = tx.objectStore("titles");
    const title = await done(titles.get(slug));
    if (title) {
      const opened = { ...title, openedAt: now.toISOString() };
      if (chapter) opened.openedChapter = { volume: String(chapter.volume), number: String(chapter.number) };
      titles.put(opened);
    }
    await finished(tx);
  }

  // PR 335: when a title was opened says what someone reads - it goes with the account's
  // other personal data (device-account.js), even when the downloads stay. So does which
  // chapter (PR 336).
  async function forgetOpened() {
    const db = await openDb();
    const tx = db.transaction("titles", "readwrite");
    const titles = tx.objectStore("titles");
    for (const title of await done(titles.getAll())) {
      if (!("openedAt" in title) && !("openedChapter" in title)) continue;
      const { openedAt, openedChapter, ...rest } = title;
      titles.put(rest);
    }
    await finished(tx);
  }

  // PR 333: «Очистить офлайн-данные» and logout without «Оставить скачанное» - every
  // downloaded chapter, image and cover, and the whole index.
  async function clearAll() {
    await caches.delete(CACHE_NAME);
    const db = await openDb();
    const tx = db.transaction(["titles", "chapters"], "readwrite");
    tx.objectStore("titles").clear();
    tx.objectStore("chapters").clear();
    await finished(tx);
  }

  // PR 333: how full the storage the browser gives this site is - everything it keeps
  // here (downloads, the app's own files), as navigator.storage.estimate() sees it.
  // `nearlyFull` past NEARLY_FULL: downloads may start failing soon. null when unknown.
  const NEARLY_FULL = 0.8;
  async function storageState() {
    const estimated = await estimate();
    if (!estimated || !estimated.quota) return null;
    const usage = estimated.usage || 0;
    const ratio = Math.min(1, usage / estimated.quota);
    return {
      usage,
      quota: estimated.quota,
      free: Math.max(0, estimated.quota - usage),
      percent: Math.round(ratio * 100),
      nearlyFull: ratio > NEARLY_FULL,
    };
  }

  async function estimate() {
    try {
      return (await navigator.storage?.estimate?.()) || null;
    } catch {
      return null;
    }
  }

  // Asked once, at the first download: without it the browser may evict the copy under
  // storage pressure. Resolves to whether storage is (now) persistent.
  async function persist() {
    try {
      if (await navigator.storage?.persisted?.()) return true;
      return (await navigator.storage?.persist?.()) === true;
    } catch {
      return false;
    }
  }

  window.offlineStore = {
    DB_NAME,
    DB_VERSION,
    CACHE_NAME,
    MIGRATIONS,
    upgrade,
    supported,
    chapterUrl,
    readerUrl,
    coverUrl,
    savedKeys,
    chaptersOf,
    saveChapter,
    saveCover,
    listTitles,
    deleteTitle,
    deleteChapters,
    readKeys,
    lastOpened,
    readChapters,
    deleteRead,
    CLEAN_CHOICES,
    LIMIT_CHOICES,
    limitState,
    settings,
    saveSettings,
    touchTitle,
    forgetOpened,
    clearAll,
    estimate,
    storageState,
    persist,
  };
})();
