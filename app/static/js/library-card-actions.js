// PR 298 (LibraryCard.dc.html): the ⋯ menu on library cards and the «Продолжить чтение»
// hero - «Страница тайтла», «Оглавление», «Удалить из библиотеки» - and the confirmation
// that removal always goes through («EPUB-файлы … останутся на устройстве»).
//
// The menu and the confirmation are popovers placed from their ⋯ button (position: fixed
// in app.css). They're moved to <body> on start-up: a card lifts on hover with a
// transform, which would otherwise become their containing block, and the hero card's
// overflow: hidden would clip them. One open at a time; Escape or a click elsewhere
// closes it and hands focus back to the button; ↑/↓ move between the menu items.
//
// The removal itself, undoable like the download history's (PR 281): every element of
// that title leaves the page at once (its card, and the hero if it's that title), the page
// recounts (library-toolbar.js listens for "library:changed"), and a toast offers
// «Вернуть» for 6 seconds - only then is POST /library/{slug}/remove sent. «Вернуть» puts
// everything back where it was without anything reaching the server. A removal still
// waiting is sent right away when another one starts, and with keepalive when the page is
// left; if it fails, the title comes back.
//
// PR 300 (LibraryMobile.dc.html): on phones the same menu and confirmation open in the
// shared bottom sheet (bottom-sheet.js) instead of as popovers - the title's name heads
// the action sheet, «Удалить из библиотеки» swaps it for the confirmation (headed
// «Удалить из библиотеки?», a pink top edge in app.css) in the same sheet.
(() => {
  const root = document.querySelector(
    '[data-role="library-titles"], [data-role="continue-reading-root"]'
  );
  if (!root) return;

  const UNDO_MS = 6000;
  const phone = window.matchMedia("(max-width: 767px)");
  const popovers = new Map(); // ⋯ button -> { menu, confirm, slug }
  let open = null; // { button, panel }
  let pending = null; // { slug, button, placed: [{ node, parent, next }], timer }
  let toast = null;

  for (const button of root.querySelectorAll('[data-role="library-more"]')) {
    const scope = button.parentElement;
    const owner = button.closest('[data-role="library-item"], [data-role="library-hero"]');
    const menu = scope.querySelector('[data-role="library-menu"]');
    const confirm = scope.querySelector('[data-role="library-confirm"]');
    if (!owner || !menu || !confirm) continue;
    document.body.append(menu, confirm);
    popovers.set(button, { menu, confirm, slug: owner.dataset.slugUrl });
    button.hidden = false;
  }

  function place(button, panel) {
    const rect = button.getBoundingClientRect();
    panel.style.top = `${Math.round(rect.bottom + 8)}px`;
    panel.style.right = `${Math.max(12, Math.round(window.innerWidth - rect.right))}px`;
  }

  function close({ focus = true } = {}) {
    if (!open) return;
    if (open.sheet) {
      // The sheet hands focus back to the ⋯ button itself once it's closed.
      open.button.setAttribute("aria-expanded", "false");
      open = null;
      window.bottomSheet.close();
      return;
    }
    const { button, panel } = open;
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    open = null;
    if (focus && button.isConnected) button.focus();
  }

  function showInSheet(button, panel) {
    const confirming = panel.dataset.role === "library-confirm";
    open = { button, panel, sheet: true };
    button.setAttribute("aria-expanded", "true");
    // Already open with the menu: the sheet swaps its content in place.
    window.bottomSheet.open({
      title: confirming ? "Удалить из библиотеки?" : button.dataset.titleName,
      content: panel,
      opener: button,
      onClose: () => {
        if (!open || open.panel !== panel) return;
        button.setAttribute("aria-expanded", "false");
        open = null;
      },
    });
  }

  function show(button, panel, focusSelector) {
    if (phone.matches && window.bottomSheet) {
      showInSheet(button, panel);
      return;
    }
    close({ focus: false });
    panel.hidden = false;
    place(button, panel);
    button.setAttribute("aria-expanded", "true");
    open = { button, panel };
    panel.querySelector(focusSelector)?.focus();
  }

  function buttonOf(panel) {
    for (const [button, parts] of popovers) {
      if (parts.menu === panel || parts.confirm === panel) return button;
    }
    return null;
  }

  function elementsFor(slug) {
    const escaped = CSS.escape(slug);
    return [...document.querySelectorAll(
      `[data-role="library-item"][data-slug-url="${escaped}"], ` +
        `[data-role="library-hero"][data-slug-url="${escaped}"], ` +
        `[data-role="home-reading-hero"][data-slug-url="${escaped}"]`
    )];
  }

  function showToast(message, action) {
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "wn-toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      document.body.append(toast);
    }
    const text = document.createElement("span");
    text.className = "wn-toast__text";
    text.textContent = message;
    toast.replaceChildren(text);
    if (action) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "wn-toast__action";
      button.dataset.role = "library-undo";
      button.textContent = action.label;
      button.addEventListener("click", action.run);
      toast.append(button);
    }
    toast.hidden = false;
    clearTimeout(toast.hideTimer);
    toast.hideTimer = setTimeout(hideToast, action ? UNDO_MS : 2600);
  }

  function hideToast() {
    if (toast) toast.hidden = true;
  }

  function putBack(p) {
    // In reverse, so a node whose `next` was another removed node finds it back in place.
    for (const { node, parent, next } of [...p.placed].reverse()) {
      parent.insertBefore(node, next && next.parentNode === parent ? next : null);
    }
    document.dispatchEvent(new CustomEvent("library:changed"));
  }

  async function send(p, { keepalive = false } = {}) {
    try {
      const response = await fetch(`/library/${encodeURIComponent(p.slug)}/remove`, {
        method: "POST",
        headers: { Accept: "application/json" },
        keepalive,
      });
      if (response.ok) {
        // The last title gone for good - the empty library has its own hint.
        if (!keepalive && !root.querySelector('[data-role="library-item"]')) window.location.reload();
        return;
      }
    } catch {
      // Falls through to putting the title back.
    }
    if (keepalive) return;
    putBack(p);
    showToast("Не удалось удалить из библиотеки");
  }

  function commit() {
    if (!pending) return;
    const p = pending;
    pending = null;
    clearTimeout(p.timer);
    send(p);
  }

  function undo() {
    if (!pending) return;
    const p = pending;
    pending = null;
    clearTimeout(p.timer);
    putBack(p);
    hideToast();
    if (p.button.isConnected) p.button.focus();
  }

  function removeTitle(slug, button, viaKeyboard) {
    commit();
    const placed = elementsFor(slug).map((node) => ({ node, parent: node.parentNode, next: node.nextSibling }));
    for (const { node } of placed) node.remove();
    pending = { slug, button, placed, timer: setTimeout(commit, UNDO_MS) };
    document.dispatchEvent(new CustomEvent("library:changed"));
    showToast("Удалено из библиотеки", { label: "Вернуть", run: undo });
    // The focused card is gone - from the keyboard, hand focus to «Вернуть».
    if (viaKeyboard) toast.querySelector('[data-role="library-undo"]').focus();
  }

  document.addEventListener("click", (event) => {
    const more = event.target.closest('[data-role="library-more"]');
    if (more && popovers.has(more)) {
      if (open && open.button === more) close();
      else show(more, popovers.get(more).menu, '[role="menuitem"]');
      return;
    }
    const panel = event.target.closest('[data-role="library-menu"], [data-role="library-confirm"]');
    if (!panel) {
      if (open) close({ focus: false });
      return;
    }
    const button = buttonOf(panel);
    if (!button) return;
    const parts = popovers.get(button);
    if (event.target.closest('[data-role="library-remove"]')) {
      show(button, parts.confirm, '[data-role="library-confirm-cancel"]');
      return;
    }
    if (event.target.closest('[data-role="library-confirm-cancel"]')) {
      close();
      return;
    }
    if (!event.target.closest('[data-role="library-confirm-remove"]')) return;
    close({ focus: false });
    removeTitle(parts.slug, button, event.detail === 0);
  });

  document.addEventListener("keydown", (event) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (open.panel.getAttribute("role") !== "menu") return;
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = [...open.panel.querySelectorAll('[role="menuitem"]')];
    const index = items.indexOf(document.activeElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    event.preventDefault();
    items[(index + step + items.length) % items.length]?.focus();
  });

  window.addEventListener("pagehide", () => {
    if (!pending) return;
    const p = pending;
    pending = null;
    clearTimeout(p.timer);
    send(p, { keepalive: true });
  });

  const reposition = () => open && !open.sheet && place(open.button, open.panel);
  window.addEventListener("scroll", reposition, { passive: true });
  window.addEventListener("resize", reposition);
})();
