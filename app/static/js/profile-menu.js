// Profile dropdown (PR 93) - click the avatar (PR 88) to reveal Профиль/Читаю/Настройки/
// Выйти, closing on a second click, a click outside the menu, or Escape. Same open/close
// mechanics as the custom <select> dropdown (PR 54's custom-dropdown.js) - toggle a class,
// listen for an outside click - but without that one's listbox keyboard navigation, since
// this menu is just a handful of plain links (and one submit button), not selectable
// options.
(() => {
  const wrapper = document.querySelector('[data-role="profile-menu"]');
  if (!wrapper) return;

  const trigger = wrapper.querySelector('[data-role="profile-menu-trigger"]');

  function isOpen() {
    return wrapper.classList.contains("profile-menu--open");
  }

  function open() {
    wrapper.classList.add("profile-menu--open");
    trigger.setAttribute("aria-expanded", "true");
  }

  function close(refocusTrigger = false) {
    wrapper.classList.remove("profile-menu--open");
    trigger.setAttribute("aria-expanded", "false");
    if (refocusTrigger) trigger.focus();
  }

  trigger.addEventListener("click", () => (isOpen() ? close() : open()));

  document.addEventListener("click", (event) => {
    if (isOpen() && !wrapper.contains(event.target)) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close(true);
  });
})();
