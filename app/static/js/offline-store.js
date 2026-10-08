// PR 330: what's downloaded for reading without a network, kept only on this device.
//
//   Cache Storage "wn-offline" - the bytes: each chapter's fragment (GET /offline/titles/
//     {slug}/chapters/{volume}/{number}, PR 329) under that URL without a query, and every
//     image it points at (/images/view?url=...), plus the title's cover the same way.
//   IndexedDB "wn-offline" - the index: titles (slug, name, cover, dates) and chapters
//     (slug, volume, number, name, branch, bytes, images, date), for «Скачано», sizes and
//     "is this one already here?".
//
// The service worker (PR 328) only ever clears its own "wn-static-*" caches, so this one
// survives worker updates. Nothing here talks to ranobelib.me: chapters come through this
// site's own endpoints, one at a time (offline-queue.js).
(() => {
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

  const coverUrl = (url) => (url ? `/images/view?url=${encodeURIComponent(url)}` : null);

  async function chaptersOf(slug) {
    const db = await openDb();
    return done(db.transaction("chapters").objectStore("chapters").index("slug").getAll(slug));
  }

  // Volume--number keys of the chapters of `slug` already on the device.
  async function savedKeys(slug) {
    return new Set((await chaptersOf(slug)).map((chapter) => `${chapter.volume}--${chapter.number}`));
  }

  // Stores one downloaded chapter: `fragment` is the PR 329 JSON, `images` the
  // [{ url, response }] of those of fragment.images that could be fetched (images are
  // best effort, as in the SDK's own exports). Returns the bytes it took.
  async function saveChapter(title, fragment, images) {
    const cache = await caches.open(CACHE_NAME);
    const body = JSON.stringify(fragment);
    let bytes = new Blob([body]).size;
    const stored = [];
    for (const { url, response } of images) {
      const blob = await response.blob();
      bytes += blob.size;
      await cache.put(url, new Response(blob, { headers: response.headers }));
      stored.push(url);
    }
    await cache.put(
      chapterUrl(fragment.slug_url, fragment.volume, fragment.number),
      new Response(body, { headers: { "Content-Type": "application/json" } }),
    );

    const db = await openDb();
    const tx = db.transaction(["titles", "chapters"], "readwrite");
    const titles = tx.objectStore("titles");
    const now = new Date().toISOString();
    const existing = await done(titles.get(fragment.slug_url));
    titles.put({
      slug: fragment.slug_url,
      name: title.name || existing?.name || fragment.slug_url,
      cover: title.cover || existing?.cover || null,
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
    const db = await openDb();
    const all = await done(db.transaction("chapters").objectStore("chapters").getAll());
    const own = all.filter((chapter) => chapter.slug === slug);
    const keep = new Set(all.filter((chapter) => chapter.slug !== slug).flatMap((c) => c.images || []));
    const title = await done(db.transaction("titles").objectStore("titles").get(slug));

    const cache = await caches.open(CACHE_NAME);
    for (const chapter of own) {
      await cache.delete(chapterUrl(slug, chapter.volume, chapter.number));
      for (const image of chapter.images || []) if (!keep.has(image)) await cache.delete(image);
    }
    if (title?.cover) await cache.delete(title.cover);

    const tx = db.transaction(["titles", "chapters"], "readwrite");
    tx.objectStore("titles").delete(slug);
    for (const chapter of own) tx.objectStore("chapters").delete([slug, chapter.volume, chapter.number]);
    await finished(tx);
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
    coverUrl,
    savedKeys,
    saveChapter,
    saveCover,
    listTitles,
    deleteTitle,
    estimate,
    persist,
  };
})();
