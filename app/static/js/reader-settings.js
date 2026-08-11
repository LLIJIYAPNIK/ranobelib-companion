// Reader typography/width settings (PR 30): persisted to localStorage and applied as CSS
// custom properties on :root, so every chapter page picks up the visitor's saved
// preference on load without a server round trip. Independent of login state - per-user
// storage in the app DB was called out as optional in the roadmap and isn't done here.
(() => {
  const STORAGE_KEY = "readerSettings";
  const FONT_FAMILIES = {
    sans: "var(--font-sans)",
    serif: "var(--font-serif)",
    mono: "var(--font-mono)",
  };
  const DEFAULTS = {
    fontFamily: "sans",
    fontSize: "15",
    lineHeight: "1.85",
    width: "640",
    // PR 63/64: tapToRead and paragraphStyle are read directly from this same
    // localStorage key by app/static/js/tap-to-read.js on the chapter page - neither is
    // a CSS custom property like the rest of these, so apply() below has nothing to do
    // for either.
    tapToRead: false,
    paragraphStyle: "chat",
  };

  const root = document.documentElement;

  function load() {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") };
    } catch {
      return { ...DEFAULTS };
    }
  }

  function apply(settings) {
    root.style.setProperty(
      "--reader-font-family",
      FONT_FAMILIES[settings.fontFamily] || FONT_FAMILIES.sans
    );
    root.style.setProperty("--reader-font-size", `${settings.fontSize}px`);
    root.style.setProperty("--reader-line-height", settings.lineHeight);
    root.style.setProperty("--reader-width", `${settings.width}px`);
  }

  let settings = load();
  apply(settings);

  const panel = document.querySelector('[data-role="reader-settings"]');
  if (!panel) return;

  function updateReadout(control) {
    if (control.type !== "range") return;
    const output = panel.querySelector(`output[for="${control.id}"]`);
    if (output) output.textContent = control.value;
  }

  for (const control of panel.querySelectorAll("[data-setting]")) {
    const key = control.dataset.setting;
    const isCheckbox = control.type === "checkbox";
    // A checkbox's own .value is always the fixed string "on" - its checked state is
    // what actually holds the setting (a boolean, not a string like every other field
    // here), so it needs separate read/write handling rather than reusing .value.
    if (isCheckbox) {
      control.checked = Boolean(settings[key]);
    } else {
      control.value = settings[key];
      updateReadout(control);
    }
    control.addEventListener("input", () => {
      settings = { ...settings, [key]: isCheckbox ? control.checked : control.value };
      apply(settings);
      if (!isCheckbox) updateReadout(control);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    });
  }
})();
