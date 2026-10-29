// «Разделы» (catalog.html): the lamp beside the catalog's wall, the phone's strip, the
// title's bottom sheet and the genre bar.
//
// The lamp (a computer, 1024px and up): pointing at a cover lights it after a short pause
// (so sweeping across the wall doesn't flicker the lamp), the rest of the wall dims, and
// the lamp cross-fades to what the card can't say - GET /titles/{slug}/lamp, one
// get_info() for the one title lit, each kept here for the page's life; focus lights at
// once, so Tab walks the wall. The lamp's backdrop and the page's glow follow the lit
// cover. It opens on the wall's first title; a click on a cover still opens the title.
//
// Narrower, there is no lamp: a tap on a cover opens the same markup in the shared bottom
// sheet (bottom-sheet.js - pulled down to close, the page held still under it) instead
// of the title page; without script a cover is a plain link.
//
// The strip (phones, a section's first eight): one big cover in the middle; on every
// frame of a swipe the cover nearest the middle is the chosen one, and its name, facts
// and «2 / 8» change at once - not once the swipe stops. A tap on a side cover brings it
// to the middle; on the middle one, its sheet.
//
// The genre bar scrolls sideways - held and dragged with the mouse (a drag isn't a click
// on the chip under it), faded at an edge only while there is more that way - and «Все
// жанры» opens the filters.
(() => {
  const page = document.querySelector('[data-role="catalog-page"]');
  if (!page) return;
  const wide = window.matchMedia("(min-width: 1024px)");
  const HOVER_INTENT_MS = 90;
  const FADE_MS = 120;

  // A cover image swapped by fading the next one in over the last once it has loaded,
  // so it never dips to nothing in between (as library-shelves.js / home-flow.js).
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
  const glow = crossfade(page.querySelector('[data-role="catalog-ambient"]'));
  const coverOf = (el) => el?.querySelector("img")?.getAttribute("src") || "";

  // One fetch per title. A failed one is forgotten, so the next point retries it; a
  // «В библиотеку» flip makes the kept copy stale (catalog-library-toggle.js).
  const kept = new Map();
  function details(slug) {
    if (!kept.has(slug)) {
      const request = fetch(`/titles/${encodeURIComponent(slug)}/lamp`).then((r) =>
        r.ok ? r.text() : Promise.reject(new Error(String(r.status)))
      );
      request.catch(() => kept.delete(slug));
      kept.set(slug, request);
    }
    return kept.get(slug);
  }
  document.addEventListener("catalog:library", (event) => kept.delete(event.detail.slug));

  const escapeHtml = (s) =>
    s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  // When the details don't come: the name, and the way to the title page.
  const fallback = (slug, name) =>
    `<div class="cg-lamp__body cg-lamp__body--missing"><h3 class="cg-lamp__name">${escapeHtml(name)}</h3>` +
    '<p class="cg-lamp__summary">Не удалось загрузить описание.</p>' +
    `<div class="cg-lamp__actions"><a class="hm-go" href="/titles/${encodeURIComponent(slug)}"><span class="hm-go__label">Открыть тайтл</span></a></div></div>`;
  const nameOf = (card) => card.querySelector(".catalog-card__title")?.textContent.trim() || "";

  // ---- The lamp ----
  const grid = page.querySelector('[data-role="catalog-grid"]');
  const lamp = page.querySelector('[data-role="catalog-lamp"]');
  const inner = lamp?.querySelector('[data-role="catalog-lamp-in"]');
  const lampArt = crossfade(lamp?.querySelector('[data-role="catalog-lamp-art"]'));
  let lit = null;
  let litCard = null;
  let pointTimer = 0;

  async function light(card) {
    if (!lamp || !card) return;
    litCard?.classList.remove("is-lit");
    card.classList.add("is-lit");
    litCard = card;
    const slug = card.dataset.slugUrl;
    if (slug === lit) return;
    lit = slug;
    const cover = coverOf(card);
    glow(cover);
    lampArt(cover);
    const started = performance.now();
    inner.classList.add("is-out");
    let html;
    try {
      html = await details(slug);
    } catch {
      html = fallback(slug, nameOf(card));
    }
    const wait = Math.max(0, FADE_MS - (performance.now() - started));
    window.setTimeout(() => {
      if (lit !== slug) return;
      inner.innerHTML = html;
      inner.classList.remove("is-out");
    }, wait);
  }

  function placeLamp() {
    if (!lamp) return;
    lamp.hidden = !wide.matches;
    if (wide.matches && !lit) light(grid?.querySelector('[data-role="catalog-card"]'));
  }

  if (grid && lamp) {
    grid.addEventListener("pointerover", (event) => {
      const card = event.target.closest('[data-role="catalog-card"]');
      if (!card || event.pointerType !== "mouse" || !wide.matches) return;
      grid.classList.add("is-pointing");
      window.clearTimeout(pointTimer);
      pointTimer = window.setTimeout(() => light(card), HOVER_INTENT_MS);
    });
    grid.addEventListener("pointerleave", () => {
      window.clearTimeout(pointTimer);
      grid.classList.remove("is-pointing");
    });
    grid.addEventListener("focusin", (event) => {
      const card = event.target.closest('[data-role="catalog-card"]');
      if (card && wide.matches) light(card);
    });
    wide.addEventListener("change", placeLamp);
    placeLamp();
  }

  // ---- The title's bottom sheet (no lamp) ----
  const sheet = document.createElement("div");
  sheet.className = "cg-sheet";
  sheet.hidden = true;
  page.append(sheet);
  let sheetFor = null;

  async function openSheet(slug, name, opener) {
    if (!window.bottomSheet) return false;
    sheetFor = slug;
    sheet.innerHTML =
      '<div class="cg-lamp__body" aria-busy="true"><div class="cg-lamp__top">' +
      '<span class="ui-skeleton ui-skeleton--cover cg-lamp__cover"></span>' +
      '<div class="cg-lamp__side"><span class="ui-skeleton ui-skeleton--line"></span>' +
      '<span class="ui-skeleton ui-skeleton--line" style="width: 60%"></span></div></div></div>';
    window.bottomSheet.open({ title: "О тайтле", content: sheet, opener });
    let html;
    try {
      html = await details(slug);
    } catch {
      html = fallback(slug, name);
    }
    if (sheetFor === slug) sheet.innerHTML = html;
    return true;
  }

  page.addEventListener("click", (event) => {
    if (wide.matches || event.defaultPrevented) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const link = event.target.closest(".catalog-card__link");
    const card = link?.closest('[data-role="catalog-card"]');
    if (!card || !window.bottomSheet) return;
    event.preventDefault();
    openSheet(card.dataset.slugUrl, nameOf(card), link);
  });

  // ---- The strip (phones) ----
  const stage = page.querySelector('[data-role="catalog-stage"]');
  if (stage) {
    const row = stage.querySelector('[data-role="catalog-stage-row"]');
    const books = [...row.querySelectorAll('[data-role="catalog-stage-book"]')];
    const nameEl = stage.querySelector('[data-role="catalog-stage-name"]');
    const factsEl = stage.querySelector('[data-role="catalog-stage-facts"]');
    const posEl = stage.querySelector('[data-role="catalog-stage-pos"]');
    const stageArt = crossfade(stage.querySelector('[data-role="catalog-stage-art"]'));
    let mid = 0;
    let frame = 0;

    function choose(i) {
      if (i === mid) return;
      mid = i;
      books.forEach((b, j) => b.classList.toggle("is-mid", j === i));
      const book = books[i];
      nameEl.textContent = book.dataset.name;
      factsEl.textContent = book.dataset.facts;
      posEl.textContent = `${i + 1} / ${books.length}`;
      stageArt(coverOf(book));
      glow(coverOf(book));
    }
    function middle() {
      const centre = row.scrollLeft + row.clientWidth / 2;
      const off = (b) => Math.abs(b.offsetLeft + b.offsetWidth / 2 - centre);
      let best = 0;
      books.forEach((b, i) => {
        if (off(b) < off(books[best])) best = i;
      });
      choose(best);
    }
    row.addEventListener(
      "scroll",
      () => {
        if (!frame) {
          frame = window.requestAnimationFrame(() => {
            frame = 0;
            middle();
          });
        }
      },
      { passive: true }
    );
    row.addEventListener("click", (event) => {
      const book = event.target.closest('[data-role="catalog-stage-book"]');
      if (!book || event.metaKey || event.ctrlKey) return;
      event.preventDefault();
      const i = books.indexOf(book);
      if (i === mid) {
        openSheet(book.dataset.slugUrl, book.dataset.name, book);
        return;
      }
      row.scrollTo({ left: book.offsetLeft + book.offsetWidth / 2 - row.clientWidth / 2, behavior: "smooth" });
    });
  }

  // ---- The genre bar ----
  const genreRow = page.querySelector('[data-role="catalog-genre-bar-row"]');
  if (genreRow) {
    const ends = () => {
      genreRow.classList.toggle("at-start", genreRow.scrollLeft < 4);
      genreRow.classList.toggle(
        "at-end",
        genreRow.scrollLeft + genreRow.clientWidth > genreRow.scrollWidth - 4
      );
    };
    genreRow.addEventListener("scroll", ends, { passive: true });
    new ResizeObserver(ends).observe(genreRow);
    // The chip that is on, in view.
    genreRow.querySelector('[aria-current="true"]')?.scrollIntoView({ block: "nearest", inline: "center" });
    ends();

    let drag = null;
    const swallow = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    genreRow.addEventListener("pointerdown", (event) => {
      if (event.pointerType !== "mouse" || event.button !== 0) return;
      drag = { x: event.clientX, left: genreRow.scrollLeft, moved: false, id: event.pointerId };
    });
    genreRow.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const dx = event.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 5) return;
      if (!drag.moved) {
        drag.moved = true;
        genreRow.setPointerCapture(drag.id);
        genreRow.classList.add("is-dragging");
      }
      genreRow.scrollLeft = drag.left - dx;
    });
    const drop = () => {
      if (drag?.moved) {
        genreRow.classList.remove("is-dragging");
        genreRow.addEventListener("click", swallow, { capture: true, once: true });
        window.setTimeout(() => genreRow.removeEventListener("click", swallow, { capture: true }), 0);
      }
      drag = null;
    };
    genreRow.addEventListener("pointerup", drop);
    genreRow.addEventListener("pointercancel", drop);
  }

  // «Все жанры»: the filters - the popover's toggle, or on phones the sheet's button.
  page.querySelector('[data-role="catalog-genre-bar-all"]')?.addEventListener("click", () => {
    const sheetButton = page.querySelector(".catalog-toolbar__sheet-filters");
    const target =
      sheetButton && !sheetButton.hidden && sheetButton.offsetParent
        ? sheetButton
        : page.querySelector('[data-role="catalog-filters-toggle"]');
    target?.click();
  });
})();
