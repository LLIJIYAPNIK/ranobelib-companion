// What a setting produces, shown beside it as it changes (the settings redesign) - before
// the form is saved, so the visitor sees the result of a switch without leaving the page.
// Without this script every preview shows the saved state the server rendered.
//
// - «Профиль» (/settings/account): the profile's header follows the nickname and bio
//   fields, and the bio counter its field.
// - «Приватность» (/settings/account): [data-preview-for="<input name>"] parts of «Так вас
//   видят другие» go «скрыто» as their switch goes off.
// - «Колокольчик» (/settings/notifications): either switch hides the bell (both gate it
//   the same way, app/db/users.py), and the line under it says why.
// - «Изменение пароля» (/settings/security): the rules app/auth/passwords.py checks,
//   ticked as the new password is typed - nothing is marked before the first keystroke.
(() => {
  const privacy = document.querySelector('[data-role="privacy-form"]');
  const preview = document.querySelector('[data-role="privacy-preview"]');
  if (privacy && preview) {
    const sync = () => {
      for (const part of preview.querySelectorAll("[data-preview-for]")) {
        const input = privacy.querySelector(`input[name="${part.dataset.previewFor}"]`);
        if (!input) continue;
        part.classList.toggle("is-off", !input.checked);
        const state = part.querySelector("[data-preview-state]");
        if (state) state.textContent = input.checked ? "видно" : "скрыто";
      }
    };
    privacy.addEventListener("change", sync);
    sync();
  }

  // The profile's header as the fields will make it: the nickname (the email when it's
  // empty, as /profile shows it) and the bio, with the profile's own empty line.
  const bio = document.querySelector('[data-role="bio-input"]');
  const bioCount = document.querySelector('[data-role="bio-count"]');
  const bioPreview = document.querySelector('[data-role="preview-bio"]');
  if (bio) {
    bio.addEventListener("input", () => {
      if (bioCount) bioCount.textContent = String(bio.value.length);
      if (!bioPreview) return;
      const text = bio.value.trim();
      bioPreview.textContent = text || bioPreview.dataset.empty || "";
      bioPreview.classList.toggle("is-empty", !text);
    });
  }

  const nickname = document.querySelector('input[name="nickname"]');
  const email = document.querySelector('input[name="email"]');
  const names = document.querySelectorAll('[data-role="preview-name"]');
  if (nickname && names.length) {
    const sync = () => {
      const name = nickname.value.trim() || email?.value.trim() || "";
      for (const el of names) el.textContent = name;
    };
    nickname.addEventListener("input", sync);
    email?.addEventListener("input", sync);
  }

  const bellForm = document.querySelector('[data-role="bell-form"]');
  const bell = document.querySelector('[data-role="bell-preview"]');
  if (bellForm && bell) {
    const text = bell.querySelector('[data-role="bell-preview-text"]');
    const sync = () => {
      const enabled = bellForm.elements.notifications_enabled?.checked;
      const quiet = bellForm.elements.do_not_disturb?.checked;
      bell.classList.toggle("is-off", !enabled || quiet);
      if (!text) return;
      if (!enabled) text.textContent = "Колокольчика нет, уведомления не показываются";
      else if (quiet) text.textContent = "Колокольчик спрятан, пока включено «Не беспокоить»";
      else text.textContent = "Колокольчик на месте, на нём число новых";
    };
    bellForm.addEventListener("change", sync);
  }

  const passwordForm = document.querySelector('[data-role="password-form"]');
  const rules = document.querySelector('[data-role="password-rules"]');
  if (passwordForm && rules) {
    const next = passwordForm.elements.new_password;
    const again = passwordForm.elements.new_password_confirm;
    const rule = (name) => rules.querySelector(`[data-rule="${name}"]`);
    const emailLocal = rule("email")?.dataset.emailLocal || "";
    const mark = (name, met) => {
      const item = rule(name);
      if (!item) return;
      item.classList.toggle("is-met", met === true);
      item.classList.toggle("is-unmet", met === false);
    };
    const sync = () => {
      const value = next?.value || "";
      if (!value) {
        for (const name of ["length", "email", "match", "long"]) mark(name, null);
        return;
      }
      mark("length", value.length >= 8);
      mark("email", !emailLocal || value.toLowerCase() !== emailLocal);
      mark("long", value.length >= 12 ? true : null);
      mark("match", again?.value ? again.value === value : null);
    };
    passwordForm.addEventListener("input", sync);
  }
})();
