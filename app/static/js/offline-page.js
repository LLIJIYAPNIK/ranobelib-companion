// PR 331: the «Нет соединения» page (offline.html, precached by the service worker and
// served under whatever URL couldn't load) - made useful offline:
//   - under a chapter URL that isn't downloaded it says so («Глава не скачана») rather than
//     a generic error;
//   - it lists what is on this device (offline-store.js's index): each title with
//     «Продолжить» and its downloaded chapters - the table of contents within what's
//     downloaded, in the title's own order (stored from the manifest at download time,
//     never sorted here). Under a title's URL (the reader's «К оглавлению») that title
//     comes first, opened.
// «Продолжить» is the chapter last opened in the reader (reader-offline.js) when it's
// downloaded, else the first downloaded one after it, else the first downloaded one.
(() => {
  const store = window.offlineStore;
  const heading = document.querySelector('[data-role="offline-heading"]');
  const hint = document.querySelector('[data-role="offline-hint"]');
  const shelf = document.querySelector('[data-role="offline-shelf"]');
  const list = document.querySelector('[data-role="offline-shelf-list"]');
  const template = document.querySelector('[data-role="offline-shelf-item"]');
  if (!shelf || !store?.supported()) return;

  const path = location.pathname.split("/").map((part) => {
    try {
      return decodeURIComponent(part);
    } catch {
      return part;
    }
  });
  // ["", "titles", slug, "chapters", volume, number]
  const currentSlug = path[1] === "titles" ? path[2] : null;
  const isChapter = currentSlug && path[3] === "chapters" && path.length === 6;

  if (isChapter) {
    heading.textContent = "Глава не скачана";
    document.title = "Глава не скачана — Webnovells";
    hint.textContent = "Эту главу не скачивали для чтения без интернета. Она откроется, когда интернет вернётся.";
  }

  function chaptersWord(n) {
    if (n % 10 === 1 && n % 100 !== 11) return "глава";
    if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return "главы";
    return "глав";
  }

  const key = (volume, number) => `${volume}--${number}`;

  // The downloaded chapters of a title, in the title's own order where it's known.
  function ordered(title, chapters) {
    if (!Array.isArray(title.toc)) return chapters;
    const position = new Map(title.toc.map(([volume, number], index) => [key(volume, number), index]));
    return [...chapters].sort(
      (a, b) =>
        (position.get(key(a.volume, a.number)) ?? Infinity) - (position.get(key(b.volume, b.number)) ?? Infinity),
    );
  }

  function lastRead(slug) {
    try {
      return localStorage.getItem(`readerLastChapter:${slug}`);
    } catch {
      return null;
    }
  }

  function continueTarget(title, chapters) {
    const last = lastRead(title.slug);
    if (!last) return chapters[0];
    const [volume, number] = last.split("--");
    const exact = chapters.find((c) => c.volume === volume && c.number === number);
    if (exact) return exact;
    if (Array.isArray(title.toc)) {
      const lastIndex = title.toc.findIndex(([v, n]) => v === volume && n === number);
      const indexOf = (c) => title.toc.findIndex(([v, n]) => v === c.volume && n === c.number);
      const after = chapters.find((c) => indexOf(c) > lastIndex);
      if (lastIndex >= 0 && after) return after;
    }
    return chapters[0];
  }

  async function render() {
    const titles = await store.listTitles();
    if (!titles.length) {
      if (!isChapter) {
        hint.textContent =
          "Эта страница откроется, когда интернет вернётся. Чтобы читать без сети, скачайте главы на странице тайтла — «Без интернета».";
      }
      return;
    }
    titles.sort((a, b) => (a.slug === currentSlug ? -1 : b.slug === currentSlug ? 1 : 0));
    const rows = [];
    for (const title of titles) {
      const chapters = ordered(title, await store.chaptersOf(title.slug));
      if (!chapters.length) continue;
      const row = template.content.firstElementChild.cloneNode(true);
      row.querySelector('[data-role="offline-shelf-name"]').textContent = title.name;
      const target = continueTarget(title, chapters);
      const go = row.querySelector('[data-role="offline-shelf-continue"]');
      go.href = store.readerUrl(title.slug, target.volume, target.number);
      go.textContent = `Продолжить · Глава ${target.number}`;
      const details = row.querySelector("details");
      details.open = title.slug === currentSlug;
      row.querySelector("summary").textContent =
        `Скачано ${chapters.length} ${chaptersWord(chapters.length)}`;
      const items = chapters.map((chapter) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = store.readerUrl(title.slug, chapter.volume, chapter.number);
        link.textContent = `Том ${chapter.volume} · Глава ${chapter.number}${chapter.name ? ` — ${chapter.name}` : ""}`;
        item.append(link);
        return item;
      });
      row.querySelector("ol").replaceChildren(...items);
      rows.push(row);
    }
    list.replaceChildren(...rows);
    shelf.hidden = !rows.length;
  }

  render().catch(() => {});
})();
