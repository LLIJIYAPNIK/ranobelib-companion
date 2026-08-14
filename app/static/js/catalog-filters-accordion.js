// Collapsible sections inside the filters panel (PR 99, доработка PR 85/98) - the genre
// list alone runs to 50+ checkboxes, taking up most of the panel even for a visitor who
// only cares about narrowing by country. Each section's header is its own accordion
// toggle, independent of the others. Sections render fully expanded with no `hidden`
// anywhere (see catalog.html), so collapsing still works exactly as before for anyone
// without JS - it just can't be collapsed.
(() => {
  const STORAGE_KEY = "catalogFiltersCollapsedSections";

  function loadCollapsedKeys() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      return new Set(Array.isArray(stored) ? stored : []);
    } catch {
      return new Set();
    }
  }

  const collapsedKeys = loadCollapsedKeys();

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...collapsedKeys]));
  }

  document.querySelectorAll('[data-role="catalog-filters-section"]').forEach((section) => {
    const toggle = section.querySelector('[data-role="catalog-filters-section-toggle"]');
    const options = section.querySelector('[data-role="catalog-filters-section-options"]');
    const key = section.dataset.sectionKey;
    if (!toggle || !options || !key) return;

    function isCollapsed() {
      return section.classList.contains("catalog-filters__section--collapsed");
    }

    // Animates max-height from/to the options list's own measured height rather than a
    // hardcoded value - a couple of country radios and 50+ genre checkboxes need very
    // different heights, and app.css's transition only fires on an actual property
    // change, so collapsing needs an explicit starting height even when the list was
    // sitting at its natural (unset) height a moment before.
    function collapse() {
      options.style.maxHeight = `${options.scrollHeight}px`;
      void options.offsetHeight; // force layout so the browser registers that height...
      requestAnimationFrame(() => {
        options.style.maxHeight = "0px"; // ...before animating down to this one
      });
      section.classList.add("catalog-filters__section--collapsed");
      toggle.setAttribute("aria-expanded", "false");
    }

    function expand() {
      options.style.maxHeight = `${options.scrollHeight}px`;
      section.classList.remove("catalog-filters__section--collapsed");
      toggle.setAttribute("aria-expanded", "true");
      options.addEventListener(
        "transitionend",
        () => {
          // Stops clipping future content changes (e.g. a genre re-checked elsewhere
          // changing the count label's width isn't a height change, but nothing here
          // guarantees the list's height never changes again) to an animation-time
          // snapshot, without touching the max-height mid-transition to avoid a jump.
          if (!isCollapsed()) options.style.maxHeight = "none";
        },
        { once: true }
      );
    }

    // Applied once, synchronously, before the panel itself is ever shown (see
    // catalog-filters-toggle.js, deferred and included right before this script) - the
    // section just starts in its remembered state with nothing to animate, rather than
    // flashing expanded-then-collapsed on every page load.
    if (collapsedKeys.has(key)) {
      section.classList.add("catalog-filters__section--collapsed");
      toggle.setAttribute("aria-expanded", "false");
      options.style.maxHeight = "0px";
    }

    toggle.addEventListener("click", () => {
      if (isCollapsed()) {
        expand();
        collapsedKeys.delete(key);
      } else {
        collapse();
        collapsedKeys.add(key);
      }
      persist();
    });
  });
})();
