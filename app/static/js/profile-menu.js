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
// ("02 Components" -> Account hub popover); on mobile (<= 767px, the Quiet Edge Bar
// layout) it's a bottom sheet over a scrim with the page scroll locked ("05
// Спецификация" -> Sheets, popovers, dialogs) - CSS does the sheet layout from the
// --sheet class, so no inline coordinates are set in that case.
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
  let scrim = null;

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

  function openScrim() {
    scrim = document.createElement("div");
    scrim.className = "shell-scrim";
    scrim.addEventListener("click", () => close(true));
    document.body.appendChild(scrim);
    document.documentElement.classList.add("shell-scroll-lock");
  }

  function closeScrim() {
    if (!scrim) return;
    scrim.remove();
    scrim = null;
    document.documentElement.classList.remove("shell-scroll-lock");
  }

  function open() {
    const asSheet = mobileQuery.matches;
    if (asSheet) openScrim();
    document.body.appendChild(panel);
    panel.style.position = "fixed";
    panel.classList.toggle("profile-menu__panel--sheet", asSheet);
    wrapper.classList.add("profile-menu--open");
    // The open state lives on the panel itself - once portaled it's a sibling of
    // .profile-menu, so a descendant selector would never match.
    panel.classList.add("profile-menu__panel--open");
    if (!asSheet) positionPopover();
    trigger.setAttribute("aria-expanded", "true");
    if (asSheet) panel.querySelector("a, button")?.focus();
    window.addEventListener("resize", closeOnLayoutChange);
    if (!asSheet) window.addEventListener("scroll", closeOnLayoutChange, true);
  }

  function close(refocusTrigger = false) {
    wrapper.classList.remove("profile-menu--open");
    panel.classList.remove("profile-menu__panel--open", "profile-menu__panel--sheet");
    trigger.setAttribute("aria-expanded", "false");
    window.removeEventListener("resize", closeOnLayoutChange);
    window.removeEventListener("scroll", closeOnLayoutChange, true);
    closeScrim();
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

  document.addEventListener("click", (event) => {
    if (isOpen() && !wrapper.contains(event.target) && !panel.contains(event.target)) {
      close();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close(true);
  });
})();
