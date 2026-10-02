// Account hub (PR 93 profile dropdown, reshaped by PR 249 / Aurora Ink): click the avatar
// to reveal Профиль · Активность · Друзья · Настройки · Выйти, closing on a second
// click, a click outside, or Escape (focus goes back to the avatar).
//
// PR 97: the panel is portaled out to <body> while open, `position: fixed` - .sidebar
// scrolls (overflow-y: auto), and an absolutely positioned descendant would be clipped
// to it no matter its z-index. It's moved back to its original spot on close so the
// markup (and the no-JS fallback) still see it nested where it started.
//
// PR 249: on desktop the hub opens beside the rail, bottom-aligned with the avatar
// ("02 Components" -> Account hub popover). On mobile (<= 767px) it's the «Меню» bottom
// sheet - since PR 279 the shared one (bottom-sheet.js), which takes the panel in and
// hands it back on close and owns the scrim, scroll lock, drag, Escape and focus there.
(() => {
  const GAP = 8;
  const mobileQuery = window.matchMedia("(max-width: 767px)");

  const wrapper = document.querySelector('[data-role="profile-menu"]');
  if (!wrapper) return;

  const trigger = wrapper.querySelector('[data-role="profile-menu-trigger"]');
  const panel = wrapper.querySelector('[data-role="profile-menu-panel"]');
  const sidebar = document.querySelector('[data-role="sidebar"]');
  const homeParent = wrapper;
  const homeNextSibling = panel.nextSibling;
  let inSheet = false;

  function isOpen() {
    return wrapper.classList.contains("profile-menu--open");
  }

  function positionPopover() {
    const triggerRect = trigger.getBoundingClientRect();
    const sidebarRect = sidebar ? sidebar.getBoundingClientRect() : triggerRect;
    panel.style.left = `${sidebarRect.right + GAP}px`;
    const bottom = window.innerHeight - triggerRect.bottom;
    const maxBottom = window.innerHeight - panel.offsetHeight - GAP;
    panel.style.bottom = `${Math.max(GAP, Math.min(bottom, maxBottom))}px`;
    panel.style.top = "auto";
  }

  function markOpen(open) {
    wrapper.classList.toggle("profile-menu--open", open);
    // The open state lives on the panel itself - once portaled it's a sibling of
    // .profile-menu, so a descendant selector would never match.
    panel.classList.toggle("profile-menu__panel--open", open);
    trigger.setAttribute("aria-expanded", String(open));
  }

  function openSheet() {
    inSheet = true;
    panel.classList.add("profile-menu__panel--in-sheet");
    panel.querySelector(".profile-menu__head")?.setAttribute("data-autofocus", "");
    markOpen(true);
    window.bottomSheet.open({
      title: "Меню",
      content: panel,
      opener: trigger,
      onClose: () => {
        inSheet = false;
        panel.classList.remove("profile-menu__panel--in-sheet");
        markOpen(false);
      },
    });
  }

  function open() {
    if (mobileQuery.matches && window.bottomSheet) {
      openSheet();
      return;
    }
    document.body.appendChild(panel);
    panel.style.position = "fixed";
    markOpen(true);
    positionPopover();
    window.addEventListener("resize", closeOnLayoutChange);
    window.addEventListener("scroll", closeOnLayoutChange, true);
  }

  function close(refocusTrigger = false) {
    if (inSheet) {
      window.bottomSheet.close();
      return;
    }
    markOpen(false);
    window.removeEventListener("resize", closeOnLayoutChange);
    window.removeEventListener("scroll", closeOnLayoutChange, true);
    homeParent.insertBefore(panel, homeNextSibling);
    panel.style.position = "";
    panel.style.top = "";
    panel.style.bottom = "";
    panel.style.left = "";
    if (refocusTrigger) trigger.focus();
  }

  function closeOnLayoutChange() {
    close();
  }

  trigger.addEventListener("click", () => (isOpen() ? close() : open()));

  // The sheet closes itself (backdrop, «Закрыть», drag, Escape) - a click on its head
  // must not count as a click outside.
  document.addEventListener("click", (event) => {
    if (isOpen() && !inSheet && !wrapper.contains(event.target) && !panel.contains(event.target)) {
      close();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen() && !inSheet) close(true);
  });
})();
