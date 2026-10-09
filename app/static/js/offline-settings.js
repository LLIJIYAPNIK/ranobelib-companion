// PR 335: «Офлайн» in the settings (settings_offline.html) - what's downloaded for reading
// without a network on this device and how much room it takes: the downloads' own size
// (the IndexedDB index of offline-store.js) next to what the browser gives the site
// (navigator.storage.estimate()), then every title - the biggest first - with its size,
// chapters and when it was last opened, and «Удалить» (offlineStore.deleteTitle); the
// auto-cleanup of read chapters (offlineStore.settings().cleanBehind - reader-offline.js
// applies it) and «Удалить прочитанное» once, now. No server call: the copy belongs to
// the device.
(() => {
  const page = document.querySelector('[data-role="offline-settings"]');
  if (!page) return;
  const store = window.offlineStore;
  const q = (role) => page.querySelector(`[data-role="${role}"]`);

  if (!store?.supported()) {
    q("offline-settings-unsupported").hidden = false;
    return;
  }

  const summary = q("offline-settings-summary");
  const total = q("offline-summary-total");
  const meter = q("offline-summary-meter");
  const fill = q("offline-summary-fill");
  const pct = q("offline-summary-pct");
  const quota = q("offline-summary-quota");
  const warning = q("offline-summary-warning");
  const titlesCard = q("offline-settings-titles");
  const list = q("offline-titles-list");
  const rowTemplate = q("offline-titles-row");
  const confirmBox = page.querySelector("#offline-settings-delete");
  const confirmText = q("offline-titles-delete-text");
  const confirmButton = q("offline-titles-delete-confirm");
  let pending = null; // the title «Удалить» was pressed for
  const cleanCard = q("offline-settings-clean");
  const cleanNow = q("offline-clean-now");
  const cleanResult = q("offline-clean-result");
  const cleanBox = page.querySelector("#offline-settings-clean");
  const cleanText = q("offline-clean-text");
  const cleanConfirm = q("offline-clean-confirm");
  let current = []; // listTitles() as last rendered

  function formatBytes(bytes) {
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    const digits = unit >= 2 && value < 10 ? 1 : 0;
    return `${value.toLocaleString("ru-RU", { maximumFractionDigits: digits })} ${units[unit]}`;
  }

  function plural(n, one, few, many) {
    if (n % 10 === 1 && n % 100 !== 11) return one;
    if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return few;
    return many;
  }
  const chaptersWord = (n) => plural(n, "глава", "главы", "глав");
  const titlesWord = (n) => plural(n, "тайтл", "тайтла", "тайтлов");

  function renderSummary(titles, storage) {
    const chapters = titles.reduce((sum, title) => sum + title.chapters, 0);
    const bytes = titles.reduce((sum, title) => sum + title.bytes, 0);
    total.textContent = titles.length
      ? `Скачано ${formatBytes(bytes)}: ${titles.length} ${titlesWord(titles.length)}, ${chapters} ${chaptersWord(chapters)}`
      : "Пока ничего не скачано. Скачать главы можно кнопкой «Скачать для чтения без интернета» на странице тайтла.";

    meter.hidden = !storage;
    warning.hidden = !storage?.nearlyFull;
    if (!storage) {
      quota.textContent = "Браузер не сообщает, сколько места он отдаёт сайту.";
      return;
    }
    fill.style.width = `${storage.percent}%`;
    pct.textContent = `${storage.percent}%`;
    // estimate() counts everything the site keeps (downloads and the app's own files).
    quota.textContent =
      `Браузер отдал сайту ≈ ${formatBytes(storage.quota)}: занято ≈ ${formatBytes(storage.usage)}, ` +
      `свободно ≈ ${formatBytes(storage.free)}.`;
    if (storage.nearlyFull) {
      warning.textContent = "Место почти закончилось — новые главы могут не скачаться. Удалите ненужное ниже.";
    }
  }

  const date = (iso) => new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });

  function renderTitles(titles) {
    titlesCard.hidden = !titles.length;
    const biggest = [...titles].sort((a, b) => b.bytes - a.bytes);
    list.replaceChildren(
      ...biggest.map((title) => {
        const row = rowTemplate.content.firstElementChild.cloneNode(true);
        const image = row.querySelector("img");
        if (title.cover) image.src = title.cover;
        else image.remove();
        const link = row.querySelector(".wn-offline-saved__name");
        link.href = `/titles/${encodeURIComponent(title.slug)}`;
        link.textContent = title.name;
        row.querySelector('[data-role="offline-titles-size"]').textContent =
          `${formatBytes(title.bytes)} · ${title.chapters} ${chaptersWord(title.chapters)}`;
        // No date for titles downloaded before PR 335 and after a logout (forgetOpened).
        row.querySelector('[data-role="offline-titles-opened"]').textContent = title.openedAt
          ? `Последний раз открывали ${date(title.openedAt)}`
          : `Скачан ${date(title.savedAt)}`;
        const remove = row.querySelector('[data-role="offline-titles-delete"]');
        remove.setAttribute("aria-label", `Удалить с устройства: ${title.name}`);
        remove.addEventListener("click", () => ask(title, remove));
        return row;
      }),
    );
  }

  function ask(title, opener) {
    pending = title;
    confirmText.textContent =
      `«${title.name}» — ${title.chapters} ${chaptersWord(title.chapters)}, ${formatBytes(title.bytes)} — ` +
      "пропадёт с этого устройства. Онлайн тайтл останется доступен.";
    window.bottomSheet.open({ title: confirmBox.dataset.bottomSheetTitle, content: confirmBox, opener });
  }

  confirmButton.addEventListener("click", async () => {
    if (!pending) return;
    const slug = pending.slug;
    pending = null;
    confirmButton.disabled = true;
    try {
      await store.deleteTitle(slug);
    } finally {
      confirmButton.disabled = false;
      window.bottomSheet.close();
      render();
    }
  });

  // --- read chapters -------------------------------------------------------------------
  for (const input of page.querySelectorAll('[data-offline-setting="cleanBehind"]')) {
    input.checked = Number(input.value) === store.settings().cleanBehind;
    input.addEventListener("change", () => store.saveSettings({ cleanBehind: Number(input.value) }));
  }

  // What «Удалить прочитанное» would free: per title, the chapters on the device before
  // the one opened last (nothing kept behind it).
  async function readOnDevice() {
    const found = [];
    for (const title of current) {
      const read = await store.readChapters(title.slug, 0);
      if (read.length) {
        found.push({ slug: title.slug, chapters: read.length, bytes: read.reduce((sum, c) => sum + (c.bytes || 0), 0) });
      }
    }
    return found;
  }

  let toClean = [];
  cleanNow.addEventListener("click", async () => {
    cleanResult.textContent = "";
    toClean = await readOnDevice();
    if (!toClean.length) {
      cleanResult.textContent = "Прочитанных глав на устройстве нет.";
      return;
    }
    const chapters = toClean.reduce((sum, t) => sum + t.chapters, 0);
    const bytes = toClean.reduce((sum, t) => sum + t.bytes, 0);
    cleanText.textContent =
      `${chapters} ${chaptersWord(chapters)} (≈ ${formatBytes(bytes)}) до тех, что вы открывали последними, ` +
      "пропадут с этого устройства. Текущие и следующие главы останутся.";
    window.bottomSheet.open({ title: cleanBox.dataset.bottomSheetTitle, content: cleanBox, opener: cleanNow });
  });

  cleanConfirm.addEventListener("click", async () => {
    cleanConfirm.disabled = true;
    let freed = { chapters: 0, bytes: 0 };
    try {
      for (const { slug } of toClean) {
        const result = await store.deleteRead(slug, 0);
        freed = { chapters: freed.chapters + result.chapters, bytes: freed.bytes + result.bytes };
      }
    } finally {
      toClean = [];
      cleanConfirm.disabled = false;
      window.bottomSheet.close();
      cleanResult.textContent = `Удалено ${freed.chapters} ${chaptersWord(freed.chapters)}, освобождено ≈ ${formatBytes(freed.bytes)}.`;
      render();
    }
  });

  async function render() {
    let titles;
    try {
      titles = await store.listTitles();
    } catch {
      q("offline-settings-unsupported").hidden = false;
      return;
    }
    const storage = await store.storageState();
    renderSummary(titles, storage);
    renderTitles(titles);
    current = titles;
    summary.hidden = false;
    cleanCard.hidden = false;
  }

  render();
})();
