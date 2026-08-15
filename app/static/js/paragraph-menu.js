// PR 131: a right-click on any paragraph opens a small context menu anchored at the
// click point - infrastructure for PR 132 (reactions) and PR 133 (comments), neither of
// which exists yet, so both items below are stubs for now.
//
// One delegated "contextmenu" listener on .reader-content itself, not one per paragraph -
// chapter.content arrives from the SDK as a single opaque HTML blob, so the number and
// shape of its children isn't known ahead of time (same reason tap-to-read.js/
// reader-progress.js delegate rather than attach per-paragraph listeners).
//
// "Which paragraph" is identified the same way tap-to-read.js/reader-progress.js already
// do it: the index of a .reader-content direct child among its siblings. In tap-to-read
// mode that child is the plate wrapping the real paragraph (tap-to-read.js replaces each
// original element with one before this script ever runs); in the ordinary reading mode
// it's the paragraph itself. Walking up from event.target to whichever ancestor is a
// direct child of .reader-content works unmodified for both, and lines up with the exact
// same index those two scripts use for their own progress tracking - one shared anchor,
// not a second one invented here.
(() => {
  const GAP = 8;

  const content = document.querySelector('[data-role="chapter"]');
  if (!content) return;

  const slugUrl = content.dataset.slugUrl || "";
  const volume = content.dataset.volume || "";
  const number = content.dataset.number || "";
  const branchId = content.dataset.branchId || "";
  const isAuthenticated = content.dataset.authenticated === "1";

  // Finds the .reader-content child (paragraph plate or bare paragraph) that `target`
  // sits inside, or null if the click landed outside them entirely (e.g. the "tap to
  // continue" hint tap-to-read.js appends after the last revealed paragraph).
  function paragraphElementFor(target) {
    let el = target;
    while (el && el.parentElement !== content) {
      el = el.parentElement;
    }
    return el && el.parentElement === content ? el : null;
  }

  function paragraphKey(index) {
    return `${slugUrl}:${volume}:${number}:${branchId}:${index}`;
  }

  const panel = document.createElement("div");
  panel.className = "paragraph-menu__panel";
  panel.setAttribute("role", "menu");
  document.body.appendChild(panel);

  function isOpen() {
    return panel.classList.contains("paragraph-menu__panel--open");
  }

  function renderItems() {
    panel.replaceChildren();
    for (const label of ["Реакции", "Комментировать"]) {
      if (isAuthenticated) {
        // PR 132/133 replace this with the real feature; until then it's a visible but
        // inert placeholder rather than either doing nothing silently or not existing.
        const item = document.createElement("button");
        item.type = "button";
        item.className = "paragraph-menu__item";
        item.setAttribute("role", "menuitem");
        item.disabled = true;
        item.append(label, " ");
        const soon = document.createElement("span");
        soon.className = "badge badge--muted";
        soon.textContent = "скоро";
        item.append(soon);
        panel.append(item);
      } else {
        // Same principle as _locked_feature.html elsewhere in the app: an anonymous
        // visitor still sees the menu, but its actions route to /login instead of
        // running (there is nothing to react/comment as without an account).
        const item = document.createElement("a");
        item.className = "paragraph-menu__item";
        item.setAttribute("role", "menuitem");
        item.href = "/login";
        item.textContent = label;
        panel.append(item);
      }
    }
  }

  function position(x, y) {
    panel.style.left = "0px";
    panel.style.top = "0px";
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const left = Math.min(x, window.innerWidth - width - GAP);
    const top = Math.min(y, window.innerHeight - height - GAP);
    panel.style.left = `${Math.max(GAP, left)}px`;
    panel.style.top = `${Math.max(GAP, top)}px`;
  }

  function open(x, y, index) {
    // Stashed on the panel for PR 132/133 to read once they give these items real
    // behavior - this stub never uses it beyond that.
    panel.dataset.paragraphKey = paragraphKey(index);
    renderItems();
    panel.classList.add("paragraph-menu__panel--open");
    position(x, y);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
  }

  function close() {
    panel.classList.remove("paragraph-menu__panel--open");
    window.removeEventListener("scroll", close, true);
    window.removeEventListener("resize", close);
  }

  content.addEventListener("contextmenu", (event) => {
    const paragraphEl = paragraphElementFor(event.target);
    if (!paragraphEl) return;
    event.preventDefault();
    const index = Array.prototype.indexOf.call(content.children, paragraphEl);
    open(event.clientX, event.clientY, index);
  });

  document.addEventListener("click", (event) => {
    if (isOpen() && !panel.contains(event.target)) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close();
  });
})();
