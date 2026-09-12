// PR 218: opens the login/register card as a modal over the current page instead of
// navigating to /login or /register - same fixed-overlay/backdrop-click/Escape/close-
// button mechanics as title-quickview.js (PR 117), the app's existing centered-modal
// pattern (distinct from the anchored flyout panels notifications-panel.js/profile-menu.js
// use - reusing this one rather than inventing a third).
//
// Every fetch() below sends X-Requested-With: XMLHttpRequest, the signal app/api/auth.py's
// _is_modal_request() branches on to return the bare _auth_card.html fragment instead of a
// full page (see its own docstring) - GET /login and GET /register for the initial
// open/tab-switch, POST for the form submit itself.
(() => {
  const AJAX_HEADERS = { "X-Requested-With": "XMLHttpRequest" };

  const overlay = document.createElement("div");
  overlay.className = "auth-modal";
  overlay.innerHTML =
    '<div class="auth-modal__panel" role="dialog" aria-modal="true" aria-label="Вход или регистрация">' +
    '<button type="button" class="auth-modal__close" aria-label="Закрыть">&times;</button>' +
    '<div class="auth-modal__body" data-role="auth-modal-body"></div>' +
    "</div>";
  document.body.appendChild(overlay);

  const body = overlay.querySelector('[data-role="auth-modal-body"]');
  const closeBtn = overlay.querySelector(".auth-modal__close");

  function isOpen() {
    return overlay.classList.contains("auth-modal--open");
  }

  function close() {
    overlay.classList.remove("auth-modal--open");
  }

  // Same "can't apply autofocus's mobile-keyboard suppression via a <script src> inside
  // injected HTML" problem disable-mobile-autofocus.js's own comment describes for a
  // normal page load - a <script> tag set via innerHTML never executes. login.html/
  // register.html's own full-page render still includes and runs that script normally
  // (untouched); this is the same fix, re-applied by hand for whatever body.innerHTML
  // just replaced, since the browser won't run the fragment's copy of it either way.
  function suppressMobileAutofocus() {
    if (!window.matchMedia("(max-width: 640px)").matches) return;
    const autofocused = body.querySelector("[autofocus]");
    if (autofocused) {
      autofocused.addEventListener("focus", () => autofocused.blur(), { once: true });
    }
  }

  // Re-run after every body.innerHTML swap - the previous listeners are gone with the old
  // DOM nodes they were attached to.
  function wire() {
    body.querySelectorAll('a[href="/login"], a[href="/register"]').forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        load(link.getAttribute("href"));
      });
    });
    const form = body.querySelector('[data-role="auth-form"]');
    if (form) form.addEventListener("submit", onSubmit);
    suppressMobileAutofocus();
  }

  async function load(url) {
    body.innerHTML = '<div class="spinner" aria-hidden="true"></div>';
    try {
      const response = await fetch(url, { headers: AJAX_HEADERS });
      body.innerHTML = await response.text();
    } catch {
      body.innerHTML = '<p class="form-error">Не удалось загрузить форму</p>';
    }
    wire();
  }

  async function onSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const isRegister = form.getAttribute("action") === "/register";
    let response;
    try {
      response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: AJAX_HEADERS,
      });
    } catch {
      return; // Left as-is - the visitor can just retry the submit.
    }
    if (response.redirected) {
      // Register's success redirect (see app/api/auth.py's _is_modal_request() branch in
      // register()) moves to a genuinely new onboarding step (the avatar prompt) - that's
      // a real navigation. Login's own success redirect always targets "/" regardless of
      // where this modal was opened from; following it would yank the visitor away from
      // whatever page they were actually on, exactly what this PR set out to stop - a
      // plain reload of the current page takes its place instead.
      if (isRegister) {
        window.location.href = response.url;
      } else {
        window.location.reload();
      }
      return;
    }
    body.innerHTML = await response.text();
    wire();
  }

  function open(url) {
    overlay.classList.add("auth-modal--open");
    load(url);
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest('[data-role="auth-modal-trigger"]');
    if (trigger) {
      event.preventDefault();
      open(trigger.getAttribute("href"));
      return;
    }
    if (isOpen() && event.target === overlay) close();
  });

  closeBtn.addEventListener("click", close);

  document.addEventListener("keydown", (event) => {
    if (isOpen() && event.key === "Escape") close();
  });
})();
