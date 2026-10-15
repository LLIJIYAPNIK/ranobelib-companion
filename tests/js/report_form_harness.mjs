// PR 357: runs report-form.js against a small fake DOM - the form rendered in base.html,
// its <dialog>, the shared bottom sheet and fetch - once as desktop and once as a phone,
// and prints what each step left behind as JSON for tests/test_report_form.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

function element(extra = {}) {
  const listeners = {};
  const attributes = {};
  const el = {
    hidden: false,
    textContent: "",
    dataset: {},
    focused: false,
    addEventListener: (type, callback) => (listeners[type] ||= []).push(callback),
    dispatch(type, event = {}) {
      const full = { type, target: el, defaultPrevented: false, ...event };
      full.preventDefault = () => (full.defaultPrevented = true);
      for (const callback of listeners[type] || []) callback(full);
      return full;
    },
    setAttribute: (name, value) => (attributes[name] = String(value)),
    removeAttribute: (name) => delete attributes[name],
    getAttribute: (name) => attributes[name] ?? null,
    attributes,
    isConnected: true,
    ...extra,
  };
  el.focus = () => {
    state.focus = el.label || el.name || "?";
  };
  return el;
}

const state = { focus: null };

function loadPage({ phone }) {
  const calls = { fetch: [], showModal: 0, sheetOpen: [], sheetClose: 0 };
  const radios = ["spam", "abuse", "other"].map((value) =>
    element({ name: "reason", value, checked: false, label: `radio-${value}` })
  );
  const details = element({ name: "details", value: "", maxLength: 500 });
  const targetType = element({ name: "target_type", value: "" });
  const targetId = element({ name: "target_id", value: "" });
  const count = element();
  const error = element({ hidden: true });
  const submit = element({ disabled: false, label: "submit", textContent: "Отправить" });
  const cancel = element({ label: "cancel" });
  const form = element({
    elements: { details, target_type: targetType, target_id: targetId },
    reset() {
      radios.forEach((radio) => (radio.checked = false));
      details.value = "";
    },
    querySelector(selector) {
      if (selector === 'input[name="reason"]:checked') return radios.find((r) => r.checked) || null;
      if (selector === 'input[name="reason"]') return radios[0];
      return null;
    },
  });
  const home = { children: [] };
  const root = element({
    hidden: true,
    parentNode: {
      insertBefore: (node) => {
        root.where = "home";
      },
    },
    nextSibling: null,
    where: "home",
    querySelector(selector) {
      return {
        '[data-role="report-form-form"]': form,
        '[data-role="report-count"]': count,
        '[data-role="report-error"]': error,
        '[data-role="report-submit"]': submit,
        '[data-role="report-cancel"]': cancel,
        "[data-autofocus]": radios[0],
      }[selector];
    },
  });
  const dialogTitle = element();
  const dialogBody = element({ append: (node) => (node.where = "dialog") });
  const dialogClose = element({ label: "dialog-close" });
  const dialog = element({
    open: false,
    showModal() {
      calls.showModal += 1;
      dialog.open = true;
    },
    close() {
      if (!dialog.open) return;
      dialog.open = false;
      dialog.dispatch("close");
    },
    querySelector(selector) {
      return {
        '[data-role="report-dialog-title"]': dialogTitle,
        '[data-role="report-dialog-body"]': dialogBody,
        '[data-role="report-dialog-close"]': dialogClose,
      }[selector];
    },
  });
  const toasts = [];
  const documentListeners = {};
  const document = {
    querySelector: (selector) =>
      ({ '[data-role="report-form"]': root, '[data-role="report-dialog"]': dialog })[selector],
    addEventListener: (type, callback) => (documentListeners[type] ||= []).push(callback),
    createElement: () => {
      const el = element();
      el.replaceChildren = (child) => (el.textContent = child.textContent);
      return el;
    },
    body: { append: (el) => toasts.push(el) },
  };
  let respond = null;
  const sheet = {
    current: null,
    open(options) {
      calls.sheetOpen.push({ title: options.title, content: options.content === root });
      sheet.current = options;
      root.where = "sheet";
    },
    close() {
      calls.sheetClose += 1;
      const current = sheet.current;
      sheet.current = null;
      if (current) {
        root.where = "home";
        current.onClose?.();
      }
    },
  };
  const context = {
    document,
    matchMedia: () => ({ matches: phone }),
    bottomSheet: sheet,
    setTimeout: () => 1,
    clearTimeout: () => {},
    FormData: class {
      constructor() {
        this.reason = radios.find((r) => r.checked)?.value ?? null;
        this.target = `${targetType.value}:${targetId.value}`;
        this.details = details.value;
      }
    },
    fetch: (url, options) => {
      calls.fetch.push({ url, method: options.method, ...options.body });
      return new Promise((resolve, reject) => {
        respond = { resolve, reject };
      });
    },
  };
  context.window = context;
  vm.runInNewContext(source, context);

  const snapshot = () => ({
    where: root.where,
    hidden: root.hidden,
    dialogOpen: dialog.open,
    title: dialogTitle.textContent,
    target: `${targetType.value}:${targetId.value}`,
    error: error.hidden ? null : error.textContent,
    submitDisabled: submit.disabled,
    submitText: submit.textContent,
    busy: form.getAttribute("aria-busy"),
    count: count.textContent,
    toast: toasts[0] && !toasts[0].hidden ? toasts[0].textContent : null,
    toastRole: toasts[0]?.getAttribute("role") ?? null,
    focus: state.focus,
    fetches: calls.fetch.length,
    showModal: calls.showModal,
    sheetOpen: calls.sheetOpen.length,
    sheetClose: calls.sheetClose,
  });

  async function reply(status, body) {
    respond.resolve({ ok: status < 300, status, json: async () => body });
    await new Promise((resolve) => setImmediate(resolve));
  }
  async function fail() {
    respond.reject(new TypeError("offline"));
    await new Promise((resolve) => setImmediate(resolve));
  }
  function submitForm() {
    form.dispatch("submit");
  }
  function clickButton(type, id) {
    const button = element({ label: `opener-${type}-${id}`, dataset: { reportTargetType: type, reportTargetId: String(id) } });
    const event = { target: { closest: () => button }, defaultPrevented: false };
    event.preventDefault = () => (event.defaultPrevented = true);
    for (const callback of documentListeners.click || []) callback(event);
    return event.defaultPrevented;
  }
  return { context, radios, details, dialog, form, calls, snapshot, reply, fail, submitForm, clickButton, cancel };
}

