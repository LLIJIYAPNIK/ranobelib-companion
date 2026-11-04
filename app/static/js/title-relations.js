// «Связанные релизы» (PR 361): the title's novels, manga and anime, fetched into the end of
// the reading column once the page's content is in (app/api/titles.py's
// title_relations_fragment), so get_relations() never holds up the first render. A 204 -
// no relations, or they couldn't be fetched - drops the slot and the page goes without.
//
// Like title-size-estimate.js, the slot only exists once title-content-load.js has
// injected the content, so this exposes an init function that call site runs.
//
// With two or more groups the group names become tabs and one strip shows at a time;
// without this script every group shows under its own name. The arrows scroll the strip in
// view and only appear when it holds more than it shows.
//
// PR 372: a slot with data-compact (a title already being read) shows the section folded into its one-line
// peek; the peek opens the whole of it.
(() => {
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function fold(section) {
    const peek = section.querySelector('[data-role="relations-peek"]');
    const body = section.querySelector('[data-role="relations-body"]');
    if (!peek || !body) return;
    section.classList.add("tp-rel--compact");
    peek.hidden = false;
    body.hidden = true;
    peek.addEventListener("click", () => {
      section.classList.remove("tp-rel--compact");
      peek.setAttribute("aria-expanded", "true");
      peek.hidden = true;
      body.hidden = false;
      window.dispatchEvent(new Event("resize")); // the strip's arrows measure it now
      const first = body.querySelector('[data-role="relations-tab"][aria-selected="true"]') || body.querySelector("a, button");
      if (first) first.focus({ preventScroll: true });
    });
  }

  function enhance(section) {
    const tabsBox = section.querySelector('[data-role="relations-tabs"]');
    const tabs = [...section.querySelectorAll('[data-role="relations-tab"]')];
    const panels = [...section.querySelectorAll('[data-role="relations-panel"]')];
    const prev = section.querySelector('[data-role="relations-prev"]');
    const next = section.querySelector('[data-role="relations-next"]');
    let strip = panels[0].querySelector('[data-role="relations-strip"]');

    function sync() {
      const over = strip.scrollWidth > strip.clientWidth + 2;
      section.classList.toggle("tp-rel--overflow", over);
      prev.disabled = strip.scrollLeft <= 2;
      next.disabled = strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 2;
    }

    function select(tab, { focus = false } = {}) {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.group !== tab.dataset.group;
        if (!panel.hidden) strip = panel.querySelector('[data-role="relations-strip"]');
      });
      if (focus) tab.focus();
      sync();
    }

    if (tabsBox && tabs.length) {
      tabsBox.hidden = false;
      tabsBox.setAttribute("role", "tablist");
      tabsBox.setAttribute("aria-label", "Связанные релизы");
      section.classList.add("tp-rel--tabbed");
      tabs.forEach((tab) => {
        const panel = panels.find((p) => p.dataset.group === tab.dataset.group);
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", panel.id);
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tab.id);
        tab.addEventListener("click", () => select(tab));
      });
      tabsBox.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        const i = tabs.indexOf(event.target.closest('[data-role="relations-tab"]'));
        if (i < 0) return;
        event.preventDefault();
        const step = event.key === "ArrowRight" ? 1 : -1;
        select(tabs[(i + step + tabs.length) % tabs.length], { focus: true });
      });
      select(tabs[0]);
    }

    [prev, next].forEach((button, k) => button.addEventListener("click", () => {
      strip.scrollBy({ left: (k ? 1 : -1) * strip.clientWidth * 0.8, behavior: reduced() ? "auto" : "smooth" });
    }));
    panels.forEach((panel) => {
      panel.querySelector('[data-role="relations-strip"]').addEventListener("scroll", sync, { passive: true });
    });
    window.addEventListener("resize", sync);
    sync();
  }

  function initTitleRelations() {
    const slot = document.querySelector('[data-role="title-relations-slot"]');
    if (!slot || slot.dataset.loading) return;
    slot.dataset.loading = "true";

    fetch(`/titles/${slot.dataset.slugUrl}/relations`)
      .then((response) => (response.status === 200 ? response.text() : ""))
      .then((html) => {
        if (!html) {
          slot.remove();
          return;
        }
        slot.innerHTML = html;
        const section = slot.querySelector('[data-role="title-relations"]');
        if (!section) return;
        enhance(section);
        if (slot.dataset.compact) fold(section);
      })
      .catch(() => slot.remove());
  }

  window.initTitleRelations = initTitleRelations;
})();
