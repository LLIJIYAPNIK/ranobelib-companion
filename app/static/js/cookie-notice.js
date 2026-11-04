// The cookie choice (PR 375): «Принять все» / «Только необходимые», from the notice
// (_cookie_notice.html) or «Настройки → Данные и cookie». Both are plain forms posting
// to /cookies/consent; this sends them with fetch instead, so the page stays where it is:
// the notice slides away, the settings page updates its «Сейчас» line. If the request
// fails (offline) the form is submitted the ordinary way - the choice is never lost
// silently.
//
// The server decides whether the notice is shown (the cookie_consent cookie), so nothing
// is hidden here on load. The localStorage flag of the old notice (PR 185) is dropped.
(() => {
  try {
    localStorage.removeItem("cookieNoticeDismissed");
  } catch {
    // storage blocked - nothing to clean
  }

  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STATES = {
    all: "Приняты все cookie, включая «Недавние».",
    essential: "Только необходимые: «Недавние» не сохраняются.",
  };

  function closeNotice(notice) {
    if (!notice) return;
    if (reduced()) {
      notice.remove();
      return;
    }
    notice.classList.add("is-leaving");
    notice.addEventListener("animationend", () => notice.remove(), { once: true });
    window.setTimeout(() => notice.remove(), 400);
  }

  // «Что и зачем хранится» opens the section itself on a phone, not the list of settings
  // areas - the flag settings-mobile-nav.js reads after a tap on its own nav.
  const more = document.querySelector('[data-role="cookie-notice-more"]');
  if (more) {
    more.addEventListener("click", () => {
      try {
        sessionStorage.setItem("settingsMobileDetail", "1");
      } catch {
        // storage blocked - the phone shows the list, one tap away
      }
    });
  }

  for (const form of document.querySelectorAll('[data-role="cookie-consent-form"]')) {
    form.addEventListener("submit", (event) => {
      const button = event.submitter;
      if (!button || !button.value || form.dataset.plain) return;
      event.preventDefault();
      const body = new FormData(form);
      body.set("choice", button.value);
      form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      fetch(form.action, { method: "POST", body, headers: { "X-Requested-With": "fetch" } })
        .then((response) => {
          if (response.status !== 204) throw new Error(String(response.status));
          closeNotice(form.closest('[data-role="cookie-notice"]'));
          const state = document.querySelector('[data-role="cookie-consent-state"]');
          if (state) state.textContent = STATES[button.value];
          document.querySelectorAll('[data-role="cookie-consent-form"] button').forEach((b) => {
            b.disabled = false;
            b.setAttribute("aria-pressed", b.value === button.value ? "true" : "false");
          });
        })
        .catch(() => {
          form.querySelectorAll("button").forEach((b) => { b.disabled = false; });
          form.dataset.plain = "true";
          form.requestSubmit(button);
        });
    });
  }
})();
