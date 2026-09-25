// PR 227: show/hide toggle for every password field (_password_field.html's macro) - one
// shared script instead of one per form, since the behavior is identical on all of them.
//
// Delegated from document rather than bound to each button up front, and loaded once from
// base.html rather than from _auth_card.html itself: auth-modal.js swaps the login/register
// card in via innerHTML, and a <script> inside HTML set that way never executes (see
// auth-modal.js's own suppressMobileAutofocus() comment) - a single document-level
// listener covers both the full-page render and every modal swap without re-wiring.
(() => {
  document.addEventListener("click", (event) => {
    const button = event.target.closest('[data-role="toggle-password"]');
    if (!button) return;
    const input = button.parentElement.querySelector("input");
    if (!input) return;

    // Read before changing `type`, which can reset the selection.
    const { selectionStart, selectionEnd } = input;
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.setAttribute("aria-label", reveal ? "Скрыть пароль" : "Показать пароль");
    button.setAttribute("aria-pressed", String(reveal));
    // toggleAttribute, not `.hidden = ...` - the icons are <svg>, and `hidden` is only a
    // reflected property on HTMLElement, so assigning it on an SVGElement is a silent no-op.
    button.querySelector('[data-icon="show"]').toggleAttribute("hidden", reveal);
    button.querySelector('[data-icon="hide"]').toggleAttribute("hidden", !reveal);

    // Back to the field, not left on the button, so the caret isn't lost mid-typing.
    input.focus();
    if (selectionStart !== null) input.setSelectionRange(selectionStart, selectionEnd);
  });
})();
