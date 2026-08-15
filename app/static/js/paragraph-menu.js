// PR 131: a right-click on any paragraph opens a small context menu anchored at the
// click point - infrastructure for PR 132 (reactions) and PR 133 (comments).
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
//
// PR 132: "Реакции" opens a strip of 10 emoji in place of the two-item list - picking one
// POSTs to /titles/{slug}/chapters/{volume}/{number}/reactions (app/api/chapters.py) and
// refreshes the little counts strip rendered under that paragraph. "Комментировать"
// stays a stub for PR 133.
(() => {
  const GAP = 8;

  // Must stay in sync with ALLOWED_EMOJI in app/db/reactions.py - both lists exist
  // independently (no shared JSON between Python and JS in this codebase), so a change
  // to one needs the same change made to the other. Labels are for aria-label/title only,
  // not sent to the server.
  const EMOJI = [
    ["👍", "Нравится"],
    ["❤️", "Любовь"],
    ["😂", "Смешно"],
    ["😮", "Удивление"],
    ["😢", "Грустно"],
    ["😡", "Злость"],
    ["🔥", "Огонь"],
    ["👏", "Аплодисменты"],
    ["🤔", "Задумчиво"],
    ["💯", "Круто"],
  ];

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

  // Where a paragraph's own reactions strip lives. In tap-to-read mode `content.children
  // [index]` is already tap-to-read.js's own generic <div> plate wrapping the real
  // paragraph (plus PR 64's timestamp span) - the strip is just another child appended
  // there. In the ordinary mode it's the SDK's own raw element, which can be anything the
  // sanitizer allows - including a bare <img>, a void element that can't have children at
  // all - so the first paragraph that actually needs a strip gets lazily wrapped in a
  // plain <div> the same way, replacing itself in `content` with that wrapper and moving
  // inside it. Reparenting like this doesn't disturb reader-progress.js's own
  // IntersectionObserver (already watching the raw element by reference by the time this
  // ever runs - script order in chapter.html puts reader-progress.js before this file -
  // and observation survives an observed node being moved to a new parent, only an
  // explicit unobserve() or removal from the document would stop it), nor
  // paragraphElementFor()/tap-to-read.js's own indexing (content.children keeps the same
  // length and order either way, just with a wrapper standing in for one entry).
  function reactionsHostFor(index) {
    const el = content.children[index];
    if (!el) return null;
    if (el.classList.contains("reader-content__paragraph-wrap")) return el;
    if (el.classList.contains("paragraph-reactions-host")) return el;
    const host = document.createElement("div");
    host.className = "paragraph-reactions-host";
    el.replaceWith(host);
    host.appendChild(el);
    return host;
  }

  function renderStrip(index, counts, mineEmoji) {
    const host = reactionsHostFor(index);
    if (!host) return;
    let strip = host.querySelector(":scope > .paragraph-reactions");
    const entries = Object.entries(counts || {}).filter(([, n]) => n > 0);
    if (entries.length === 0) {
      strip?.remove();
      return;
    }
    if (!strip) {
      strip = document.createElement("div");
      strip.className = "paragraph-reactions";
      host.append(strip);
    }
    strip.replaceChildren();
    for (const [emoji, n] of entries) {
      const pill = document.createElement("span");
      pill.className = "paragraph-reactions__pill";
      if (emoji === mineEmoji) pill.classList.add("paragraph-reactions__pill--mine");
      pill.textContent = `${emoji} ${n}`;
      strip.append(pill);
    }
  }

  // The visitor's own current pick per paragraph, kept in memory from the initial bulk
  // fetch and updated after every toggle - the picker (renderReactionPicker below) reads
  // this synchronously to highlight the active emoji instead of firing a request every
  // time the menu opens.
  const mineByIndex = new Map();

  // One bulk fetch for the whole chapter on load, not one per paragraph - a chapter page
  // can have dozens, and this is the same "counts under an already-revealed paragraph
  // shouldn't need a request of its own" reasoning as the endpoint itself (see
  // app/db/reactions.py's count_reactions()).
  async function loadInitialReactions() {
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/reactions?branch_id=${encodeURIComponent(branchId)}`
      );
      if (!response.ok) return;
      const data = await response.json();
      for (const [indexStr, emoji] of Object.entries(data.mine || {})) {
        mineByIndex.set(Number(indexStr), emoji);
      }
      for (const [indexStr, counts] of Object.entries(data.counts || {})) {
        const index = Number(indexStr);
        renderStrip(index, counts, mineByIndex.get(index) ?? null);
      }
    } catch {
      // No network, or the server errored - the chapter itself still reads fine without
      // reaction counts, so this fails silently rather than surfacing an error banner
      // over content that has nothing to do with reactions.
    }
  }
  loadInitialReactions();

  async function pickReaction(index, emoji) {
    const body = new URLSearchParams({
      paragraph_index: String(index),
      emoji,
      branch_id: branchId,
    });
    let data;
    try {
      const response = await fetch(`/titles/${slugUrl}/chapters/${volume}/${number}/reactions`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });
      if (!response.ok) return;
      data = await response.json();
    } catch {
      return;
    }
    if (data.mine) {
      mineByIndex.set(index, data.mine);
    } else {
      mineByIndex.delete(index);
    }
    renderStrip(index, data.counts, data.mine);
    close();
  }

  const panel = document.createElement("div");
  panel.className = "paragraph-menu__panel";
  panel.setAttribute("role", "menu");
  document.body.appendChild(panel);

  let lastX = 0;
  let lastY = 0;

  function isOpen() {
    return panel.classList.contains("paragraph-menu__panel--open");
  }

  function addStubItem(label) {
    // PR 133 replaces this with the real feature; until then it's a visible but inert
    // placeholder rather than either doing nothing silently or not existing.
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
  }

  function addLoginItem(label) {
    // Same principle as _locked_feature.html elsewhere in the app: an anonymous visitor
    // still sees the menu, but its actions route to /login instead of running (there is
    // nothing to react/comment as without an account).
    const item = document.createElement("a");
    item.className = "paragraph-menu__item";
    item.setAttribute("role", "menuitem");
    item.href = "/login";
    item.textContent = label;
    panel.append(item);
  }

  function renderReactionPicker(index) {
    panel.replaceChildren();

    const back = document.createElement("button");
    back.type = "button";
    back.className = "paragraph-menu__back";
    back.textContent = "← Назад";
    back.addEventListener("click", () => {
      renderMenuItems(index);
      position(lastX, lastY);
    });
    panel.append(back);

    const picker = document.createElement("div");
    picker.className = "paragraph-menu__emoji-picker";
    picker.setAttribute("role", "menu");
    const mineEmoji = mineByIndex.get(index) ?? null;
    for (const [emoji, label] of EMOJI) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "paragraph-menu__emoji";
      if (emoji === mineEmoji) button.classList.add("paragraph-menu__emoji--active");
      button.setAttribute("role", "menuitemradio");
      button.setAttribute("aria-checked", emoji === mineEmoji ? "true" : "false");
      button.setAttribute("aria-label", label);
      button.title = label;
      button.textContent = emoji;
      button.addEventListener("click", () => pickReaction(index, emoji));
      picker.append(button);
    }
    panel.append(picker);
  }

  function renderMenuItems(index) {
    panel.replaceChildren();
    if (!isAuthenticated) {
      addLoginItem("Реакции");
      addLoginItem("Комментировать");
      return;
    }

    const reactItem = document.createElement("button");
    reactItem.type = "button";
    reactItem.className = "paragraph-menu__item";
    reactItem.setAttribute("role", "menuitem");
    reactItem.textContent = "Реакции";
    reactItem.addEventListener("click", () => {
      renderReactionPicker(index);
      position(lastX, lastY);
    });
    panel.append(reactItem);

    addStubItem("Комментировать");
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
    lastX = x;
    lastY = y;
    // Stashed on the panel for PR 133 to read once it gives "Комментировать" real
    // behavior - this file never reads it back itself, the reaction picker above keys
    // off `index`/`mineByIndex` directly instead.
    panel.dataset.paragraphKey = paragraphKey(index);
    renderMenuItems(index);
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
