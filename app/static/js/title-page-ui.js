// «Лампа» - the title page's behaviour on top of _title_content.html. Runs after
// title-content-load.js has injected the fragment (window.initTitlePageUi). Without it every
// volume shows, the folds stay shut and the form still exports what is ticked.
//
// - The shelf: each spine is the tab of its volume's chapters. Spines are laid out in whole
//   pixels so every gap is the same; a shelf wider than its column scrolls, with ‹ › on a
//   computer and a line under it that shows where you are; it opens at the reader's volume.
// - The folds: a read run or a long tail opens and folds again; its head stays on top while
//   you scroll through it, and folding from there brings you back to where the run began.
// - «Номер или название главы»: filters every volume in place.
// - «Выбрать главы»: a box on every row, «Весь том» on every volume, the bar at the foot
//   (chapter-export-panel.js shows it once something is ticked).
// - The default translation saves itself when a choice is made.
// - The share read counts up, the cover leans toward the pointer, and on a phone a dock
//   carries «Продолжить» once the lamp's button has scrolled away.
(() => {
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const phone = () => window.matchMedia("(max-width: 767px)").matches;
  const plural = (n, one, few, many) => {
    const a = n % 10, b = n % 100;
    return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
  };

  function initSummary() {
    const summary = document.querySelector('[data-role="title-summary"]');
    const toggle = document.querySelector('[data-role="title-summary-toggle"]');
    if (!summary || !toggle) return;
    summary.classList.add("tp-summary--clamped");
    toggle.hidden = summary.scrollHeight <= summary.clientHeight + 1;
    toggle.addEventListener("click", () => {
      const open = summary.classList.toggle("tp-summary--clamped") === false;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Свернуть" : "Читать полностью";
    });
  }

  function initFolds(form) {
    function setOpen(fold, open) {
      fold.dataset.open = open ? "true" : "false";
      fold.querySelector('[data-role="toc-fold-toggle"]').setAttribute("aria-expanded", open ? "true" : "false");
      fold.querySelector(".tp-fold__body > ol").inert = !open;
      const n = fold.querySelectorAll('[data-role="toc-chapter"]').length;
      fold.querySelector('[data-role="toc-fold-word"]').textContent =
        `${n} ${plural(n, "глава", "главы", "глав")} · ${open ? "свернуть" : "показать"}`;
    }
    form.addEventListener("click", (event) => {
      const head = event.target.closest('[data-role="toc-fold-toggle"]');
      if (!head) return;
      const fold = head.closest('[data-role="toc-fold"]');
      const open = fold.dataset.open !== "true";
      const stuck = head.getBoundingClientRect().top <= 1;
      setOpen(fold, open);
      if (!open && stuck) fold.scrollIntoView({ block: "start" });
    });
    return setOpen;
  }

  function initShelf(form) {
    const section = document.querySelector('[data-role="title-volumes"]');
    const shelf = document.querySelector('[data-role="shelf"]');
    if (!section || !shelf) return;
    const spines = [...shelf.querySelectorAll('[data-role="shelf-spine"]')];
    const panels = [...form.querySelectorAll('[data-role="toc-volume"]')];
    const prev = section.querySelector('[data-role="shelf-prev"]');
    const next = section.querySelector('[data-role="shelf-next"]');
    const track = section.querySelector('[data-role="shelf-track"]');
    section.classList.add("tp-vols--js");
    form.classList.add("toc--tabbed");

    function select(spine, { focus = false } = {}) {
      spines.forEach((s) => {
        const on = s === spine;
        s.setAttribute("aria-selected", on ? "true" : "false");
        s.tabIndex = on ? 0 : -1;
      });
      panels.forEach((panel) => {
        const on = panel.dataset.volume === spine.dataset.volume;
        panel.hidden = !on;
        if (on && !reduced()) {
          panel.classList.remove("tp-volume--enter");
          void panel.offsetWidth;
          panel.classList.add("tp-volume--enter");
        }
      });
      if (focus) spine.focus();
    }

    // Whole-pixel spines: one gap everywhere, the spare pixels spread one per spine.
    function layout() {
      const style = getComputedStyle(shelf);
      const room = shelf.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const gap = parseFloat(style.columnGap) || 0;
      const min = phone() ? 28 : 22;
      const base = Math.max(min, Math.floor((room - gap * (spines.length - 1)) / spines.length));
      let spare = base > min ? room - gap * (spines.length - 1) - base * spines.length : 0;
      spines.forEach((s) => s.style.setProperty("--w", `${base + (spare-- > 0 ? 1 : 0)}px`));
    }

    function sync() {
      const over = shelf.scrollWidth > shelf.clientWidth + 2;
      section.classList.toggle("tp-vols--overflow", over);
      prev.disabled = shelf.scrollLeft <= 2;
      next.disabled = shelf.scrollLeft + shelf.clientWidth >= shelf.scrollWidth - 2;
      track.style.width = `${(shelf.clientWidth / shelf.scrollWidth) * 100}%`;
      track.style.transform = `translateX(${(shelf.scrollLeft / shelf.clientWidth) * 100}%)`;
    }

    shelf.addEventListener("click", (event) => {
      const spine = event.target.closest('[data-role="shelf-spine"]');
      if (!spine) return;
      event.preventDefault();
      select(spine);
    });
    shelf.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const i = spines.indexOf(event.target.closest('[data-role="shelf-spine"]'));
      if (i < 0) return;
      event.preventDefault();
      const to = spines[Math.max(0, Math.min(spines.length - 1, i + (event.key === "ArrowRight" ? 1 : -1)))];
      select(to, { focus: true });
      to.scrollIntoView({ inline: "nearest", block: "nearest" });
    });
    [prev, next].forEach((button, k) => button.addEventListener("click", () => {
      shelf.scrollBy({ left: (k ? 1 : -1) * shelf.clientWidth * 0.8, behavior: reduced() ? "auto" : "smooth" });
    }));
    shelf.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", () => { layout(); sync(); });

    layout();
    const start = spines.find((s) => s.getAttribute("aria-selected") === "true") || spines[0];
    select(start);
    shelf.scrollLeft = start.offsetLeft - shelf.clientWidth / 2 + start.offsetWidth / 2;
    sync();
    return { select, spines, panels };
  }

  function initFind(form, setFold, shelf) {
    const input = form.querySelector('[data-role="toc-find"]');
    const empty = form.querySelector('[data-role="toc-find-empty"]');
    if (!input) return;
    const rows = [...form.querySelectorAll('[data-role="toc-chapter"]')];
    const folds = [...form.querySelectorAll('[data-role="toc-fold"]')];
    const panels = [...form.querySelectorAll('[data-role="toc-volume"]')];
    let before = null;

    // Enter in the field must not submit the export form.
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") event.preventDefault(); });
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      if (!q) {
        form.classList.remove("toc--finding");
        rows.forEach((row) => (row.hidden = false));
        folds.forEach((fold) => setFold(fold, before?.get(fold) ?? false));
        before = null;
        if (shelf) shelf.select(shelf.spines.find((s) => s.getAttribute("aria-selected") === "true"));
        else panels.forEach((panel) => (panel.hidden = false));
        empty.hidden = true;
        return;
      }
      if (!before) before = new Map(folds.map((fold) => [fold, fold.dataset.open === "true"]));
      form.classList.add("toc--finding");
      let hits = 0;
      rows.forEach((row) => {
        const on = row.dataset.number === q || row.dataset.name.includes(q);
        row.hidden = !on;
        if (on) hits++;
      });
      folds.forEach((fold) => setFold(fold, fold.querySelector('[data-role="toc-chapter"]:not([hidden])') !== null));
      panels.forEach((panel) => (panel.hidden = !panel.querySelector('[data-role="toc-chapter"]:not([hidden])')));
      empty.hidden = hits > 0;
      empty.textContent = `Нет главы с номером или названием «${input.value.trim()}».`;
    });
  }

  function initPicking(form) {
    const toggle = form.querySelector('[data-role="toc-select-toggle"]');
    if (!toggle) return;
    const count = form.querySelector('[data-role="toc-picked-count"]');
    const word = form.querySelector('[data-role="toc-picked-word"]');
    form.classList.add("toc--selectable");
    toggle.hidden = false;
    form.querySelectorAll('[data-role="toc-pick-volume"]').forEach((b) => (b.hidden = false));

    const boxes = () => [...form.querySelectorAll(".toc__chapter-checkbox")];
    function sync() {
      const n = boxes().filter((b) => b.checked).length;
      count.textContent = n;
      word.textContent = plural(n, "глава", "главы", "глав");
    }
    function setSelecting(on) {
      form.classList.toggle("toc--selecting", on);
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      if (!on) boxes().forEach((b) => (b.checked = false));
      form.dispatchEvent(new Event("change", { bubbles: true }));
    }
    toggle.addEventListener("click", () => setSelecting(!form.classList.contains("toc--selecting")));
    form.querySelector('[data-role="toc-pick-cancel"]')?.addEventListener("click", () => setSelecting(false));
    form.addEventListener("click", (event) => {
      const all = event.target.closest('[data-role="toc-pick-volume"]');
      if (all) {
        const volume = all.closest('[data-role="toc-volume"]');
        const inside = [...volume.querySelectorAll(".toc__chapter-checkbox")];
        const every = inside.every((b) => b.checked);
        inside.forEach((b) => (b.checked = !every));
        form.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      // While picking, a row ticks its box instead of opening the chapter.
      const link = event.target.closest(".tp-row__link");
      if (link && form.classList.contains("toc--selecting")) {
        event.preventDefault();
        const box = link.parentElement.querySelector(".toc__chapter-checkbox");
        box.checked = !box.checked;
        form.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    form.addEventListener("change", sync);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && form.classList.contains("toc--selecting")) setSelecting(false);
    });
    sync();
  }

  function initDefaultTranslation() {
    const form = document.querySelector('[data-role="default-translation-form"]');
    if (!form) return;
    form.querySelector('[data-role="default-translation-save"]').hidden = true;
    form.addEventListener("change", () => form.requestSubmit());
  }

  function initMotion() {
    const pct = document.querySelector('[data-role="title-progress-pct"]');
    if (pct && !reduced()) {
      const value = Number(pct.dataset.value);
      const t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / 900);
        pct.textContent = `${Math.round(value * (1 - (1 - k) ** 3))}%`;
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    const tilt = document.querySelector('[data-role="title-cover-tilt"]');
    if (tilt && !reduced() && window.matchMedia("(hover: hover)").matches) {
      tilt.addEventListener("pointermove", (event) => {
        const r = tilt.getBoundingClientRect();
        tilt.classList.add("tp-tilt--live");
        tilt.style.setProperty("--ry", `${((event.clientX - r.left) / r.width - 0.5) * 14}deg`);
        tilt.style.setProperty("--rx", `${(0.5 - (event.clientY - r.top) / r.height) * 14}deg`);
      });
      tilt.addEventListener("pointerleave", () => {
        tilt.classList.remove("tp-tilt--live");
        tilt.style.setProperty("--rx", "0deg");
        tilt.style.setProperty("--ry", "0deg");
      });
    }
  }

  // The dock sits above the phone's bottom bar, however tall the phone makes it; with the
  // navigation at the top instead (?nav=top) it sits at the screen's foot.
  function initDock() {
    const dock = document.querySelector('[data-role="title-dock"]');
    const go = document.querySelector('[data-role="title-go"]');
    if (!dock || !go) return;
    const nav = document.querySelector('[data-role="sidebar"]');
    const place = () => {
      const r = nav?.getBoundingClientRect();
      const bottomBar = r && r.top > window.innerHeight / 2;
      dock.style.setProperty("--tp-nav-h", `${bottomBar ? Math.round(window.innerHeight - r.top) : 0}px`);
    };
    place();
    window.addEventListener("resize", place);
    window.visualViewport?.addEventListener("resize", place);
    new IntersectionObserver(([entry]) => {
      const on = !entry.isIntersecting && entry.boundingClientRect.top < 0;
      dock.classList.toggle("tp-dock--on", on);
      dock.setAttribute("aria-hidden", on ? "false" : "true");
      dock.tabIndex = on ? 0 : -1;
    }).observe(go);
  }

  function initTitlePageUi() {
    initSummary();
    initDefaultTranslation();
    initMotion();
    initDock();
    const form = document.querySelector('[data-role="chapter-toc-form"]');
    if (!form) return;
    const setFold = initFolds(form);
    const shelf = initShelf(form);
    initFind(form, setFold, shelf);
    initPicking(form);
    // PR 254: the reader's «К оглавлению» / chapter title link here with #title-panel-toc;
    // the fragment arrives after the browser's own jump to the hash, so do it here.
    if (location.hash === "#title-panel-toc") {
      document.getElementById("title-panel-toc")?.scrollIntoView({ block: "start" });
    }
  }

  window.initTitlePageUi = initTitlePageUi;
})();
