// «Полки» (library.html): the home page's «Витрина», once per shelf.
//
// On each shelf one book is brought forward: its cover lit and a little larger, its
// details shown beside the shelf (every book's details share one cell and cross-fade),
// the shelf's backdrop and the page's glow following its cover. On a computer pointing
// at a cover or focusing it brings it forward; the shelf is held and dragged sideways or
// paged with ‹ ›, each arrow shown only while there is somewhere to go that way and both
// gone when the shelf fits. On a phone the shelf is the home page's strip: the cover in
// the middle is the one brought forward, worked out on every frame of the swipe, so its
// details change as it passes the middle; tapping a side cover brings it to the middle.
// A tap or click on the book in front opens it (the cover is its title's link).
//
// Above the shelves: «Все / Читаю / Не начаты / Дочитаны» (which shelves show), a search
// inside the library and the sort - all over what the server rendered (a personal library
// is small), each shelf sorted within itself. The tab counts follow the search.
//
// library-card-actions.js takes a removed title's cover and details away (both carry
// data-role="library-item") and puts them back on «Вернуть»; either way it sends
// "library:changed", and the shelves recount and bring another book forward if needed.
//
// Issue 396: a shelf with nothing to show - none rendered by the server, emptied by the
// search or by a removal - isn't on the page; the first shelf shown is the big one. When
// the tab or the search leaves no shelf at all, one line under the tools says why.
(() => {
  const titles = document.querySelector('[data-role="library-titles"]');
  if (!titles) return;

  const phone = window.matchMedia("(max-width: 767px)");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const HOVER_INTENT_MS = 90;
  const view = { tab: "all", query: "", sort: "recent" };

  // A cover image swapped by fading the next one in over the last once it has loaded,
  // so it never dips to nothing in between (as home-flow.js does).
  function crossfade(img) {
    if (!img) return () => {};
    const layers = [img, img.cloneNode()];
    layers[1].classList.add("is-hidden");
    img.after(layers[1]);
    let shown = 0;
    return (src) => {
      if (!src || layers[shown].getAttribute("src") === src) return;
      const next = layers[1 - shown];
      next.onload = () => {
        next.onload = null;
        if (next.getAttribute("src") !== src) return;
        next.classList.remove("is-hidden");
        layers[shown].classList.add("is-hidden");
        shown = 1 - shown;
      };
      next.src = src;
    };
  }

  const glow = crossfade(document.querySelector('[data-role="library-ambient"]'));
  const coverOf = (book) => book.querySelector("img")?.getAttribute("src") || "";

  const byRecent = (a, b) =>
    b.dataset.read.localeCompare(a.dataset.read) || b.dataset.added.localeCompare(a.dataset.added);
  const SORTS = {
    recent: byRecent,
    added: (a, b) => b.dataset.added.localeCompare(a.dataset.added),
    name: (a, b) => a.dataset.name.localeCompare(b.dataset.name, "ru"),
    progress: (a, b) => Number(b.dataset.progress) - Number(a.dataset.progress) || byRecent(a, b),
  };

  const shelves = [...titles.querySelectorAll("[data-shelf]")].map(makeShelf);

  function makeShelf(section) {
    const key = section.dataset.shelf;
    const body = section.querySelector('[data-role="shelf-body"]');
    const row = section.querySelector('[data-role="shelf-row"]');
    const panelsBox = section.querySelector(".lb-shelf__panels");
    const count = section.querySelector('[data-role="shelf-count"]');
    const pos = section.querySelector('[data-role="shelf-pos"]');
    const nav = section.querySelector('[data-role="shelf-nav"]');
    const [back, forward] = nav.querySelectorAll("[data-scroll]");
    const art = crossfade(section.querySelector('[data-role="shelf-art"]'));
    let active = null; // the slug brought forward
    let timer = 0;
    let frame = 0;
    let drag = null;

    section.dataset.live = "";
    for (const panel of panelsBox.querySelectorAll("[data-panel]")) panel.hidden = false;

    const books = () => [...row.querySelectorAll("[data-book]")];
    const shown = () => books().filter((book) => !book.hidden);
    const panelFor = (slug) =>
      panelsBox.querySelector(`[data-panel][data-slug-url="${CSS.escape(slug)}"]`);

    function choose(book, { follow = true } = {}) {
      const slug = book.dataset.slugUrl;
      const list = shown();
      pos.textContent = `${list.indexOf(book) + 1} / ${list.length}`;
      if (slug === active) return;
      active = slug;
      for (const other of books()) {
        const on = other === book;
        other.classList.toggle("is-active", on);
        if (on) other.setAttribute("aria-current", "true");
        else other.removeAttribute("aria-current");
      }
      for (const panel of panelsBox.querySelectorAll("[data-panel]")) {
        panel.classList.toggle("is-active", panel.dataset.slugUrl === slug);
      }
      art(coverOf(book));
      if (follow) glow(coverOf(book));
    }

    function centre(book) {
      row.scrollTo({
        left: book.offsetLeft + book.offsetWidth / 2 - row.clientWidth / 2,
        behavior: reduced.matches ? "auto" : "smooth",
      });
    }

    // On a phone: the book nearest the middle of the strip.
    function middle() {
      const mid = row.scrollLeft + row.clientWidth / 2;
      const off = (book) => Math.abs(book.offsetLeft + book.offsetWidth / 2 - mid);
      const list = shown();
      if (!list.length) return;
      choose(list.reduce((best, book) => (off(book) < off(best) ? book : best)));
    }

    function ends() {
      const fits = row.scrollWidth <= row.clientWidth + 4;
      row.classList.toggle("can-scroll", !fits);
      nav.hidden = fits;
      back.hidden = row.scrollLeft < 4;
      forward.hidden = row.scrollLeft + row.clientWidth > row.scrollWidth - 4;
    }

    // Sort, search and the tab: what this shelf shows, and in which order. A new sort,
    // search or tab starts the shelf over from its first book; a removal or «Вернуть»
    // keeps the one in front if it is still there.
    function apply(restart) {
      if (restart) {
        active = null;
        row.scrollLeft = 0;
      }
      const sort = SORTS[view.sort];
      for (const box of [row, panelsBox]) {
        const items = [...box.querySelectorAll('[data-role="library-item"]')].sort(sort);
        box.append(...items);
      }
      for (const item of section.querySelectorAll('[data-role="library-item"]')) {
        item.hidden = Boolean(view.query) && !item.dataset.name.includes(view.query);
      }
      const list = shown();
      count.textContent = String(list.length);
      body.hidden = list.length === 0;
      section.hidden = list.length === 0 || (view.tab !== "all" && view.tab !== key);
      if (list.length) {
        const current = list.find((book) => book.dataset.slugUrl === active);
        if (!current) active = null;
        choose(current || list[0], { follow: false });
        if (phone.matches) centre(current || list[0]);
      }
      requestAnimationFrame(ends);
      return list.length;
    }

    row.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "mouse") row.classList.add("is-pointing");
    });
    row.addEventListener("pointerleave", () => {
      clearTimeout(timer);
      if (!drag) row.classList.remove("is-pointing");
    });
    row.addEventListener("pointerover", (event) => {
      const book = event.target.closest("[data-book]");
      if (!book || event.pointerType !== "mouse" || drag) return;
      clearTimeout(timer);
      timer = setTimeout(() => choose(book), HOVER_INTENT_MS);
    });
    row.addEventListener("focusin", (event) => {
      const book = event.target.closest("[data-book]");
      if (!book) return;
      if (phone.matches) centre(book);
      else choose(book);
    });
    // The first tap on a book not in front brings it forward; the book in front opens.
    row.addEventListener("click", (event) => {
      const book = event.target.closest("[data-book]");
      if (!book || book.dataset.slugUrl === active) return;
      event.preventDefault();
      if (phone.matches) centre(book);
      else choose(book);
    });

    for (const button of [back, forward]) {
      button.addEventListener("click", () =>
        row.scrollBy({
          left: Number(button.dataset.scroll) * row.clientWidth * 0.8,
          behavior: reduced.matches ? "auto" : "smooth",
        })
      );
    }
    row.addEventListener(
      "scroll",
      () => {
        ends();
        if (phone.matches && !frame) {
          frame = requestAnimationFrame(() => {
            frame = 0;
            middle();
          });
        }
      },
      { passive: true }
    );
    new ResizeObserver(ends).observe(row);

    // Held and dragged with the mouse. A drag that moved isn't a click on the cover under it.
    const swallow = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    row.addEventListener("pointerdown", (event) => {
      if (event.pointerType !== "mouse" || event.button !== 0) return;
      if (!row.classList.contains("can-scroll")) return;
      drag = { x: event.clientX, left: row.scrollLeft, moved: false, id: event.pointerId };
    });
    row.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const dx = event.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 5) return;
      if (!drag.moved) {
        drag.moved = true;
        row.setPointerCapture(drag.id);
        row.classList.add("is-dragging");
        clearTimeout(timer);
      }
      row.scrollLeft = drag.left - dx;
    });
    const drop = () => {
      if (!drag) return;
      if (drag.moved) {
        row.classList.remove("is-dragging");
        row.addEventListener("click", swallow, { capture: true, once: true });
        setTimeout(() => row.removeEventListener("click", swallow, { capture: true }), 0);
      }
      drag = null;
    };
    row.addEventListener("pointerup", drop);
    row.addEventListener("pointercancel", drop);

    return {
      key,
      section,
      apply,
      matching: () => books().filter((book) => !view.query || book.dataset.name.includes(view.query)).length,
      first: () => shown()[0],
    };
  }

  // --- The controls. ---
  const tools = titles.querySelector('[data-role="library-tools"]');
  const tabs = [...tools.querySelectorAll("[data-shelf-tab]")];
  const search = tools.querySelector('[data-role="library-search"]');
  const sortBox = tools.querySelector('[data-role="library-sort"]');
  const sortButton = sortBox.querySelector("button");
  const sortList = sortBox.querySelector('[role="listbox"]');
  const options = [...sortList.querySelectorAll('[role="option"]')];
  const none = titles.querySelector('[data-role="library-none"]');
  tools.hidden = false;

  // A tab's name without its count, for «На полке «Дочитаны» нет «…».».
  const tabName = (key) =>
    tabs.find((tab) => tab.dataset.shelfTab === key)?.firstChild.textContent.trim() || "";
  // What an empty tab says, the search aside.
  const NOTHING = {
    all: "Пока пусто.",
    reading: "Сейчас нет начатых тайтлов: откройте главу — тайтл встанет на эту полку.",
    new: "Все тайтлы в библиотеке уже начаты.",
    done: "Дочитанных тайтлов пока нет.",
  };

  function render(restart = true) {
    for (const shelf of shelves) shelf.apply(restart);
    const visible = shelves.filter((shelf) => !shelf.section.hidden);
    visible.forEach((shelf, i) => shelf.section.classList.toggle("lb-shelf--small", i > 0));
    none.hidden = visible.length > 0;
    if (!visible.length) {
      if (!view.query) none.textContent = NOTHING[view.tab];
      else if (view.tab === "all") none.textContent = `В библиотеке нет «${view.query}».`;
      else none.textContent = `На полке «${tabName(view.tab)}» нет «${view.query}».`;
    }
    const counts = Object.fromEntries(shelves.map((shelf) => [shelf.key, shelf.matching()]));
    counts.all = Object.values(counts).reduce((sum, n) => sum + n, 0);
    for (const tab of tabs) {
      // A shelf the server didn't render (it had no titles) counts 0.
      tab.querySelector('[data-role="tab-count"]').textContent = String(counts[tab.dataset.shelfTab] ?? 0);
    }
    // The page's glow: the first shelf on show's book in front.
    const lead = visible[0]?.first();
    if (lead) glow(coverOf(lead));
  }

  function pickTab(tab, focus = false) {
    view.tab = tab.dataset.shelfTab;
    for (const other of tabs) {
      other.setAttribute("aria-checked", String(other === tab));
      other.tabIndex = other === tab ? 0 : -1;
    }
    if (focus) tab.focus();
    render();
  }
  for (const tab of tabs) tab.addEventListener("click", () => pickTab(tab));
  tools.querySelector('[role="radiogroup"]').addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const at = tabs.findIndex((tab) => tab.getAttribute("aria-checked") === "true");
    pickTab(tabs[(at + step + tabs.length) % tabs.length], true);
  });

  search.addEventListener("input", () => {
    view.query = search.value.trim().toLowerCase();
    render();
  });

  // The sort: a button and its list. ↑/↓ walk the options, Enter or Space picks, Esc closes.
  let at = 0;
  const mark = (i) => {
    at = i;
    options.forEach((option, j) => option.classList.toggle("is-at", j === i));
  };
  function openSort(open) {
    sortList.hidden = !open;
    sortButton.setAttribute("aria-expanded", String(open));
    if (open) {
      mark(options.findIndex((option) => option.dataset.sort === view.sort));
      sortList.focus();
    }
  }
  function pickSort(option) {
    view.sort = option.dataset.sort;
    for (const other of options) other.setAttribute("aria-selected", String(other === option));
    sortBox.querySelector('[data-role="library-sort-label"]').textContent = option.textContent;
    sortButton.setAttribute("aria-label", `Сортировка: ${option.textContent}`);
    openSort(false);
    sortButton.focus();
    render();
  }
  sortButton.addEventListener("click", () => openSort(sortList.hidden));
  sortList.addEventListener("click", (event) => {
    const option = event.target.closest('[role="option"]');
    if (option) pickSort(option);
  });
  sortList.addEventListener("pointermove", (event) => {
    const option = event.target.closest('[role="option"]');
    if (option) mark(options.indexOf(option));
  });
  sortList.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      mark((at + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pickSort(options[at]);
    } else if (event.key === "Escape" || event.key === "Tab") {
      openSort(false);
      sortButton.focus();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!sortList.hidden && !sortBox.contains(event.target)) openSort(false);
  });

  document.addEventListener("library:changed", () => render(false));
  render(false);
})();
