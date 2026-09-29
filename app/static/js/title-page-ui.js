// Title page behaviour on top of _title_content.html (PR 253, Aurora Ink). Runs after
// title-content-load.js has injected the fragment (window.initTitlePageUi). Without JS
// the page still works: both sections show, the summary isn't clamped and every chapter
// checkbox is visible.
//
// - Описание / Оглавление tabs: mobile only (the desktop layout shows both side by
//   side). Arrow keys move between the two tabs.
// - «Читать полностью»: the summary is clamped to 4 lines only once this runs, and only
//   gets the toggle when it actually overflows.
// - «Выбрать главы»: chapter checkboxes stay hidden until selection mode is on
//   (.toc--selecting); chapter-export-panel.js still shows the export bar once something
//   is checked. title-actions-sheet.js turns the same mode on from «Скачать тома или
//   главы» via window.titlePageUi.startChapterSelection().
(() => {
  const mobileQuery = window.matchMedia("(max-width: 767px)");

  function initTabs() {
    const tablist = document.querySelector('[data-role="title-tabs"]');
    if (!tablist) return null;
    const tabs = [...tablist.querySelectorAll('[data-role="title-tab"]')];
    const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls")));

    function select(index, focus = false) {
      tabs.forEach((tab, i) => {
        const on = i === index;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        if (on) tab.setAttribute("aria-current", "page");
        else tab.removeAttribute("aria-current");
      });
      apply(index);
      if (focus) tabs[index].focus();
    }

    // The panels are only hidden while the tabs are in use (mobile); on desktop both
    // stay visible whatever was last picked.
    function apply(index = tabs.findIndex((tab) => tab.getAttribute("aria-selected") === "true")) {
      const tabbed = mobileQuery.matches;
      tablist.hidden = !tabbed;
      panels.forEach((panel, i) => {
        if (panel) panel.hidden = tabbed && i !== index;
      });
    }

    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(i));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        select((i + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length, true);
      });
    });
    mobileQuery.addEventListener("change", () => apply());
    select(0);
    return { showToc: () => select(tabs.length - 1) };
  }

  function initSummary() {
    const summary = document.querySelector('[data-role="title-summary"]');
    const toggle = document.querySelector('[data-role="title-summary-toggle"]');
    if (!summary || !toggle) return;

    summary.classList.add("title-summary--clamped");
    const overflows = () => summary.scrollHeight > summary.clientHeight + 1;
    toggle.hidden = !overflows();

    toggle.addEventListener("click", () => {
      const expanded = summary.classList.toggle("title-summary--clamped") === false;
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      toggle.textContent = expanded ? "Свернуть" : "Читать полностью";
    });
  }

  function initChapterSelection() {
    const form = document.querySelector('[data-role="chapter-toc-form"]');
    const button = document.querySelector('[data-role="toc-select-toggle"]');
    if (!form || !button) return () => {};

    form.classList.add("toc--selectable");
    button.hidden = false;

    function setSelecting(on) {
      form.classList.toggle("toc--selecting", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.textContent = on ? "Готово" : "Выбрать главы";
      if (!on) {
        form.querySelectorAll(".toc__chapter-checkbox:checked").forEach((box) => {
          box.checked = false;
        });
        form.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    button.addEventListener("click", () => setSelecting(!form.classList.contains("toc--selecting")));
    return () => setSelecting(true);
  }

  function initTitlePageUi() {
    const tabs = initTabs();
    initSummary();
    const startSelecting = initChapterSelection();

    window.titlePageUi = {
      startChapterSelection() {
        tabs?.showToc();
        startSelecting();
        const form = document.querySelector('[data-role="chapter-toc-form"]');
        form?.scrollIntoView({ block: "start" });
        form?.focus({ preventScroll: true });
      },
    };
  }

  window.initTitlePageUi = initTitlePageUi;
})();