async function desktop() {
  const page = loadPage({ phone: false });
  const out = {};
  const opener = element({ label: "comment-button" });
  page.context.reportForm.open({ targetType: "comment", targetId: 5, opener });
  out.opened = page.snapshot();

  page.submitForm();
  out.noReason = page.snapshot();

  page.radios[1].checked = true;
  page.form.dispatch("change", { target: page.radios[1] });
  page.details.value = "грубит";
  page.details.dispatch("input");
  out.picked = page.snapshot();

  page.submitForm();
  out.sending = page.snapshot();
  out.sentBody = page.calls.fetch[0];
  out.escapeWhileSending = page.dialog.dispatch("cancel").defaultPrevented;
  page.submitForm(); // a second click while sending does nothing
  out.fetchesWhileSending = page.calls.fetch.length;
  await page.reply(409, { detail: "Комментарий уже скрыт" });
  out.refused = page.snapshot();

  page.submitForm();
  await page.fail();
  out.offline = page.snapshot();

  page.submitForm();
  await page.reply(422, { detail: [{ msg: "bad" }] });
  out.unreadable = page.snapshot();

  page.submitForm();
  await page.reply(201, { status: "sent", message: "Жалоба отправлена" });
  out.sent = page.snapshot();

  page.context.reportForm.open({ targetType: "comment", targetId: 5, opener });
  out.repeatOnPage = page.snapshot();

  out.delegatedHandled = page.clickButton("profile", 7);
  out.profileOpened = page.snapshot();
  page.radios[0].checked = true;
  page.submitForm();
  await page.reply(200, { status: "already_sent", message: "Вы уже отправили жалобу" });
  out.alreadySent = page.snapshot();

  page.context.reportForm.open({ targetType: "comment", targetId: 9, opener });
  page.cancel.dispatch("click");
  out.cancelled = page.snapshot();

  page.context.reportForm.open({ targetType: "title", targetId: 1, opener });
  out.unknownType = page.snapshot();
  return out;
}

async function phone() {
  const page = loadPage({ phone: true });
  const out = {};
  const opener = element({ label: "profile-more" });
  page.context.reportForm.open({ targetType: "profile", targetId: 7, opener });
  out.opened = page.snapshot();
  out.sheetTitle = page.calls.sheetOpen[0];
  page.radios[2].checked = true;
  page.submitForm();
  await page.reply(201, { status: "sent", message: "Жалоба отправлена" });
  out.sent = page.snapshot();
  page.context.reportForm.open({ targetType: "profile", targetId: 7, opener });
  out.repeatOnPage = page.snapshot();
  return out;
}

const result = { desktop: await desktop() };
state.focus = null;
result.phone = await phone();
process.stdout.write(JSON.stringify(result));
