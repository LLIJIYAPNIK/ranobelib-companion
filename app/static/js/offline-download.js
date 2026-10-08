// PR 330: «Скачать для чтения без интернета» on the title page. Opens the shared bottom
// sheet with the manager (_title_content.html, [data-role="offline-download"]): which
// chapters - by default the unread ones from the reading position on - the translation
// for chapters that have several (asked, never picked for the visitor), and the size
// against the device's free space. «Скачать» closes the sheet and runs an OfflineQueue
// (offline-queue.js) - strictly one chapter at a time - with its progress, pause, resume,
// retry and cancel on the page itself ([data-role="offline-download-status"]).
//
// Hidden without IndexedDB/Cache Storage. Chapters already on the device are skipped, so
// starting the same selection again (after a cancel, or after leaving the page mid-way)
// carries on where it stopped. Runs after title-content-load.js has injected the
// fragment (window.initOfflineDownload).
(() => {
  const PAUSE_NOTES = {
    "rate-limit": "ranobelib сейчас ограничивает запросы — продолжите чуть позже.",
    blocked: "ranobelib.me временно блокирует запросы — продолжите позже.",
    quota: "На устройстве закончилось место. Освободите его в «Скачано» и продолжите.",
    offline: "Нет соединения. Продолжите, когда интернет вернётся.",
  };
  const PERSIST_KEY = "offlinePersistAsked";

  const ios =
    /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

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

  function initOfflineDownload() {
    const root = document.querySelector('[data-role="offline-download"]');
    if (!root || !window.offlineStore?.supported() || !window.OfflineQueue) return;

    const store = window.offlineStore;
    const slug = root.dataset.slugUrl;
    const currentIndex = root.dataset.currentIndex === "" ? null : Number(root.dataset.currentIndex);
    const defaultTranslation = root.dataset.defaultTranslation;
    const q = (role, scope = root) => scope.querySelector(`[data-role="${role}"]`);

    const loading = q("offline-loading");
    const errorBox = q("offline-error");
    const form = q("offline-form");
    const nextField = q("offline-next");
    const nextCount = q("offline-next-count");
    const range = q("offline-range");
    const from = q("offline-from");
    const to = q("offline-to");
    const translation = q("offline-translation");
    const translationSelect = q("offline-translation-select");
    const summary = q("offline-summary");
    const space = q("offline-space");
    const startButton = q("offline-start");

    const status = document.querySelector('[data-role="offline-download-status"]');
    const statusText = q("offline-status-text", status);
    const statusSize = q("offline-status-size", status);
    const statusFill = q("offline-status-fill", status);
    const statusNote = q("offline-status-note", status);
    const pauseButton = q("offline-pause", status);
    const resumeButton = q("offline-resume", status);
    const retryButton = q("offline-retry", status);
    const cancelButton = q("offline-cancel", status);
    const savedLink = q("offline-saved-link", status);

    let manifest = null;
    let chapters = [];
    let saved = new Set();
    let free = null;
    let queue = null;

    q("offline-ios-hint").hidden = !(ios && !standalone());
    document
      .querySelectorAll('[data-role="offline-download-open"], [data-role="offline-download-sep"]')
      .forEach((element) => {
        element.hidden = false;
      });

    const active = () => queue && (queue.state === "running" || queue.state === "paused");

    function selection() {
      const mode = form.elements["offline-mode"].value;
      if (mode === "all") return chapters;
      if (mode === "range") {
        const a = Number(from.value);
        const b = Number(to.value);
        return chapters.slice(Math.min(a, b), Math.max(a, b) + 1);
      }
      const start = currentIndex ?? 0;
      return chapters.slice(start, start + Number(nextCount.value));
    }

    const key = (chapter) => `${chapter.volume}--${chapter.number}`;

    function refresh() {
      const mode = form.elements["offline-mode"].value;
      nextField.hidden = mode !== "next";
      range.hidden = mode !== "range";

      const picked = selection();
      const missing = picked.filter((chapter) => !saved.has(key(chapter)));
      const needsTranslation = missing.some((chapter) => chapter.branches.length > 1);
      translation.hidden = !needsTranslation;

      const perChapter = manifest.estimated_bytes_per_chapter;
      const need = perChapter ? perChapter * missing.length : null;
      const parts = [];
      if (missing.length) {
        parts.push(`${missing.length} ${chaptersWord(missing.length)}`);
        if (need) parts.push(`≈ ${formatBytes(need)}`);
      } else {
        parts.push(picked.length ? "Все выбранные главы уже на устройстве" : "Нет глав для скачивания");
      }
      const already = picked.length - missing.length;
      if (missing.length && already) parts.push(`уже на устройстве: ${already}`);
      if (free != null) parts.push(`свободно ≈ ${formatBytes(free)}`);
      summary.textContent = parts.join(" · ");

      space.hidden = !(need && free != null && need > free);
      if (!space.hidden) {
        space.textContent = `Места может не хватить: нужно ≈ ${formatBytes(need)}, свободно ≈ ${formatBytes(free)}.`;
      }

      if (active()) {
        startButton.disabled = true;
        startButton.textContent = "Уже скачивается — см. на странице";
      } else {
        startButton.disabled = !missing.length || (needsTranslation && translationSelect.value === "");
        startButton.textContent = missing.length ? `Скачать ${missing.length} ${chaptersWord(missing.length)}` : "Скачать";
      }
    }

    function fillForm() {
      chapters = manifest.volumes.flatMap((volume) => volume.chapters);
      const options = chapters.map((chapter, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = `Том ${chapter.volume} · Глава ${chapter.number}${chapter.name ? ` — ${chapter.name}` : ""}`;
        return option;
      });
      from.replaceChildren(...options);
      to.replaceChildren(...options.map((option) => option.cloneNode(true)));
      const start = Math.min(currentIndex ?? 0, Math.max(chapters.length - 1, 0));
      from.value = String(start);
      to.value = String(Math.min(start + 9, chapters.length - 1));

      const variants = Math.max(0, ...chapters.map((chapter) => chapter.branches.length));
      for (let index = 0; index < variants; index += 1) {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = `Вариант ${index + 1}`;
        translationSelect.append(option);
      }
      if (defaultTranslation !== "" && Number(defaultTranslation) < variants) {
        translationSelect.value = defaultTranslation;
      }
    }

    async function load() {
      saved = await store.savedKeys(slug);
      const estimate = await store.estimate();
      free = estimate && estimate.quota != null ? Math.max(0, estimate.quota - (estimate.usage || 0)) : null;
      if (manifest) {
        refresh();
        return;
      }
      loading.hidden = false;
      errorBox.hidden = true;
      try {
        const response = await fetch(`/offline/titles/${encodeURIComponent(slug)}/manifest`, {
          headers: { Accept: "application/json" },
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.detail || "Не удалось загрузить оглавление");
        manifest = data;
        fillForm();
        form.hidden = false;
        refresh();
      } catch (error) {
        errorBox.textContent =
          error instanceof TypeError ? "Нет соединения — оглавление не загрузилось" : error.message;
        errorBox.hidden = false;
      } finally {
        loading.hidden = true;
      }
    }

    function open(opener) {
      // On mobile the trigger is a row in the «⋯» sheet (a native <dialog>) - close it.
      document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
      window.bottomSheet.open({ title: root.dataset.bottomSheetTitle, content: root, opener });
      load();
    }

    function render(current) {
      status.hidden = false;
      const shown = Math.min(current.position + (current.state === "running" ? 1 : 0), current.total);
      const texts = {
        running: `Скачиваем главу ${shown} из ${current.total}`,
        paused: `Пауза · скачано ${current.completed} из ${current.total}`,
        done: `Готово · скачано ${current.completed} из ${current.total}`,
        cancelled: `Отменено · скачано ${current.completed} из ${current.total}`,
      };
      statusText.textContent = texts[current.state] || "";
      statusSize.textContent = current.bytes ? formatBytes(current.bytes) : "";
      statusFill.style.width = `${current.total ? Math.round((current.position / current.total) * 100) : 0}%`;

      const notes = [];
      if (current.state === "running") notes.push("Не уходите со страницы, пока идёт скачивание.");
      if (current.state === "paused" && PAUSE_NOTES[current.pauseReason]) {
        notes.push(PAUSE_NOTES[current.pauseReason]);
      }
      if (current.failed.length && current.state !== "running") {
        const first = current.failed[0];
        notes.push(
          `Не скачались: ${current.failed.length} ${chaptersWord(current.failed.length)} ` +
            `(глава ${first.item.number}: ${first.message}).`,
        );
      }
      statusNote.textContent = notes.join(" ");
      statusNote.hidden = !notes.length;

      pauseButton.hidden = current.state !== "running";
      resumeButton.hidden = current.state !== "paused";
      retryButton.hidden = !(current.failed.length && (current.state === "done" || current.state === "paused"));
      cancelButton.hidden = !(current.state === "running" || current.state === "paused");
      savedLink.hidden = !(current.completed && current.state !== "running");

      if (current.state === "done" || current.state === "cancelled") {
        store.savedKeys(slug).then((keys) => {
          saved = keys;
        });
      }
    }

    async function start(event) {
      event.preventDefault();
      if (active()) return;
      const variant = translationSelect.value === "" ? null : Number(translationSelect.value);
      const items = selection()
        .filter((chapter) => !saved.has(key(chapter)))
        .map((chapter) => ({
          volume: chapter.volume,
          number: chapter.number,
          name: chapter.name,
          // Several translations: the visitor's «Вариант N», as the SDK's own
          // translation_index means it. A chapter without that variant goes without a
          // branch and comes back as «выберите перевод» - listed, not guessed.
          branchId:
            chapter.branches.length > 1 && variant != null
              ? (chapter.branches[variant]?.branch_id ?? null)
              : null,
        }));
      if (!items.length) return;

      try {
        if (localStorage.getItem(PERSIST_KEY) !== "1") {
          localStorage.setItem(PERSIST_KEY, "1");
          store.persist();
        }
      } catch {
        store.persist();
      }

      const title = {
        slug,
        name: root.dataset.titleName,
        cover: await store.saveCover(root.dataset.coverUrl),
      };
      queue = new window.OfflineQueue(items, {
        download: (item, signal) => window.OfflineQueue.downloadChapter(title, item, signal),
        onChange: render,
      });
      window.bottomSheet.close();
      status.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      queue.start();
    }

    document.addEventListener("click", (event) => {
      const trigger = event.target.closest('[data-role="offline-download-open"]');
      if (trigger) open(trigger);
    });
    form.addEventListener("change", refresh);
    form.addEventListener("submit", start);
    pauseButton.addEventListener("click", () => queue?.pause("user"));
    resumeButton.addEventListener("click", () => queue?.resume());
    retryButton.addEventListener("click", () => queue?.retryFailed());
    cancelButton.addEventListener("click", () => queue?.cancel());
  }

  window.initOfflineDownload = initOfflineDownload;
})();
