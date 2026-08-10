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
  const DEFAULTS = { fontFamily: "sans", fontSize: "15", lineHeight: "1.85", width: "640" };

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
    control.value = settings[key];
    updateReadout(control);
    control.addEventListener("input", () => {
      settings = { ...settings, [key]: control.value };
      apply(settings);
      updateReadout(control);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    });
  }
})();
