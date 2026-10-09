// PR 335: «Офлайн» in the settings (settings_offline.html) - what's downloaded for reading
// without a network on this device and how much room it takes: the downloads' own size
// (the IndexedDB index of offline-store.js) next to what the browser gives the site
// (navigator.storage.estimate()), then every title - the biggest first - with its size,
// chapters and when it was last opened, and «Удалить» (offlineStore.deleteTitle). No
// server call: the copy belongs to the device.
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
    summary.hidden = false;
  }

  render();
})();
