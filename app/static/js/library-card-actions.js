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
// The removal itself: POST /library/{slug}/remove (JSON), then every element of that title
// on the page goes (its card, and the hero if it's that title) and the page recounts
// (library-toolbar.js listens for "library:changed").
(() => {
  const root = document.querySelector('[data-role="library-titles"]');
  if (!root) return;

  const popovers = new Map(); // ⋯ button -> { menu, confirm, slug }
  let open = null; // { button, panel }

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
    const { button, panel } = open;
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    open = null;
    if (focus && button.isConnected) button.focus();
  }

  function show(button, panel, focusSelector) {
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
        `[data-role="library-hero"][data-slug-url="${escaped}"]`
    )];
  }

  async function remove(slug) {
    const response = await fetch(`/library/${encodeURIComponent(slug)}/remove`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    return response.ok;
  }

  function dropTitle(slug) {
    for (const [button, parts] of popovers) {
      if (parts.slug !== slug) continue;
      parts.menu.remove();
      parts.confirm.remove();
      popovers.delete(button);
    }
    for (const element of elementsFor(slug)) element.remove();
    document.dispatchEvent(new CustomEvent("library:changed"));
  }

  document.addEventListener("click", async (event) => {
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
    const confirmButton = event.target.closest('[data-role="library-confirm-remove"]');
    if (!confirmButton) return;
    confirmButton.disabled = true;
    let ok = false;
    try {
      ok = await remove(parts.slug);
    } catch {
      ok = false;
    }
    confirmButton.disabled = false;
    if (!ok) return;
    close({ focus: false });
    dropTitle(parts.slug);
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

  const reposition = () => open && place(open.button, open.panel);
  window.addEventListener("scroll", reposition, { passive: true });
  window.addEventListener("resize", reposition);
})();
