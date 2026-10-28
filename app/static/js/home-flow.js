// «Витрина» (app/templates/home/_flow.html): pointing at a cover (or focusing it from the
// keyboard) brings that book forward - it widens and rises, the rest dim and narrow -
// and its details replace the panel; a click on the cover in front opens its title as
// the plain link it is. On a phone the shelf is a strip swiped sideways: the book that
// comes to rest in the middle is chosen; tapping a side cover brings it to the middle,
// tapping the middle one opens it.
(() => {
  const root = document.querySelector("[data-flow]");
  if (!root) return;

  const shelf = root.querySelector("[data-flow-shelf]");
  const books = [...root.querySelectorAll("[data-flow-book]")];
  const panels = [...root.querySelectorAll("[data-flow-panel]")];
  const HOVER_INTENT_MS = 90;

  // Two backdrop layers: the next cover fades in over the last once it has loaded, so
  // the backdrop never dips to dark in between.
  const art = root.querySelector("[data-flow-art]");
  const arts = art ? [art, art.cloneNode()] : [];
  if (art) {
    arts[1].classList.add("is-hidden");
    art.after(arts[1]);
  }
  let shown = 0;

  // Panels share one cell and cross-fade (app.css); without script only the first shows.
  root.dataset.live = "";
  for (const panel of panels) panel.hidden = false;

  let active = 0;

  function stack() {
    // Books further from the chosen one go further back.
    books.forEach((book, i) => book.style.setProperty("--z", String(30 - Math.abs(i - active))));
  }

  function choose(index) {
    if (index === active) return;
    active = index;
    books.forEach((book, i) => {
      book.classList.toggle("is-active", i === index);
      if (i === index) book.setAttribute("aria-current", "true");
      else book.removeAttribute("aria-current");
    });
    panels.forEach((panel, i) => panel.classList.toggle("is-active", i === index));
    stack();

    // The backdrop follows the chosen cover.
    const cover = books[index].dataset.cover;
    if (arts.length && cover && arts[shown].getAttribute("src") !== cover) {
      const next = arts[1 - shown];
      const reveal = () => {
        next.onload = null;
        // Moved on meanwhile, or already shown (a cached image is complete at once).
        if (books[active].dataset.cover !== cover || !next.classList.contains("is-hidden")) return;
        next.classList.remove("is-hidden");
        arts[shown].classList.add("is-hidden");
        shown = 1 - shown;
      };
      next.onload = reveal;
      next.src = cover;
      if (next.complete) reveal();
    }
  }

  // Only a real mouse movement chooses: while books resize, other covers pass under a
  // still cursor, and treating that as pointing at them would run the choice on by itself.
  let intent = 0;
  let pointed = -1;
  shelf?.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || (!event.movementX && !event.movementY)) return;
    const book = event.target.closest("[data-flow-book]");
    const index = book ? books.indexOf(book) : -1;
    if (index === pointed) return;
    pointed = index;
    clearTimeout(intent);
    if (index >= 0) intent = setTimeout(() => choose(index), HOVER_INTENT_MS);
  });
  shelf?.addEventListener("pointerleave", () => {
    pointed = -1;
    clearTimeout(intent);
  });
  const phone = window.matchMedia("(max-width: 767px)");

  function centre(book, smooth = true) {
    book.scrollIntoView({ behavior: smooth ? "smooth" : "instant", inline: "center", block: "nearest" });
  }

  // Phone: whichever cover is nearest the strip's middle is the chosen one.
  let ticking = false;
  shelf?.addEventListener(
    "scroll",
    () => {
      if (!phone.matches || ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const middle = shelf.scrollLeft + shelf.clientWidth / 2;
        let nearest = 0;
        books.forEach((book, i) => {
          const at = book.offsetLeft + book.offsetWidth / 2;
          const best = books[nearest].offsetLeft + books[nearest].offsetWidth / 2;
          if (Math.abs(at - middle) < Math.abs(best - middle)) nearest = i;
        });
        choose(nearest);
      });
    },
    { passive: true },
  );

  for (const [i, book] of books.entries()) {
    book.addEventListener("focus", () => {
      if (phone.matches) centre(book);
      else choose(i);
    });
    book.addEventListener("click", (event) => {
      // Touch (and a click faster than the hover intent): bring forward first.
      if (i === active) return;
      event.preventDefault();
      clearTimeout(intent);
      if (phone.matches) centre(book);
      else choose(i);
    });
  }

  stack();
})();
