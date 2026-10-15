// PR 357: «⋯» on someone else's profile (profile.html) - for now a single item,
// «Пожаловаться», which opens the report form (report-form.js, base.html).
//
// Desktop: a popover under the button, moved to <body> on start-up - the hero has
// overflow: hidden and would clip it. Escape or a click elsewhere closes it and hands
// focus back to «⋯». Phones: the shared bottom sheet (bottom-sheet.js), headed with the
// person's name; «Пожаловаться» swaps the form into the same sheet. Either way the form
// returns focus to «⋯» when it closes.
(() => {
  const trigger = document.querySelector('[data-role="profile-more"]');
  const menu = document.querySelector('[data-role="profile-more-menu"]');
  if (!trigger || !menu) return;

  const report = menu.querySelector('[data-role="profile-report"]');
  const phone = window.matchMedia("(max-width: 767px)");
  let open = null; // "popover" | "sheet"

  document.body.append(menu);
  trigger.hidden = false;

  function place() {
    const rect = trigger.getBoundingClientRect();
    menu.style.top = `${Math.round(rect.bottom + 8)}px`;
    menu.style.right = `${Math.max(12, Math.round(window.innerWidth - rect.right))}px`;
  }

  function close({ focus = true } = {}) {
    if (open === "popover") {
      menu.hidden = true;
      if (focus) trigger.focus();
    } else if (open === "sheet") {
      window.bottomSheet.close();
    }
    trigger.setAttribute("aria-expanded", "false");
    open = null;
  }

  function show() {
    trigger.setAttribute("aria-expanded", "true");
    if (phone.matches && window.bottomSheet) {
      open = "sheet";
      window.bottomSheet.open({
        title: trigger.dataset.profileName,
        content: menu,
        opener: trigger,
        onClose: () => {
          if (open !== "sheet") return;
          trigger.setAttribute("aria-expanded", "false");
          open = null;
        },
      });
      return;
    }
    open = "popover";
    menu.hidden = false;
    place();
    report.focus();
  }

  trigger.addEventListener("click", () => {
    if (open) close();
    else show();
  });

  report.addEventListener("click", () => {
    const inSheet = open === "sheet";
    // In the sheet the form takes the menu's place itself; a popover just goes away.
    if (inSheet) {
      open = null;
      trigger.setAttribute("aria-expanded", "false");
    } else {
      close({ focus: false });
    }
    window.reportForm?.open({
      targetType: "profile",
      targetId: Number(menu.dataset.userId),
      opener: trigger,
    });
  });

  document.addEventListener("click", (event) => {
    if (open !== "popover") return;
    if (menu.contains(event.target) || trigger.contains(event.target)) return;
    close({ focus: false });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open === "popover") close();
  });

  window.addEventListener("resize", () => {
    if (open === "popover") close({ focus: false });
  });
})();
