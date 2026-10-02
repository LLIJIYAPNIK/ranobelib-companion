// Client-side search/filter and the explicit "clear history" action (PR 274).
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
      const empty = document.createElement("p");
      empty.className = "ui-empty";
      empty.textContent = "Пока ничего не скачивали.";
      root.replaceWith(empty);
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
