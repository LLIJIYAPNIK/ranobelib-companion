// PR 275 (Webnovells Redesign, screen A3): the library toolbar - search by name, sort,
// progress filter and the list/grid switch - over the cards the server already rendered
// (a personal library is small, so this is plain client-side filtering, no extra
// request). The toolbar is `hidden` in the markup and only shown here, so without JS the
// page is just the full list.
//
// Also drops the cover skeleton shimmer (.wn-skeleton) once each cover image has loaded.
//
// PR 282 (Webnovells Mobile -> Библиотека): on phones the sort/progress selects give way
// to «Фильтры» - a count of the controls changed from their first option, the current
// values as chips, and the shared bottom sheet (bottom-sheet.js) with a radio group per
// select, built from that select's own options. A radio sets its select and fires
// "change", so the selects stay the one source of state for both layouts.
//
// PR 297 (LibraryDesktop.dc.html): the page's two modes. The default one (no search,
// «Недавно читал», «Любой») has the «Продолжить чтение» hero, «Моя библиотека» and the
// «Добавить из каталога» tile; any criterion switches .wn-library's data-mode to
// "results" - the hero's own card joins the grid (app.css), and the panel gets the
// «Условия» chips (each drops its criterion), «Сбросить всё» and «K тайтлов из N».
(() => {
  for (const img of document.querySelectorAll(".wn-skeleton img")) {
    const done = () => img.closest(".wn-skeleton")?.classList.add("is-loaded");
    if (img.complete) done();
    else {
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
    }
  }

  const toolbar = document.querySelector('[data-role="library-toolbar"]');
  const titles = document.querySelector('[data-role="library-titles"]');
  if (!toolbar || !titles) return;
  toolbar.hidden = false;

  const search = toolbar.querySelector('[data-role="library-search"]');
  const sort = toolbar.querySelector('[data-role="library-sort"]');
  const progress = toolbar.querySelector('[data-role="library-progress"]');
  const viewButtons = [...toolbar.querySelectorAll("[data-view]")];
  const noResults = titles.querySelector('[data-role="library-no-results"]');
  const VIEW_KEY = "libraryView";

  // data-progress: the percent read, 0 when opened but unknown, -1 never opened.
  function matchesProgress(value, pct) {
    if (value === "started") return pct >= 0 && pct < 100;
    if (value === "new") return pct < 0;
    if (value === "done") return pct >= 100;
    return true;
  }

  const page = titles.closest(".wn-library");
  const criteria = toolbar.querySelector('[data-role="library-criteria"]');
  const criteriaChips = criteria?.querySelector('[data-role="library-criteria-chips"]');
  const criteriaCount = criteria?.querySelector('[data-role="library-criteria-count"]');
  const endText = titles.querySelector('[data-role="library-end-text"]');
  const endReset = titles.querySelector('[data-role="library-end-reset"]');
  const end = titles.querySelector('[data-role="library-end"]');
  const noResultsText = titles.querySelector('[data-role="library-no-results-text"]');
  const noResultsCatalog = titles.querySelector('[data-role="library-no-results-catalog"]');
  let total = titles.querySelectorAll('[data-role="library-item"]').length;
  let endDefault = endText?.textContent ?? "";

  function titlesWord(n) {
    const m10 = n % 10;
    const m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return `${n} тайтл`;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return `${n} тайтла`;
    return `${n} тайтлов`;
  }

  function resetAll() {
    search.value = "";
    sort.selectedIndex = 0;
    progress.selectedIndex = 0;
    apply();
  }

  function criterionChip(key, value, clear) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "wn-library-criterion";
    chip.setAttribute("aria-label", `Убрать: ${key} ${value}`);
    const label = document.createElement("span");
    label.className = "wn-library-criterion__key";
    label.textContent = key;
    chip.append(label, value);
    chip.insertAdjacentHTML(
      "beforeend",
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17"/></svg>'
    );
    chip.addEventListener("click", () => {
      clear();
      apply();
    });
    return chip;
  }

  function syncMode(visibleTotal) {
    const query = search.value.trim();
    const results = Boolean(query) || sort.selectedIndex !== 0 || progress.selectedIndex !== 0;
    if (page) page.dataset.mode = results ? "results" : "default";
    if (criteria) {
      criteria.hidden = !results;
      const chipsNow = [];
      if (query) chipsNow.push(criterionChip("Поиск", `«${query}»`, () => (search.value = "")));
      if (sort.selectedIndex !== 0) {
        chipsNow.push(criterionChip("Сортировка", sort.options[sort.selectedIndex].text, () => (sort.selectedIndex = 0)));
      }
      if (progress.selectedIndex !== 0) {
        chipsNow.push(criterionChip("Прогресс", progress.options[progress.selectedIndex].text, () => (progress.selectedIndex = 0)));
      }
      criteriaChips?.replaceChildren(...chipsNow);
      if (criteriaCount) criteriaCount.textContent = `${titlesWord(visibleTotal)} из ${total}`;
    }
    if (endText) endText.textContent = results ? "Больше ничего не подходит под условия" : endDefault;
    if (endReset) endReset.hidden = !results;
    // Nothing matches: the empty card says what was searched for and offers the catalog
    // with the same search; the end line goes.
    if (end) end.hidden = visibleTotal === 0;
    if (noResultsText) {
      noResultsText.textContent = query
        ? `Среди ваших тайтлов нет «${query}». Возможно, он ещё не добавлен — проверьте каталог.`
        : "Под эти условия не подходит ни один тайтл из библиотеки.";
    }
    if (noResultsCatalog) {
      noResultsCatalog.href = query ? `/catalog?${new URLSearchParams({ query })}` : "/catalog";
    }
  }

  const collator = new Intl.Collator("ru");
  const comparators = {
    recent: (a, b) => Number(a.dataset.order) - Number(b.dataset.order),
    added: (a, b) => (b.dataset.added || "").localeCompare(a.dataset.added || ""),
    name: (a, b) => collator.compare(a.dataset.name, b.dataset.name),
    progress: (a, b) => Number(b.dataset.progress) - Number(a.dataset.progress),
  };

  function apply() {
    const query = search.value.trim().toLowerCase();
    let visibleTotal = 0;
    for (const list of titles.querySelectorAll('[data-role="library-list"]')) {
      const items = [...list.querySelectorAll('[data-role="library-item"]')];
      items.sort(comparators[sort.value] || comparators.recent);
      let visible = 0;
      for (const item of items) {
        const show =
          (!query || item.dataset.name.includes(query)) &&
          matchesProgress(progress.value, Number(item.dataset.progress));
        item.hidden = !show;
        if (show) visible += 1;
        list.append(item);
      }
      // PR 297: «Добавить из каталога» stays the grid's last cell whatever the order.
      const tile = list.querySelector('[data-role="library-add-tile"]');
      if (tile) list.append(tile);
      const section = list.closest('[data-role="library-section"]');
      if (section) section.hidden = visible === 0;
      visibleTotal += visible;
    }
    // An emptied library (the last title removed, «Вернуть» still on offer) isn't "nothing
    // found" - library-card-actions.js reloads into the empty library's hint once the
    // removal is sent.
    if (noResults) noResults.hidden = visibleTotal > 0 || total === 0;
    syncMode(visibleTotal);
    syncFilters(visibleTotal);
  }

  const filtersOpen = toolbar.querySelector('[data-role="library-filters-open"]');
  const filtersCount = toolbar.querySelector('[data-role="library-filters-count"]');
  const chipRow = toolbar.querySelector('[data-role="library-filter-chips"]');
  const filtersSheet = document.getElementById("library-filters");
  const filtersDone = filtersSheet?.querySelector('[data-role="library-filters-done"]');
  const filtersReset = filtersSheet?.querySelector('[data-role="library-filters-reset"]');
  const filterControls = [sort, progress];
  const capitalize = (text) => text.charAt(0).toLocaleUpperCase("ru") + text.slice(1);
  const chips = new Map();

  function openFilters(opener) {
    if (!filtersSheet || !window.bottomSheet) return;
    window.bottomSheet.open({ title: filtersSheet.dataset.bottomSheetTitle, content: filtersSheet, opener });
  }

  function buildFilters() {
    if (!filtersSheet) return;
    for (const group of filtersSheet.querySelectorAll("[data-filter-for]")) {
      const select = toolbar.querySelector(`[data-role="${group.dataset.filterFor}"]`);
      for (const option of select.options) {
        const label = document.createElement("label");
        label.className = "wn-library-filters__option";
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `sheet-${group.dataset.filterFor}`;
        input.value = option.value;
        input.addEventListener("change", () => {
          select.value = input.value;
          select.dispatchEvent(new Event("change"));
        });
        const text = document.createElement("span");
        text.textContent = capitalize(option.text);
        label.append(input, text);
        group.append(label);
      }
    }
    for (const select of filterControls) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "wn-library-chip";
      chip.setAttribute("aria-haspopup", "dialog");
      chip.addEventListener("click", () => openFilters(chip));
      chipRow?.append(chip);
      chips.set(select, chip);
    }
    filtersOpen?.addEventListener("click", () => openFilters(filtersOpen));
    filtersReset?.addEventListener("click", () => {
      for (const select of filterControls) select.selectedIndex = 0;
      search.value = "";
      apply();
    });
  }

  function syncFilters(visibleTotal) {
    const changed = filterControls.filter((select) => select.selectedIndex !== 0).length;
    if (filtersCount) {
      filtersCount.hidden = changed === 0;
      filtersCount.textContent = String(changed);
    }
    filtersOpen?.setAttribute("aria-label", changed ? `Фильтры, изменено: ${changed}` : "Фильтры");
    for (const select of filterControls) {
      const chip = chips.get(select);
      if (chip) {
        const value = document.createElement("b");
        value.textContent = select.options[select.selectedIndex].text;
        chip.replaceChildren(`${select.getAttribute("aria-label")}: `, value);
      }
      const radio = filtersSheet?.querySelector(`input[name="sheet-${select.dataset.role}"][value="${select.value}"]`);
      if (radio) radio.checked = true;
    }
    if (filtersDone) filtersDone.textContent = visibleTotal ? `Показать ${visibleTotal}` : "Готово";
  }

  function setView(view) {
    titles.classList.toggle("wn-library__titles--grid", view === "grid");
    for (const button of viewButtons) {
      button.setAttribute("aria-checked", button.dataset.view === view ? "true" : "false");
    }
  }

  criteria?.querySelector('[data-role="library-criteria-reset"]')?.addEventListener("click", resetAll);
  endReset?.addEventListener("click", resetAll);
  titles
    .querySelector('[data-role="library-no-results-reset"]')
    ?.addEventListener("click", resetAll);

  // PR 298: a title removed from its card menu (library-card-actions.js) - recount
  // everything that shows the library's size, then re-apply the current criteria.
  document.addEventListener("library:changed", () => {
    total = titles.querySelectorAll('[data-role="library-item"]').length;
    endDefault = `Это вся библиотека · ${titlesWord(total)}`;
    const eyebrow = document.querySelector(".wn-library__eyebrow");
    if (eyebrow) eyebrow.textContent = `Ваши тайтлы · ${titlesWord(total)}`;
    const switchCount = document.querySelector('[data-role="library-switch-count"]');
    if (switchCount) switchCount.textContent = String(total);
    const rest = titles.querySelector('[data-role="library-rest"]');
    if (rest) {
      const hero = titles.querySelector('[data-role="library-hero"]');
      if (!hero) rest.textContent = titlesWord(total);
      else rest.textContent = total > 1 ? `ещё ${titlesWord(total - 1)}` : "пока только этот";
    }
    apply();
  });

  buildFilters();
  syncFilters(titles.querySelectorAll('[data-role="library-item"]').length);
  search.addEventListener("input", apply);
  sort.addEventListener("change", apply);
  progress.addEventListener("change", apply);
  for (const button of viewButtons) {
    button.addEventListener("click", () => {
      setView(button.dataset.view);
      try {
        localStorage.setItem(VIEW_KEY, button.dataset.view);
      } catch {
        // Storage can be unavailable (private mode) - the switch still works for this visit.
      }
    });
  }

  let savedView = null;
  try {
    savedView = localStorage.getItem(VIEW_KEY);
  } catch {
    savedView = null;
  }
  if (savedView === "grid") setView("grid");
})();
