// PR 357: the report form - «Пожаловаться» on someone else's comment (paragraph-menu.js)
// or profile (profile-more.js). One form for both, rendered once in base.html for a
// logged-in visitor: reasons as radio buttons plus optional details, posted to
// POST /reports (PR 356, app/api/reports.py).
//
//   window.reportForm.open({ targetType: "comment" | "profile", targetId, opener })
//
// or a button with data-report-target-type/-id. On phones it opens in the shared bottom
// sheet (bottom-sheet.js) - swapping in place if the sheet already shows something, like
// the profile's «⋯» menu - and on desktop in the <dialog> next to it in base.html.
//
// Sending disables «Отправить» («Отправляем…»); a refusal stays in the form with the
// server's own words (not your own, already hidden, the daily limit...). Success closes
// it with a toast: «Жалоба отправлена», or «Вы уже отправили жалобу» when this reader's
// report on the object is still open (the server keeps one per object). A second
// «Пожаловаться» on an object already reported on this page skips the form and says so
// straight away. Nothing about the outcome of moderation comes back to the reader.
(() => {
  const root = document.querySelector('[data-role="report-form"]');
  const dialog = document.querySelector('[data-role="report-dialog"]');
  if (!root || !dialog) return;

  const form = root.querySelector('[data-role="report-form-form"]');
  const details = form.elements.details;
  const count = root.querySelector('[data-role="report-count"]');
  const error = root.querySelector('[data-role="report-error"]');
  const submit = root.querySelector('[data-role="report-submit"]');
  const dialogTitle = dialog.querySelector('[data-role="report-dialog-title"]');
  const dialogBody = dialog.querySelector('[data-role="report-dialog-body"]');
  const phone = window.matchMedia("(max-width: 767px)");
  const home = { parent: root.parentNode, next: root.nextSibling };

  const TITLES = { comment: "Пожаловаться на комментарий", profile: "Пожаловаться на профиль" };
  const SENT = "Жалоба отправлена";
  const ALREADY = "Вы уже отправили жалобу";
  const TOAST_MS = 4000;

  const reported = new Set(); // "comment:12" - sent from this page
  let current = null; // { key, opener, inSheet }
  let sending = false;
  let toast = null;
  let toastTimer = null;

  function showToast(message) {
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
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, TOAST_MS);
  }

  function updateCount() {
    count.textContent = `${details.value.length} / ${details.maxLength}`;
  }

  function setError(message) {
    error.textContent = message || "";
    error.hidden = !message;
  }

  function setSending(on) {
    sending = on;
    submit.disabled = on;
    submit.textContent = on ? "Отправляем…" : "Отправить";
    if (on) form.setAttribute("aria-busy", "true");
    else form.removeAttribute("aria-busy");
  }

  function reset(targetType, targetId) {
    form.reset();
    form.elements.target_type.value = targetType;
    form.elements.target_id.value = String(targetId);
    setError("");
    setSending(false);
    updateCount();
  }

  function putBack() {
    root.hidden = true;
    home.parent.insertBefore(root, home.next);
  }

  function open({ targetType, targetId, opener } = {}) {
    if (!TITLES[targetType] || !targetId) return;
    const key = `${targetType}:${targetId}`;
    if (reported.has(key)) {
      // Opened from a menu in the sheet (the profile's «⋯» on a phone): close that too.
      window.bottomSheet?.close();
      showToast(ALREADY);
      return;
    }
    reset(targetType, targetId);
    const title = TITLES[targetType];
    if (phone.matches && window.bottomSheet) {
      current = { key, opener, inSheet: true };
      window.bottomSheet.open({
        title,
        content: root,
        opener,
        onClose: () => {
          if (current?.inSheet) current = null;
        },
      });
      return;
    }
    current = { key, opener, inSheet: false };
    dialogTitle.textContent = title;
    dialogBody.append(root);
    root.hidden = false;
    dialog.showModal();
    root.querySelector("[data-autofocus]")?.focus();
  }

  function close() {
    if (!current) return;
    if (current.inSheet) {
      window.bottomSheet.close();
    } else {
      dialog.close();
    }
  }

  dialog.addEventListener("close", () => {
    if (!current || current.inSheet) return;
    const { opener } = current;
    current = null;
    putBack();
    if (opener?.isConnected) opener.focus();
  });

  // A click on the backdrop (the <dialog> itself, outside its content) closes it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && !sending) dialog.close();
  });
  dialog.addEventListener("cancel", (event) => {
    if (sending) event.preventDefault();
  });
  dialog
    .querySelector('[data-role="report-dialog-close"]')
    .addEventListener("click", () => dialog.close());
  root.querySelector('[data-role="report-cancel"]').addEventListener("click", close);
  details.addEventListener("input", updateCount);
  form.addEventListener("change", (event) => {
    if (event.target.name === "reason") setError("");
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (sending || !current) return;
    if (!form.querySelector('input[name="reason"]:checked')) {
      setError("Выберите причину жалобы");
      form.querySelector('input[name="reason"]').focus();
      return;
    }
    setError("");
    setSending(true);
    const { key } = current;
    let response = null;
    let data = null;
    try {
      response = await fetch("/reports", {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
      });
      data = await response.json().catch(() => null);
    } catch {
      setSending(false);
      setError("Нет соединения. Попробуйте ещё раз.");
      return;
    }
    setSending(false);
    if (!response.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : null;
      setError(detail || "Не удалось отправить жалобу. Попробуйте ещё раз.");
      return;
    }
    reported.add(key);
    close();
    showToast(data?.status === "already_sent" ? ALREADY : SENT);
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-report-target-type]");
    if (!button) return;
    event.preventDefault();
    open({
      targetType: button.dataset.reportTargetType,
      targetId: Number(button.dataset.reportTargetId),
      opener: button,
    });
  });

  window.reportForm = { open, close };
})();
