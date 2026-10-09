// PR 330: «Скачано» on /downloads (_offline_saved.html) - every title kept on this device
// for reading without a network, with its chapter count and size, and «Удалить», which
// frees the space (offlineStore.deleteTitle: the chapters, their images, the cover).
// Read from the IndexedDB index only, no server call. Hidden when there's nothing.
// PR 333: how full the browser's storage for this site is (a warning past 80%), and
// «Очистить офлайн-данные» - every download at once (offlineStore.clearAll).
(() => {
  const section = document.querySelector('[data-role="offline-saved"]');
  if (!section || !window.offlineStore?.supported()) return;

  const store = window.offlineStore;
  const list = section.querySelector('[data-role="offline-saved-list"]');
  const rowTemplate = section.querySelector('[data-role="offline-saved-row"]');
  const count = section.querySelector('[data-role="offline-saved-count"]');
  const usage = section.querySelector('[data-role="offline-saved-usage"]');
  const confirmBox = section.querySelector("#offline-delete-confirm");
  const confirmText = section.querySelector('[data-role="offline-delete-text"]');
  const confirmButton = section.querySelector('[data-role="offline-delete-confirm"]');
  const quotaWarning = section.querySelector('[data-role="offline-saved-quota-warning"]');
  const clearAllButton = section.querySelector('[data-role="offline-clear-all"]');
  const clearBox = section.querySelector("#offline-clear-confirm");
  const clearText = section.querySelector('[data-role="offline-clear-text"]');
  const clearConfirm = section.querySelector('[data-role="offline-clear-confirm"]');
  let current = [];

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

  function chaptersWord(n) {
    if (n % 10 === 1 && n % 100 !== 11) return "глава";
    if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return "главы";
    return "глав";
  }

  const date = (iso) => new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });

  async function render() {
    let titles;
    try {
      titles = await store.listTitles();
    } catch {
      section.hidden = true;
      return;
    }
    current = titles;
    section.hidden = !titles.length;
    if (!titles.length) return;

    count.textContent = String(titles.length);
    const total = titles.reduce((sum, title) => sum + title.bytes, 0);
    const storage = await store.storageState();
    usage.textContent =
      `Занято ≈ ${formatBytes(total)}` +
      (storage ? ` · свободно ≈ ${formatBytes(storage.free)} · место браузера занято на ${storage.percent}%` : "");
    quotaWarning.hidden = !storage?.nearlyFull;
    if (storage?.nearlyFull) {
      quotaWarning.textContent =
        `Место почти закончилось: браузер отдал сайту ≈ ${formatBytes(storage.quota)}, занято ${storage.percent}%. ` +
        "Новые главы могут не скачаться — удалите ненужное.";
    }

    list.replaceChildren(
      ...titles.map((title) => {
        const row = rowTemplate.content.firstElementChild.cloneNode(true);
        const image = row.querySelector("img");
        if (title.cover) image.src = title.cover;
        else image.remove();
        const link = row.querySelector(".wn-offline-saved__name");
        link.href = `/titles/${encodeURIComponent(title.slug)}`;
        link.textContent = title.name;
        row.querySelector(".wn-offline-saved__meta").textContent =
          `${title.chapters} ${chaptersWord(title.chapters)} · ${formatBytes(title.bytes)} · ${date(title.updatedAt)}`;
        const remove = row.querySelector('[data-role="offline-saved-delete"]');
        remove.dataset.slug = title.slug;
        remove.setAttribute("aria-label", `Удалить скачанное: ${title.name}`);
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

  clearAllButton.addEventListener("click", () => {
    const chapters = current.reduce((sum, title) => sum + title.chapters, 0);
    const bytes = current.reduce((sum, title) => sum + title.bytes, 0);
    clearText.textContent = `${chapters} ${chaptersWord(chapters)}, ≈ ${formatBytes(bytes)}`;
    window.bottomSheet.open({ title: clearBox.dataset.bottomSheetTitle, content: clearBox, opener: clearAllButton });
  });

  clearConfirm.addEventListener("click", async () => {
    clearConfirm.disabled = true;
    try {
      await store.clearAll();
    } finally {
      clearConfirm.disabled = false;
      window.bottomSheet.close();
      render();
    }
  });

  render();
})();
