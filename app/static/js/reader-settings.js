// Reader settings (PR 30 onwards): persisted to localStorage under "readerSettings" and
// applied as CSS custom properties / attributes on :root, so every chapter page picks up
// the visitor's preference on load without a server round trip. Independent of login.
//
// Every control with data-setting="<key>" is bound here, wherever it sits - the
// /settings/reading page and the reader's Aa panel (PR 255) share the one stored object,
// so they can't drift apart. Checkboxes store booleans, radios (segmented controls,
// theme swatches) their value, range/select/number inputs their value;
// [data-setting-step="<key>"] buttons nudge a number (the font size stepper) and
// [data-role="reader-settings-reset"] restores the defaults. Other tabs follow along
// through the storage event.
//
// Keys read by other scripts, not by CSS: tapToRead / readerMode, paragraphStyle,
// paragraphAnimation, revealTempo (tap-to-read.js), showParagraphSocial
// (paragraph-menu.js), readingSpeedWpm (reading-speed-test.js), autoDownloadNext and
// autoDownloadAnyNetwork (offline-autodownload.js, PR 334), keepScreenOn
// (reader-wake-lock.js, PR 338), openDownloadedFromCopy (offline-store.js hands it to the
// service worker, PR 339).
//
// PR 255 adds four keys:
// - theme: Aurora Dark (default), AMOLED, Sepia, Light, System - data-reader-theme on
//   :root swaps the --r-* tokens (app.css). reader-theme-early.js sets it before first
//   paint on the chapter page.
// - margins: side padding of the mobile reading column (compact / normal / wide).
// - readerMode: "scroll" | "tap", the same switch as tapToRead - both are kept in sync
//   so tap-to-read.js keeps reading tapToRead.
// - revealPreset: "instant" | "smooth" | "speed" - writes paragraphAnimation and
//   revealTempo underneath; picking those directly in «Дополнительно» shows no preset.
(() => {
  const STORAGE_KEY = "readerSettings";
  const SETTINGS_VERSION = 2;
  const FONT_FAMILIES = {
    sans: "var(--font-reader-sans)",
    serif: "var(--font-reader-serif)",
    mono: "var(--font-mono)",
  };
  const MARGINS = { compact: "16px", normal: "22px", wide: "28px" };
  const THEMES = new Set(["aurora", "amoled", "sepia", "light", "system"]);
  const PRESETS = {
    instant: { paragraphAnimation: "none", revealTempo: "instant" },
    smooth: { paragraphAnimation: "fade", revealTempo: "instant" },
    speed: { paragraphAnimation: "none", revealTempo: "word-by-word" },
  };
  const DEFAULTS = {
    fontFamily: "serif",
    fontSize: "18",
    lineHeight: "1.85",
    width: "720",
    margins: "normal",
    theme: "aurora",
    readerMode: "scroll",
    tapToRead: false,
    revealPreset: "instant",
    paragraphStyle: "book",
    paragraphAnimation: "none",
    revealTempo: "instant",
    showParagraphSocial: true,
    // PR 166: independent of fontSize - a bigger reading font shouldn't force bigger
    // comments and vice versa.
    commentFontSize: "14",
    // PR 334: how many next chapters of a downloaded title to keep on the device ("0" -
    // off), and whether that may use any network where the browser can't tell Wi-Fi.
    autoDownloadNext: "0",
    autoDownloadAnyNetwork: false,
    // PR 338: hold a screen Wake Lock while reading - off by default (battery).
    keepScreenOn: false,
    // PR 339: a downloaded chapter whose page the network is slow to give opens from the
    // copy on the device (app/pwa/service-worker.js) - on by default.
    openDownloadedFromCopy: true,
  };
  // Not how the text looks - kept by «Сбросить настройки».
  const KEPT_ON_RESET = [
    "readingSpeedWpm",
    "autoDownloadNext",
    "autoDownloadAnyNetwork",
    "keepScreenOn",
    "openDownloadedFromCopy",
  ];

  const root = document.documentElement;

  function presetFor(settings) {
    const match = Object.entries(PRESETS).find(
      ([, values]) =>
        values.paragraphAnimation === settings.paragraphAnimation &&
        values.revealTempo === settings.revealTempo
    );
    return match ? match[0] : "custom";
  }

  function readStored() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") || {};
    } catch {
      return null;
    }
  }

  function save(settings) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      // storage blocked - the change still applies to this page
    }
  }

  // PR 254: "book" replaced the chat bubble as the default paragraph style. Anyone who
  // ever changed a setting has the old default "chat" saved alongside it, so a stored
  // "chat" from before settingsVersion 2 is taken as that default, not a choice.
  function load() {
    const stored = readStored();
    if (stored === null) return { ...DEFAULTS };
    if ((stored.settingsVersion || 1) < SETTINGS_VERSION) {
      if (stored.paragraphStyle === "chat") stored.paragraphStyle = "book";
      stored.settingsVersion = SETTINGS_VERSION;
      save(stored);
    }
    const settings = { ...DEFAULTS, ...stored };
    // Settings saved before PR 255 only know tapToRead / the two raw reveal keys.
    if (!("readerMode" in stored)) settings.readerMode = settings.tapToRead ? "tap" : "scroll";
    settings.tapToRead = settings.readerMode === "tap";
    if (!("revealPreset" in stored)) settings.revealPreset = presetFor(settings);
    if (!THEMES.has(settings.theme)) settings.theme = DEFAULTS.theme;
    return settings;
  }

  function apply(settings) {
    root.style.setProperty(
      "--reader-font-family",
      FONT_FAMILIES[settings.fontFamily] || FONT_FAMILIES.serif
    );
    root.style.setProperty("--reader-font-size", `${settings.fontSize}px`);
    root.style.setProperty("--reader-line-height", settings.lineHeight);
    root.style.setProperty("--reader-width", `${settings.width}px`);
    root.style.setProperty("--reader-margin", MARGINS[settings.margins] || MARGINS.normal);
    root.style.setProperty("--comment-font-size", `${settings.commentFontSize}px`);
    if (settings.theme === "aurora") delete root.dataset.readerTheme;
    else root.dataset.readerTheme = settings.theme;
    root.classList.toggle("reader-social-off", settings.showParagraphSocial === false);
  }

  let settings = load();
  apply(settings);

  // --- controls ------------------------------------------------------------------------
  let syncing = false;

  function controls() {
    return document.querySelectorAll("[data-setting]");
  }

  function sync() {
    syncing = true;
    for (const control of controls()) {
      const value = settings[control.dataset.setting];
      if (control.type === "checkbox") {
        control.checked = Boolean(value);
      } else if (control.type === "radio") {
        control.checked = String(value) === control.value;
      } else if (String(control.value) !== String(value)) {
        control.value = value;
        // custom-dropdown.js repaints its trigger on "change", ui-range.js its fill on
        // "input" - both ignored by bind() below while syncing.
        if (control.tagName === "SELECT") control.dispatchEvent(new Event("change"));
        if (control.type === "range") control.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    for (const output of document.querySelectorAll("[data-setting-value]")) {
      output.textContent = settings[output.dataset.settingValue];
    }
    for (const output of document.querySelectorAll("output[for]")) {
      const control = document.getElementById(output.getAttribute("for"));
      if (control?.dataset.setting) output.textContent = settings[control.dataset.setting];
    }
    syncing = false;
  }

  function set(key, value) {
    const previousMode = settings.readerMode;
    // reading-speed-test.js writes readingSpeedWpm straight to storage - don't save a
    // stale copy of it back over the new value.
    const stored = readStored();
    if (stored && "readingSpeedWpm" in stored) settings.readingSpeedWpm = stored.readingSpeedWpm;
    const next = { ...settings, [key]: value };
    if (key === "readerMode") next.tapToRead = value === "tap";
    if (key === "tapToRead") next.readerMode = value ? "tap" : "scroll";
    if (key === "revealPreset" && PRESETS[value]) Object.assign(next, PRESETS[value]);
    if (key === "paragraphAnimation" || key === "revealTempo") next.revealPreset = presetFor(next);
    settings = next;
    save(settings);
    apply(settings);
    sync();
    document.dispatchEvent(
      new CustomEvent("reader-settings:change", {
        detail: { key, settings: { ...settings }, modeChanged: previousMode !== settings.readerMode },
      })
    );
  }

  function bind(control) {
    if (control.dataset.settingBound) return;
    control.dataset.settingBound = "1";
    const key = control.dataset.setting;
    const handler = () => {
      if (syncing) return;
      if (control.type === "checkbox") set(key, control.checked);
      else if (control.type === "radio") {
        if (control.checked) set(key, control.value);
      } else set(key, control.value);
    };
    control.addEventListener(control.tagName === "SELECT" || control.type === "radio" ? "change" : "input", handler);
  }

  function bindAll() {
    controls().forEach(bind);
    for (const button of document.querySelectorAll("[data-setting-step]")) {
      if (button.dataset.settingBound) continue;
      button.dataset.settingBound = "1";
      button.addEventListener("click", () => {
        const key = button.dataset.settingStep;
        const min = Number(button.dataset.min ?? -Infinity);
        const max = Number(button.dataset.max ?? Infinity);
        const next = Math.min(max, Math.max(min, Number(settings[key]) + Number(button.dataset.step)));
        set(key, String(next));
      });
    }
    for (const button of document.querySelectorAll('[data-role="reader-settings-reset"]')) {
      if (button.dataset.settingBound) continue;
      button.dataset.settingBound = "1";
      button.addEventListener("click", () => {
        const previousMode = settings.readerMode;
        // The measured reading speed, auto-download and keeping the screen on aren't
        // display preferences - they survive a reset.
        const kept = Object.fromEntries(
          KEPT_ON_RESET.filter((name) => settings[name] !== undefined).map((name) => [name, settings[name]])
        );
        settings = { ...DEFAULTS, ...kept, settingsVersion: SETTINGS_VERSION };
        save(settings);
        apply(settings);
        sync();
        document.dispatchEvent(
          new CustomEvent("reader-settings:change", {
            detail: { key: null, settings: { ...settings }, modeChanged: previousMode !== settings.readerMode },
          })
        );
      });
    }
    sync();
  }

  // Another tab (or reading-speed-test.js) changed the stored object.
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    settings = load();
    apply(settings);
    sync();
  });

  window.readerSettings = { get: () => ({ ...settings }), set, bindAll };
  bindAll();
})();
