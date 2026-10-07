// Client-side search/filter and the explicit "clear history" action (PR 274).
// PR 314: keeps the «Сводка» card in step with the rows on the page - recounted from
// their data-history-outcome after a delete (or its undo), gone with «Очистить».
(() => {
  const root = document.querySelector('[data-role="download-history-groups"]');
  if (!root) return;

  const search = document.querySelector('[data-role="download-history-search"]');
  const filters = [...document.querySelectorAll('[data-role="download-history-filter"]')];
  const noResults = document.querySelector('[data-role="download-history-no-results"]');
  const clear = document.querySelector('[data-role="clear-download-history"]');
  let selected = "all";

  const update = () => {
    const query = (search?.value || "").trim().toLocaleLowerCase("ru");
    let visible = 0;
    for (const row of root.querySelectorAll(".downloads-history__item")) {
      const matchesQuery = !query || row.dataset.historyTitle.includes(query);
      const matchesStatus = selected === "all" || row.dataset.historyStatus === selected;
      row.hidden = !(matchesQuery && matchesStatus);
      if (!row.hidden) visible += 1;
    }
    for (const group of root.querySelectorAll(".wn-downloads-history__group")) {
      group.hidden = !group.querySelector(".downloads-history__item:not([hidden])");
    }
    if (noResults) noResults.hidden = visible !== 0;
  };

  const summary = document.querySelector('[data-role="downloads-summary"]');
  const plural = (n, one, few, many) => {
    if (n % 10 === 1 && n % 100 !== 11) return one;
    if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return few;
    return many;
  };

  function refreshSummary() {
    if (!summary) return;
    const rows = [...root.querySelectorAll(".downloads-history__item")];
    if (rows.length === 0) {
      summary.hidden = true;
      return;
    }
    summary.hidden = false;
    const count = (outcome) => rows.filter((row) => row.dataset.historyOutcome === outcome).length;
    const set = (role, text) => {
      const el = summary.querySelector(`[data-role="${role}"]`);
      if (el) el.textContent = text;
    };
    set("summary-scope", `${rows.length} ${plural(rows.length, "последняя загрузка", "последние загрузки", "последних загрузок")}`);
    set("summary-done", String(count("done")));
    set("summary-error", String(count("error")));
    set("summary-cancelled", String(count("cancelled")));
    // Rows are newest first, so the first one left is the last download.
    const link = rows[0].querySelector(".downloads-history__link");
    const name = summary.querySelector('[data-role="summary-last-name"]');
    if (name && link) {
      name.textContent = link.textContent;
      name.href = link.getAttribute("href");
    }
    set("summary-last-meta", rows[0].dataset.historyMeta || "");
  }

  document.addEventListener("downloads:historychange", refreshSummary);

  search?.addEventListener("input", update);
  for (const button of filters) {
    button.addEventListener("click", () => {
      selected = button.dataset.filter;
      for (const candidate of filters) {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-pressed", String(active));
      }
      update();
    });
  }

  // PR 281: on mobile «Очистить историю?» is a bottom sheet (Webnovells Mobile ->
  // Загрузки, the shared one from bottom-sheet.js); desktop keeps confirm().
  const mobile = window.matchMedia("(max-width: 767px)");
  const sheet = document.getElementById("clear-history-confirm");
  const confirmInSheet = document.querySelector('[data-role="clear-download-history-confirm"]');

  async function clearHistory() {
    clear.disabled = true;
    try {
      const response = await fetch("/downloads/history", { method: "DELETE" });
      if (!response.ok) {
        clear.disabled = false;
        return;
      }
      root.replaceChildren();
      const count = document.querySelector('[data-role="history-count"]');
      if (count) count.textContent = "0";
      clear.remove();
      // PR 314: the whole table (one header for every day) goes, and the toolbar with it.
      document.querySelector(".wn-downloads__tools")?.remove();
      if (noResults) noResults.hidden = true;
      const empty = document.createElement("p");
      empty.className = "wn-downloads-history-empty wn-downloads-history-empty--plain";
      empty.dataset.role = "download-history-empty";
      empty.textContent = "Пока ничего не скачивали.";
      (document.querySelector('[data-role="download-history-table"]') || root).replaceWith(empty);
      refreshSummary();
    } catch {
      clear.disabled = false;
    }
  }

  clear?.addEventListener("click", () => {
    if (mobile.matches && sheet && window.bottomSheet) {
      window.bottomSheet.open({ title: sheet.dataset.bottomSheetTitle, content: sheet, opener: clear });
      return;
    }
    if (window.confirm("Очистить всю историю загрузок?")) clearHistory();
  });

  confirmInSheet?.addEventListener("click", () => {
    window.bottomSheet.close();
    clearHistory();
  });
})();
