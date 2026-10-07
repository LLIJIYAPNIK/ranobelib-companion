// PR 131: a right-click on any paragraph opens a small context menu anchored at the
// click point - infrastructure for PR 132 (reactions) and PR 133 (comments).
//
// One delegated "contextmenu" listener on .reader-content itself, not one per paragraph -
// chapter.content arrives from the SDK as a single opaque HTML blob, so the number and
// shape of its children isn't known ahead of time (same reason tap-to-read.js/
// reader-progress.js delegate rather than attach per-paragraph listeners).
//
// "Which paragraph" is identified the same way tap-to-read.js/reader-progress.js already
// do it: the index of a .reader-content direct child among its siblings. In tap-to-read
// mode that child is the plate wrapping the real paragraph (tap-to-read.js replaces each
// original element with one before this script ever runs); in the ordinary reading mode
// it's the paragraph itself. Walking up from event.target to whichever ancestor is a
// direct child of .reader-content works unmodified for both, and lines up with the exact
// same index those two scripts use for their own progress tracking - one shared anchor,
// not a second one invented here.
//
// PR 132: "Реакции" opens a strip of 10 emoji in place of the two-item list - picking one
// POSTs to /titles/{slug}/chapters/{volume}/{number}/reactions (app/api/chapters.py) and
// refreshes the little counts strip rendered under that paragraph.
//
// PR 133: "Комментировать" opens a text composer the same way - it POSTs to
// .../comments and creates a top-level comment. Every paragraph with at least one
// comment also gets an always-visible "N комментариев ▾" toggle (unlike the reactions
// strip, which is hover-only in the ordinary reading mode - comments are a more durable
// affordance, not a decorative overlay); the actual thread loads lazily, only once that
// toggle is clicked. Each comment has its own "Ответить" opening an inline reply
// composer, nested reddit-style under its parent via CSS (see .paragraph-comment__replies
// in app.css). PR 165: renderCommentNode() does track its own depth now, just to cap how
// far the indent grows - see MAX_INDENT_DEPTH above.
//
// PR 312 (Webnovells): the comment layer - paragraph context, thread and composer -
// opens inline under the paragraph on desktop and in the shared bottom sheet on phones;
// see "PR 133: comments" below.
//
// PR 134: readerSettings.showParagraphSocial (default true, like every other reading
// setting) gates this whole file - once it's explicitly false there is nothing left for
// a right-click to open (both PR 132's reactions and PR 133's comments are it), so
// nothing here so much as attaches a listener rather than just hiding what would've
// rendered. Existing reactions/comments aren't touched server-side by this - the setting
// only ever decides whether this script fetches/renders them, never deletes anything.
(() => {
  const GAP = 8;

  // PR 165: revisits PR 133's original choice not to thread a depth counter through
  // renderCommentNode() at all ("recursion ... doesn't need to know or pass its own depth
  // down at all") - true as long as indentation only ever cost a fixed per-level
  // margin-left, but a real thread nests deep enough (see the PR 165 screenshot) that the
  // cumulative indent from .paragraph-comment__replies > .paragraph-comment (app.css)
  // eats nearly the whole comment column, leaving the text itself a few characters wide.
  // MAX_INDENT_DEPTH caps how many levels keep marching right; renderCommentNode() below
  // tags any .paragraph-comment__replies past it with a modifier class that freezes
  // margin-left at 0 for its own children (app.css), so deeper threads stay flush at the
  // last indented level instead of running off the edge of the column.
  const MAX_INDENT_DEPTH = 6;

  function loadReaderSettings() {
    try {
      return JSON.parse(localStorage.getItem("readerSettings") || "{}");
    } catch {
      return {};
    }
  }
  if (loadReaderSettings().showParagraphSocial === false) return;

  // Must stay in sync with ALLOWED_EMOJI in app/db/reactions.py - both lists exist
  // independently (no shared JSON between Python and JS in this codebase), so a change
  // to one needs the same change made to the other. Labels are for aria-label/title only,
  // not sent to the server.
  const EMOJI = [
    ["👍", "Нравится"],
    ["❤️", "Любовь"],
    ["😂", "Смешно"],
    ["😮", "Удивление"],
    ["😢", "Грустно"],
    ["😡", "Злость"],
    ["🔥", "Огонь"],
    ["👏", "Аплодисменты"],
    ["🤔", "Задумчиво"],
    ["💯", "Круто"],
  ];

  const content = document.querySelector('[data-role="chapter"]');
  if (!content) return;

  const slugUrl = content.dataset.slugUrl || "";
  const volume = content.dataset.volume || "";
  const number = content.dataset.number || "";
  const branchId = content.dataset.branchId || "";
  const isAuthenticated = content.dataset.authenticated === "1";
  // PR 172: compared against comment.user_id to decide whether "Изменить"/"Удалить" show
  // on a given comment - "" (logged out) never matches a real id, so those never render
  // for an anonymous visitor either. The real ownership check still happens server-side
  // (app/db/comments.py's edit_comment()/delete_comment() scope their UPDATE by user_id) -
  // this only decides what the UI offers to click.
  const currentUserId = content.dataset.userId ? Number(content.dataset.userId) : null;

  // Finds the .reader-content child (paragraph plate or bare paragraph) that `target`
  // sits inside, or null if the click landed outside them entirely (e.g. the "tap to
  // continue" hint tap-to-read.js appends after the last revealed paragraph).
  function paragraphElementFor(target) {
    let el = target;
    while (el && el.parentElement !== content) {
      el = el.parentElement;
    }
    return el && el.parentElement === content ? el : null;
  }

  // Where a paragraph's own reactions strip and/or comments section live - shared by
  // both, so a paragraph that ends up with one of each still gets wrapped exactly once.
  // In tap-to-read mode `content.children[index]` is already tap-to-read.js's own
  // generic <div> plate wrapping the real paragraph (plus PR 64's timestamp span) - the
  // strip/section are just more children appended there. In the ordinary mode it's the
  // SDK's own raw element, which can be anything the sanitizer allows - including a bare
  // <img>, a void element that can't have children at all - so the first paragraph that
  // actually needs to host either one gets lazily wrapped in a plain <div> the same way,
  // replacing itself in `content` with that wrapper and moving inside it. Reparenting
  // like this doesn't disturb reader-progress.js's own IntersectionObserver (already
  // watching the raw element by reference by the time this ever runs - script order in
  // chapter.html puts reader-progress.js before this file - and observation survives an
  // observed node being moved to a new parent, only an explicit unobserve() or removal
  // from the document would stop it), nor paragraphElementFor()/tap-to-read.js's own
  // indexing (content.children keeps the same length and order either way, just with a
  // wrapper standing in for one entry).
  function paragraphHostFor(index) {
    const el = content.children[index];
    if (!el) return null;
    if (el.classList.contains("reader-content__paragraph-wrap")) return el;
    if (el.classList.contains("paragraph-reactions-host")) return el;
    const host = document.createElement("div");
    host.className = "paragraph-reactions-host";
    el.replaceWith(host);
    host.appendChild(el);
    return host;
  }

  // PR 156: the original chapter paragraph a menu is open for, unwrapped from whichever
  // host may have replaced it in `content.children` - paragraphHostFor above always
  // appends the raw element as the wrapper's *first* child before anything else
  // (reactions strip, comments section, tap-to-read's own timestamp span) gets added, in
  // both wrapper kinds it recognizes, so `firstElementChild` reliably isolates just the
  // paragraph's own text from that later UI. An unwrapped paragraph (no reactions/
  // comments attached yet) has no such wrapper to unwrap - `content.children[index]` is
  // already the raw element in that case.
  function paragraphContentElementFor(index) {
    const el = content.children[index];
    if (!el) return null;
    const wrapped =
      el.classList.contains("reader-content__paragraph-wrap") ||
      el.classList.contains("paragraph-reactions-host");
    return wrapped ? el.firstElementChild : el;
  }

  // `> `-prefixes every line, blank lines included (a bare `>`, not `> ` with trailing
  // whitespace nh3 would just strip again) - markdown-it's blockquote rule only keeps
  // consecutive `>`-prefixed lines together as one quote, so a blank *unprefixed* line
  // would end the quote early instead of just separating two paragraphs inside it.
  function quoteLines(text) {
    return text
      .split("\n")
      .map((line) => (line ? `> ${line}` : ">"))
      .join("\n");
  }

  // The rendered paragraph text, not its raw HTML - .innerText (not .textContent) so a
  // paragraph with actual line breaks in its layout (e.g. a <ul> the SDK's sanitizer
  // allowed through) quotes as multiple prefixed lines instead of one run-together line.
  function quoteParagraphText(index) {
    const el = paragraphContentElementFor(index);
    return el ? quoteLines(el.innerText.trim()) : "";
  }

  // PR 313 (Webnovells): paragraph reactions live in the paragraph's own row - the same
  // .paragraph-comments__bar as the «N комментариев» pill (PR 312), always visible rather
  // than a hover-only overlay. Each emoji is a .wn-reaction chip with its count; clicking
  // one toggles it exactly as picking it in the picker does, «+» opens the picker, and
  // past stripLimit() emoji (fewer on phones, where chips are 44px) the rest fold behind
  // a «+N» chip. Clicks render at once and roll
  // back if the server refuses (reaction-state.js).
  const narrowQuery = window.matchMedia("(max-width: 767px)");
  const stripLimit = () => (narrowQuery.matches ? 3 : 5);
  const EMOJI_LABELS = new Map(EMOJI);
  const reactionsByIndex = new Map(); // index -> { counts, mine }
  const pendingReactions = new Set();
  const expandedStrips = new Set();
  const reactionState = window.reactionState;

  const ICON_ADD_REACTION =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M20.5 11.5a8.5 8.5 0 1 1-8-8.49"/><path d="M8.5 14.5c.9 1.2 2.1 1.8 3.5 1.8s2.6-.6 3.5-1.8"/>' +
    '<path d="M9 9.5h.01M15 9.5h.01" stroke-width="2.6"/><path d="M19 2v6M16 5h6"/></svg>';

  function hasReactions(index) {
    const state = reactionsByIndex.get(index);
    return Boolean(state && Object.values(state.counts || {}).some((n) => n > 0));
  }

  function reactionChip(index, emoji, n, mine, pending) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "wn-reaction";
    chip.dataset.key = emoji;
    if (mine) chip.classList.add("wn-reaction--mine");
    chip.setAttribute("aria-pressed", mine ? "true" : "false");
    chip.setAttribute("aria-label", `${EMOJI_LABELS.get(emoji) || emoji}: ${n}`);
    chip.title = EMOJI_LABELS.get(emoji) || emoji;
    if (pending) chip.setAttribute("aria-busy", "true");
    const glyph = document.createElement("span");
    glyph.className = "wn-reaction__emoji";
    glyph.textContent = emoji;
    const count = document.createElement("span");
    count.className = "wn-reaction__count";
    count.textContent = String(n);
    chip.append(glyph, count);
    chip.addEventListener("click", () => {
      if (!isAuthenticated) {
        window.location.href = "/login";
        return;
      }
      toggleReaction(index, emoji);
    });
    return chip;
  }

  // Redraws the paragraph's reaction chips from reactionsByIndex - in a fixed order (the
  // picker's), so a click never reshuffles the row under the pointer.
  function renderStrip(index, pending = false) {
    const parts = partsFor(index);
    if (!parts) return;
    const strip = parts.strip;
    const state = reactionsByIndex.get(index) || { counts: {}, mine: null };
    const entries = EMOJI.map(([emoji]) => [emoji, state.counts?.[emoji] || 0]).filter(
      ([, n]) => n > 0
    );
    // Re-rendering replaces the chips - focus goes back to the same one (by data-key).
    const focusedKey = strip.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    strip.replaceChildren();
    strip.toggleAttribute("aria-busy", pending);
    const limit = stripLimit();
    const folded = entries.length > limit && !expandedStrips.has(index);
    const shown = folded
      ? entries.filter(([emoji], i) => i < limit - 1 || emoji === state.mine)
      : entries;
    for (const [emoji, n] of shown) {
      strip.append(reactionChip(index, emoji, n, emoji === state.mine, pending && emoji === state.mine));
    }
    if (folded) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "wn-reaction wn-reaction--more";
      more.dataset.key = "more";
      const hiddenCount = entries.length - shown.length;
      more.textContent = `+${hiddenCount}`;
      more.setAttribute("aria-label", `Показать ещё ${hiddenCount} ${plural(hiddenCount, "реакцию", "реакции", "реакций")}`);
      more.addEventListener("click", () => {
        expandedStrips.add(index);
        renderStrip(index);
        strip.children[Math.min(limit - 1, strip.children.length - 1)]?.focus();
      });
      strip.append(more);
    }
    if (isAuthenticated && entries.length > 0) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "wn-reaction wn-reaction--add";
      add.dataset.key = "add";
      add.setAttribute("aria-label", "Добавить реакцию");
      add.title = "Добавить реакцию";
      add.innerHTML = ICON_ADD_REACTION;
      add.addEventListener("click", (event) => {
        event.stopPropagation();
        const rect = add.getBoundingClientRect();
        open(rect.left, rect.bottom + 6, index, { picker: true, opener: add });
      });
      strip.append(add);
    }
    if (focusedKey) {
      const again = [...strip.children].find((el) => el.dataset.key === focusedKey);
      (again || strip.querySelector(".wn-reaction"))?.focus({ preventScroll: true });
    }
    syncSection(index);
  }

  function setReactions(index, state, pending = false) {
    reactionsByIndex.set(index, { counts: state.counts || {}, mine: state.mine ?? null });
    renderStrip(index, pending);
  }

  // One bulk fetch for the whole chapter on load, not one per paragraph - a chapter page
  // can have dozens (see app/db/reactions.py's count_reactions()).
  async function loadInitialReactions() {
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/reactions?branch_id=${encodeURIComponent(branchId)}`
      );
      if (!response.ok) return;
      const data = await response.json();
      for (const [indexStr, counts] of Object.entries(data.counts || {})) {
        const index = Number(indexStr);
        setReactions(index, { counts, mine: data.mine?.[indexStr] ?? null });
      }
    } catch {
      // No network, or the server errored - the chapter still reads fine without counts.
    }
  }
  loadInitialReactions();

  // The chip, the picker and the menu all end up here: shown at once, confirmed or rolled
  // back by the server's answer (one request per paragraph at a time).
  async function toggleReaction(index, emoji) {
    if (pendingReactions.has(index)) return;
    pendingReactions.add(index);
    const previous = reactionsByIndex.get(index) || { counts: {}, mine: null };
    const ok = await reactionState.optimistic({
      previous,
      guess: reactionState.toggleEmoji(previous.counts, previous.mine, emoji),
      render: (state, pending) => setReactions(index, state, pending),
      request: async () => {
        const body = new URLSearchParams({
          paragraph_index: String(index),
          emoji,
          branch_id: branchId,
        });
        const response = await fetch(`/titles/${slugUrl}/chapters/${volume}/${number}/reactions`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body,
        });
        return response.ok ? response.json() : null;
      },
    });
    pendingReactions.delete(index);
    if (!ok) flashBarError(index, "Не удалось сохранить реакцию");
  }

  // A short-lived note in the paragraph's row after a rolled-back reaction.
  function flashBarError(index, message) {
    const parts = partsFor(index);
    if (!parts) return;
    parts.error.textContent = message;
    parts.error.hidden = false;
    clearTimeout(parts.error.hideTimer);
    parts.error.hideTimer = setTimeout(() => {
      parts.error.hidden = true;
    }, 4000);
  }

  function pickReaction(index, emoji) {
    const fromRow = menuOpener && partsFor(index)?.strip.contains(menuOpener);
    close(true);
    const done = toggleReaction(index, emoji);
    if (fromRow) {
      // The strip is redrawn under it - land on the new «+» (or the row's first chip).
      done.then(() => {
        const strip = partsFor(index)?.strip;
        (strip?.querySelector('[data-key="add"]') || strip?.querySelector(".wn-reaction"))?.focus({
          preventScroll: true,
        });
      });
    }
  }

  // --- PR 133: comments -----------------------------------------------------------
  //
  // PR 312 (Webnovells): the comment layer was a 240px composer squeezed into the
  // floating menu plus an early thread layout. Now every paragraph with comments (or one
  // the visitor has just chosen to comment on) gets one layer: a header naming the
  // paragraph and quoting it, the thread (loading/empty/error states), and the composer.
  // On desktop it opens inline, in normal flow right under the paragraph - it pushes the
  // text down rather than floating over it, so it can never cover it or be clipped by the
  // viewport edge. On phones the same element is handed to the shared bottom sheet
  // (bottom-sheet.js, PR 279), which puts it back when it closes. Endpoints, data and the
  // reply/edit/delete/markdown/attachment behavior are unchanged.

  const phoneQuery = window.matchMedia("(max-width: 767px)");
  const MAX_COMMENT_LENGTH = 2000; // mirrors MAX_COMMENT_LENGTH in app/db/comments.py
  const COUNTER_FROM = 1800; // the length counter only shows once it starts to matter

  function plural(n, one, few, many) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) return few;
    return many;
  }

  function pluralizeComments(n) {
    return plural(n, "комментарий", "комментария", "комментариев");
  }

  // Same rules as app/api/notifications.py's relative_time() (PR 311): durations, then a
  // short date - one way of saying "when" across the site.
  const MONTHS = "янв. февр. мар. апр. мая июн. июл. авг. сент. окт. нояб. дек.".split(" ");
  function relativeTime(iso) {
    const moment = new Date(iso);
    const seconds = Math.max(0, Math.floor((Date.now() - moment.getTime()) / 1000));
    if (seconds < 60) return "только что";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч назад`;
    if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} дн. назад`;
    const date = `${moment.getDate()} ${MONTHS[moment.getMonth()]}`;
    return moment.getFullYear() === new Date().getFullYear() ? date : `${date} ${moment.getFullYear()}`;
  }

  function fullTime(iso) {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function svgIcon(paths, strokeWidth = 1.8) {
    return (
      `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ` +
      `stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
      paths +
      "</svg>"
    );
  }

  const ICON_COMMENT = svgIcon('<path d="M4 5h16v11H9l-5 4z"/>');
  const ICON_SMILE = svgIcon(
    '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c.9 1.2 2.1 1.8 3.5 1.8s2.6-.6 3.5-1.8"/>' +
      '<path d="M9 9.5h.01M15 9.5h.01" stroke-width="2.6"/>'
  );
  const ICON_CLIP = svgIcon(
    '<path d="m20 11.5-8.1 8.1a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.7-7.7"/>'
  );
  const ICON_CLOSE = svgIcon('<path d="M6 6l12 12M18 6 6 18"/>', 2);

  // PR 147: the same picture-or-initials pairing base.html's Jinja templates render via
  // avatar_url(user)/avatar_initials(user) - comment.avatar_url/avatar_initials arrive
  // already computed by the same server-side helpers (app/auth/avatar.py).
  function buildCommentAvatar(comment) {
    const avatar = document.createElement("span");
    avatar.className = "paragraph-comment__avatar";
    if (comment.avatar_url) {
      const img = document.createElement("img");
      img.className = "avatar-img";
      img.src = comment.avatar_url;
      img.alt = "";
      avatar.append(img);
    } else {
      avatar.textContent = comment.avatar_initials;
    }
    return avatar;
  }

  // Per-paragraph comment state, keyed by index:
  // - commentCountByIndex is the single source of truth for the number on the toggle -
  //   set from the initial bulk fetch and refreshed after every post, never recomputed
  //   from the tree (whose root-level length isn't the "replies included" count).
  // - commentTreeByIndex is the nested tree, loaded lazily the first time the layer opens
  //   and reused afterwards, so reopening needs no request.
  // - commentsExpandedByIndex tracks which layers are open.
  // - loadFailedIndexes: the last tree load failed - the layer offers «Повторить».
  // - collapsedCommentIds: reply threads the visitor folded - kept across re-renders.
  const commentCountByIndex = new Map();
  const commentTreeByIndex = new Map();
  const commentsExpandedByIndex = new Set();
  const loadingIndexes = new Set();
  const loadFailedIndexes = new Set();
  const collapsedCommentIds = new Set();

  // PR 149: free-form emoji for the composer - not the 10-emoji paragraph reaction
  // palette above. PR 312: an inline palette inside the composer itself rather than a
  // floating popover, so it can't be clipped, works the same inside the bottom sheet and
  // stays in the sheet's focus order.
  const COMMENT_EMOJI = [
    "😀", "😁", "😂", "🤣", "😊", "😉", "😍", "😘", "😜", "🤔",
    "😐", "😴", "😭", "😢", "😡", "🥳", "😱", "🤯", "🥰", "😎",
    "👍", "👎", "👏", "🙏", "💪", "🤝", "👋", "✌️", "🤞", "👌",
    "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "💔", "💯", "🔥",
    "🎉", "✨", "⭐", "☀️", "🌙", "☕", "🍕", "🎮",
  ];

  // Inserts at the caret (replacing any current selection) rather than always appending
  // to the end, so picking an emoji mid-sentence lands where the visitor was typing.
  function insertAtCursor(textarea, text) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? textarea.value.length;
    textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
    const caret = start + text.length;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
    textarea.dispatchEvent(new Event("input"));
  }

  let composerSeq = 0;

  // A composer: an auto-growing textarea with emoji/attachment triggers and one primary
  // submit, shared by the layer's own "new comment" box, every comment's reply form and
  // "Изменить". `onSubmit(body, file)` resolves to {ok} or {ok: false, message}; the text
  // (and staged file) is only cleared on success. `context` adds a line above the field
  // («Ответ для …», «Редактирование») with a cancel button; Escape cancels too.
  // PR 156: `initialValue` pre-fills the textarea. PR 172: `allowAttachment: false` for
  // «Изменить», which only ever overwrites `body`.
  function buildComposer({
    onSubmit,
    placeholder,
    initialValue = "",
    allowAttachment = true,
    submitLabel = "Отправить",
    context = null,
    onCancel = null,
    modifier = null,
  }) {
    const id = ++composerSeq;
    const wrap = document.createElement("div");
    wrap.className = "paragraph-comments__composer";
    if (modifier) wrap.classList.add(`paragraph-comments__composer--${modifier}`);

    if (context) {
      const contextRow = document.createElement("div");
      contextRow.className = "paragraph-comments__composer-context";
      const label = document.createElement("span");
      label.textContent = context;
      contextRow.append(label);
      if (onCancel) {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "paragraph-comments__icon-btn";
        cancel.setAttribute("aria-label", "Отменить");
        cancel.title = "Отменить";
        cancel.innerHTML = ICON_CLOSE;
        cancel.addEventListener("click", () => onCancel());
        contextRow.append(cancel);
      }
      wrap.append(contextRow);
    }

    const field = document.createElement("div");
    field.className = "paragraph-comments__field";

    const textarea = document.createElement("textarea");
    textarea.className = "paragraph-comments__textarea";
    textarea.id = `comment-composer-${id}`;
    textarea.placeholder = placeholder;
    textarea.setAttribute("aria-label", placeholder.replace(/…$/, ""));
    textarea.rows = 2;
    textarea.maxLength = MAX_COMMENT_LENGTH;
    textarea.value = initialValue;
    field.append(textarea);

    // PR 150/151: staged client-side until «Отправить» - the file itself travels to the
    // server as multipart (submitComment below). One button for image/video/GIF alike -
    // app/comment_attachment.py sniffs the bytes server-side.
    let stagedAttachment = null;
    let attachmentInput = null;
    let attachmentChip = null;
    let attachmentPreviewUrl = null;

    function clearStagedAttachment() {
      stagedAttachment = null;
      if (attachmentInput) attachmentInput.value = "";
      attachmentChip?.remove();
      attachmentChip = null;
      // Revoked only after the <img> using it is gone.
      if (attachmentPreviewUrl) URL.revokeObjectURL(attachmentPreviewUrl);
      attachmentPreviewUrl = null;
      updateSubmitState();
    }

    function stageAttachment(file) {
      stagedAttachment = file;
      attachmentChip?.remove();
      if (attachmentPreviewUrl) URL.revokeObjectURL(attachmentPreviewUrl);
      attachmentPreviewUrl = null;

      attachmentChip = document.createElement("div");
      attachmentChip.className = "paragraph-comments__attachment-chip";
      // PR 151: a real thumbnail for an image (GIF included), the name for a video.
      if (file.type.startsWith("image/")) {
        attachmentPreviewUrl = URL.createObjectURL(file);
        const thumb = document.createElement("img");
        thumb.className = "paragraph-comments__attachment-chip-thumb";
        thumb.src = attachmentPreviewUrl;
        thumb.alt = "";
        attachmentChip.append(thumb);
      }
      const name = document.createElement("span");
      name.className = "paragraph-comments__attachment-chip-name";
      name.textContent = file.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "paragraph-comments__icon-btn";
      remove.setAttribute("aria-label", "Убрать вложение");
      remove.title = "Убрать вложение";
      remove.innerHTML = ICON_CLOSE;
      remove.addEventListener("click", () => {
        clearStagedAttachment();
        textarea.focus();
      });
      attachmentChip.append(name, remove);
      toolbar.before(attachmentChip);
      updateSubmitState();
    }

    const toolbar = document.createElement("div");
    toolbar.className = "paragraph-comments__toolbar";
    const triggers = document.createElement("div");
    triggers.className = "paragraph-comments__triggers";

    const palette = document.createElement("div");
    palette.className = "paragraph-comments__emoji-palette";
    palette.id = `comment-emoji-${id}`;
    palette.setAttribute("role", "group");
    palette.setAttribute("aria-label", "Эмодзи");
    palette.hidden = true;
    for (const emoji of COMMENT_EMOJI) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "paragraph-comments__emoji";
      button.textContent = emoji;
      button.addEventListener("click", () => insertAtCursor(textarea, emoji));
      palette.append(button);
    }

    const emojiToggle = document.createElement("button");
    emojiToggle.type = "button";
    emojiToggle.className = "paragraph-comments__icon-btn paragraph-comments__emoji-toggle";
    emojiToggle.setAttribute("aria-label", "Эмодзи");
    emojiToggle.setAttribute("aria-expanded", "false");
    emojiToggle.setAttribute("aria-controls", palette.id);
    emojiToggle.title = "Эмодзи";
    emojiToggle.innerHTML = ICON_SMILE;
    emojiToggle.addEventListener("click", () => {
      palette.hidden = !palette.hidden;
      emojiToggle.setAttribute("aria-expanded", palette.hidden ? "false" : "true");
    });
    triggers.append(emojiToggle);

    if (allowAttachment) {
      attachmentInput = document.createElement("input");
      attachmentInput.type = "file";
      attachmentInput.accept = "image/*,video/*";
      attachmentInput.hidden = true;
      attachmentInput.addEventListener("change", () => {
        const file = attachmentInput.files?.[0];
        if (file) stageAttachment(file);
      });
      const attachmentToggle = document.createElement("button");
      attachmentToggle.type = "button";
      attachmentToggle.className = "paragraph-comments__icon-btn paragraph-comments__attachment-toggle";
      attachmentToggle.setAttribute("aria-label", "Прикрепить изображение, GIF или видео");
      attachmentToggle.title = "Прикрепить изображение, GIF или видео";
      attachmentToggle.innerHTML = ICON_CLIP;
      attachmentToggle.addEventListener("click", () => attachmentInput.click());
      triggers.append(attachmentToggle, attachmentInput);
    }

    const counter = document.createElement("span");
    counter.className = "paragraph-comments__counter";
    counter.hidden = true;

    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "ui-btn ui-btn--primary ui-btn--sm paragraph-comments__submit";
    submit.textContent = submitLabel;

    toolbar.append(triggers, counter, submit);
    field.append(toolbar);

    const error = document.createElement("p");
    error.className = "paragraph-comments__error";
    error.setAttribute("role", "alert");
    error.hidden = true;

    // PR 148: the same minimal subset app/markdown_render.py actually renders.
    const hint = document.createElement("p");
    hint.className = "paragraph-comments__hint";
    hint.textContent = "**жирный**, *курсив*, ~~зачёркнутый~~, [ссылка](url), списки";
    const hintKeys = document.createElement("span");
    hintKeys.className = "paragraph-comments__hint-keys"; // hidden on touch screens
    hintKeys.textContent = " · Ctrl+Enter — отправить";
    hint.append(hintKeys);

    wrap.append(field, palette, error, hint);

    // Grows with its text up to a cap (app.css max-height), then scrolls.
    function autosize() {
      textarea.style.height = "auto";
      textarea.style.height = `${textarea.scrollHeight + 2}px`;
    }

    function updateSubmitState() {
      const empty = !textarea.value.trim() && !stagedAttachment;
      submit.setAttribute("aria-disabled", empty ? "true" : "false");
      const length = textarea.value.length;
      counter.hidden = length < COUNTER_FROM;
      counter.textContent = `${length} / ${MAX_COMMENT_LENGTH}`;
      counter.classList.toggle("paragraph-comments__counter--limit", length >= MAX_COMMENT_LENGTH);
    }

    function showError(message) {
      error.textContent = message;
      error.hidden = false;
    }

    let sending = false;
    async function send() {
      if (sending) return;
      const body = textarea.value.trim();
      if (!body && !stagedAttachment) {
        textarea.focus();
        return;
      }
      sending = true;
      error.hidden = true;
      submit.setAttribute("aria-busy", "true");
      submit.textContent = "Отправка…";
      textarea.readOnly = true;
      wrap.setAttribute("aria-busy", "true");
      let result;
      try {
        result = await onSubmit(body, stagedAttachment);
      } finally {
        sending = false;
        submit.removeAttribute("aria-busy");
        submit.textContent = submitLabel;
        textarea.readOnly = false;
        wrap.removeAttribute("aria-busy");
      }
      // A rejected or failed post keeps the text and the staged file - nothing typed or
      // picked is lost, same as a normal <form> whose submit failed.
      if (result.ok) {
        textarea.value = "";
        clearStagedAttachment();
        palette.hidden = true;
        emojiToggle.setAttribute("aria-expanded", "false");
        autosize();
        updateSubmitState();
      } else {
        showError(result.message || "Не удалось отправить. Проверьте соединение и попробуйте ещё раз.");
        if (wrap.isConnected) textarea.focus();
      }
    }

    submit.addEventListener("click", send);
    textarea.addEventListener("input", () => {
      autosize();
      updateSubmitState();
    });
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        send();
      } else if (event.key === "Escape" && onCancel) {
        event.preventDefault();
        event.stopPropagation();
        onCancel();
      }
    });

    updateSubmitState();

    // For the callers: focus with the caret at the end, and «Цитировать»'s quote insert.
    wrap.focusComposer = () => {
      textarea.focus({ preventScroll: true });
      const end = textarea.value.length;
      textarea.setSelectionRange(end, end);
      autosize();
    };
    wrap.insertText = (text) => {
      const current = textarea.value.trimEnd();
      textarea.value = current ? `${current}\n\n${text}\n\n` : `${text}\n\n`;
      autosize();
      updateSubmitState();
    };
    wrap.resetComposer = () => {
      textarea.value = initialValue;
      error.hidden = true;
      clearStagedAttachment();
      palette.hidden = true;
      emojiToggle.setAttribute("aria-expanded", "false");
      autosize();
    };
    wrap.textarea = textarea;
    return wrap;
  }

  // The server's own message for a rejected post (400 detail: too long, bad attachment,
  // ...), or null to fall back to the generic one.
  async function errorMessage(response) {
    try {
      const data = await response.json();
      return typeof data.detail === "string" ? data.detail : null;
    } catch {
      return null;
    }
  }

  // PR 155: like/dislike on a comment - a separate endpoint from the paragraph reactions
  // above. PR 313: the same .wn-reaction chip (the quiet variant - no outline until it's
  // yours), optimistic like the paragraph chips. Mutates the shared tree object so a later
  // re-render of the thread keeps it. Guests see the counts; a click sends them to /login.
  const THUMB_ICON =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z"/>' +
    '<path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';

  function buildCommentReactions(comment, onError) {
    const wrap = document.createElement("span");
    wrap.className = "paragraph-comment__reactions";
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", "Оценка комментария");
    let pending = false;

    function renderButtons() {
      const focusedValue = wrap.contains(document.activeElement)
        ? document.activeElement.dataset.value
        : null;
      wrap.replaceChildren();
      for (const [value, label] of [
        [1, "Нравится"],
        [-1, "Не нравится"],
      ]) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "wn-reaction wn-reaction--quiet paragraph-comment__reaction";
        btn.dataset.value = String(value);
        if (value === -1) btn.classList.add("paragraph-comment__reaction--down");
        const mine = comment.my_reaction === value;
        if (mine) btn.classList.add("wn-reaction--mine");
        if (pending && mine) btn.setAttribute("aria-busy", "true");
        btn.setAttribute("aria-pressed", mine ? "true" : "false");
        const count = comment.reactions?.[value === 1 ? "like" : "dislike"] || 0;
        btn.setAttribute("aria-label", `${label}: ${count}`);
        btn.title = label;
        const icon = document.createElement("span");
        icon.className = "wn-reaction__icon";
        icon.innerHTML = THUMB_ICON;
        const countEl = document.createElement("span");
        countEl.className = "wn-reaction__count";
        countEl.textContent = String(count);
        btn.append(icon, countEl);
        btn.addEventListener("click", () => vote(value));
        wrap.append(btn);
      }
      if (focusedValue) wrap.querySelector(`[data-value="${focusedValue}"]`)?.focus({ preventScroll: true });
    }

    async function vote(value) {
      if (!isAuthenticated) {
        window.location.href = "/login";
        return;
      }
      if (pending) return;
      pending = true;
      const previous = { counts: comment.reactions, mine: comment.my_reaction ?? null };
      const ok = await reactionState.optimistic({
        previous,
        guess: reactionState.toggleVote(previous.counts, previous.mine, value),
        render: (state, isPending) => {
          comment.reactions = state.counts;
          comment.my_reaction = state.mine;
          pending = isPending;
          renderButtons();
        },
        request: async () => {
          const response = await fetch(
            `/titles/${slugUrl}/chapters/${volume}/${number}/comments/${comment.id}/reactions`,
            {
              method: "POST",
              headers: { "Content-Type": "application/x-www-form-urlencoded" },
              body: new URLSearchParams({ value: String(value) }),
            }
          );
          return response.ok ? response.json() : null;
        },
      });
      pending = false;
      if (!ok) onError("Не удалось сохранить оценку. Попробуйте ещё раз.");
    }

    renderButtons();
    return wrap;
  }

  function countReplies(comment) {
    return comment.replies.reduce((n, reply) => n + 1 + countReplies(reply), 0);
  }

  function actionButton(className, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `paragraph-comment__action ${className}`;
    button.textContent = label;
    return button;
  }

  // One comment: avatar | author · time · (изменено), body, attachment, then the action
  // row (votes, Ответить, Изменить/Удалить on your own, fold the replies) and the reply
  // composer under it. Replies hang under a thin guide line, indented per level up to
  // MAX_INDENT_DEPTH (PR 165).
  function renderCommentNode(index, comment, depth = 0) {
    const el = document.createElement("article");
    el.className = "paragraph-comment";
    if (depth > 0) el.classList.add("paragraph-comment--reply");
    el.dataset.commentId = String(comment.id);
    el.tabIndex = -1;

    const side = document.createElement("div");
    side.className = "paragraph-comment__side";
    side.append(buildCommentAvatar(comment));
    el.append(side);

    const main = document.createElement("div");
    main.className = "paragraph-comment__main";

    const meta = document.createElement("div");
    meta.className = "paragraph-comment__meta";
    const author = document.createElement("a");
    author.className = "paragraph-comment__author";
    author.href = `/profile/${comment.user_id}`;
    author.textContent = comment.author;
    const time = document.createElement("time");
    time.className = "paragraph-comment__time";
    time.dateTime = comment.created_at;
    time.title = fullTime(comment.created_at);
    time.textContent = relativeTime(comment.created_at);
    meta.append(author, time);
    // PR 172: «изменено» once edit_comment() has overwritten the body - never for a
    // deleted comment.
    if (comment.updated_at && !comment.is_deleted) {
      const edited = document.createElement("span");
      edited.className = "paragraph-comment__edited";
      edited.textContent = "изменено";
      edited.title = fullTime(comment.updated_at);
      meta.append(edited);
    }
    main.append(meta);

    // PR 148: body_html is HTML sanitized server-side (app/markdown_render.py, nh3 on an
    // allow-list) - innerHTML is what turns the Markdown into formatting. PR 172: a deleted
    // comment shows a fixed placeholder instead.
    const body = document.createElement("div");
    body.className = "paragraph-comment__body";
    if (comment.is_deleted) {
      body.classList.add("paragraph-comment__body--deleted");
      body.textContent = "Комментарий удалён";
    } else {
      body.innerHTML = comment.body_html;
    }
    main.append(body);

    // PR 150/151: "gif" is an upload converted server-side into a silent looping mp4;
    // "image"/"video" are rendered plainly. delete_comment() clears attachment_url.
    if (comment.attachment_url && comment.attachment_kind === "gif") {
      const video = document.createElement("video");
      video.className = "paragraph-comment__attachment";
      video.src = comment.attachment_url;
      video.autoplay = true;
      video.loop = true;
      video.muted = true;
      video.playsInline = true;
      main.append(video);
    } else if (comment.attachment_url && comment.attachment_kind === "video") {
      const video = document.createElement("video");
      video.className = "paragraph-comment__attachment";
      video.src = comment.attachment_url;
      video.controls = true;
      main.append(video);
    } else if (comment.attachment_url && comment.attachment_kind === "image") {
      const img = document.createElement("img");
      img.className = "paragraph-comment__attachment";
      img.src = comment.attachment_url;
      img.alt = "Вложение к комментарию";
      main.append(img);
    }

    const actions = document.createElement("div");
    actions.className = "paragraph-comment__actions";
    if (!comment.is_deleted) {
      actions.append(
        buildCommentReactions(comment, (message) => {
          actionError.textContent = message;
          actionError.hidden = false;
        })
      );
    }

    const actionError = document.createElement("p");
    actionError.className = "paragraph-comments__error";
    actionError.setAttribute("role", "alert");
    actionError.hidden = true;

    let replyForm = null;
    if (isAuthenticated && !comment.is_deleted) {
      const replyToggle = actionButton("paragraph-comment__reply-toggle", "Ответить");
      replyToggle.setAttribute("aria-expanded", "false");
      const closeReply = () => {
        replyForm.hidden = true;
        replyForm.resetComposer();
        replyToggle.setAttribute("aria-expanded", "false");
        replyToggle.focus();
      };
      replyForm = buildComposer({
        onSubmit: (text, attachmentFile) =>
          submitComment(index, text, comment.id, attachmentFile, { focusNewest: true }),
        placeholder: "Ваш ответ…",
        context: `Ответ для ${comment.author}`,
        onCancel: closeReply,
        modifier: "inline",
      });
      replyForm.hidden = true;
      replyToggle.addEventListener("click", () => {
        if (!replyForm.hidden) {
          closeReply();
          return;
        }
        replyForm.hidden = false;
        replyToggle.setAttribute("aria-expanded", "true");
        replyForm.focusComposer();
      });
      actions.append(replyToggle);
    } else if (!isAuthenticated) {
      const link = document.createElement("a");
      link.className = "paragraph-comment__action paragraph-comment__reply-toggle";
      link.href = "/login";
      link.textContent = "Войти, чтобы ответить";
      actions.append(link);
    }

    // PR 172: «Изменить»/«Удалить» on the visitor's own comments only - a UI nicety, the
    // real ownership check is server-side (edit_comment()/delete_comment()).
    if (isAuthenticated && currentUserId === comment.user_id && !comment.is_deleted) {
      const editToggle = actionButton("paragraph-comment__edit-toggle", "Изменить");
      editToggle.setAttribute("aria-expanded", "false");
      // «Изменить» swaps the body itself for the composer in place.
      const closeEdit = () => {
        editForm.hidden = true;
        editForm.resetComposer();
        body.hidden = false;
        actions.hidden = false;
        editToggle.setAttribute("aria-expanded", "false");
        editToggle.focus();
      };
      const editForm = buildComposer({
        onSubmit: (text) => editComment(index, comment.id, text),
        placeholder: "Текст комментария…",
        initialValue: comment.body,
        allowAttachment: false,
        submitLabel: "Сохранить",
        context: "Редактирование",
        onCancel: closeEdit,
        modifier: "inline",
      });
      editForm.hidden = true;
      editToggle.addEventListener("click", () => {
        body.hidden = true;
        actions.hidden = true;
        editForm.hidden = false;
        editToggle.setAttribute("aria-expanded", "true");
        editForm.focusComposer();
      });
      body.after(editForm);

      const deleteToggle = actionButton(
        "paragraph-comment__delete-toggle paragraph-comment__action--danger",
        "Удалить"
      );
      deleteToggle.addEventListener("click", async () => {
        if (!window.confirm("Удалить комментарий?")) return;
        actionError.hidden = true;
        el.setAttribute("aria-busy", "true");
        const ok = await removeComment(index, comment.id);
        el.removeAttribute("aria-busy");
        if (!ok) {
          actionError.textContent = "Не удалось удалить комментарий. Попробуйте ещё раз.";
          actionError.hidden = false;
        }
      });
      actions.append(editToggle, deleteToggle);
    }

    // PR 152: folding a reply thread - the action-row button and a click on the thread's
    // guide line both flip the same state, kept across re-renders (collapsedCommentIds).
    let repliesDiv = null;
    let collapseToggle = null;
    const replyCount = countReplies(comment);

    function setRepliesCollapsed(collapsed) {
      if (collapsed) collapsedCommentIds.add(comment.id);
      else collapsedCommentIds.delete(comment.id);
      repliesDiv.hidden = collapsed;
      collapseToggle.textContent = collapsed
        ? `Показать ${replyCount} ${plural(replyCount, "ответ", "ответа", "ответов")}`
        : "Свернуть ответы";
      collapseToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }

    if (comment.replies.length > 0) {
      collapseToggle = actionButton("paragraph-comment__collapse-toggle", "");
      actions.append(collapseToggle);

      repliesDiv = document.createElement("div");
      repliesDiv.className = "paragraph-comment__replies";
      repliesDiv.id = `comment-replies-${comment.id}`;
      collapseToggle.setAttribute("aria-controls", repliesDiv.id);
      if (depth + 1 > MAX_INDENT_DEPTH) {
        repliesDiv.classList.add("paragraph-comment__replies--flat");
      }
      // PR 164: the guide line as its own element, so hovering one level's line never
      // lights up an ancestor's.
      const line = document.createElement("span");
      line.className = "paragraph-comment__replies-line";
      line.title = "Свернуть ответы";
      repliesDiv.append(line);
      for (const reply of comment.replies) {
        repliesDiv.append(renderCommentNode(index, reply, depth + 1));
      }
      line.addEventListener("click", () => setRepliesCollapsed(true));
      collapseToggle.addEventListener("click", () => setRepliesCollapsed(!repliesDiv.hidden));
      setRepliesCollapsed(collapsedCommentIds.has(comment.id));
    }

    main.append(actions, actionError);
    if (replyForm) main.append(replyForm);
    el.append(main);
    if (repliesDiv) el.append(repliesDiv);
    return el;
  }

  function paragraphQuote(index) {
    const el = paragraphContentElementFor(index);
    return el ? el.textContent.replace(/\s+/g, " ").trim() : "";
  }

  // Everything a paragraph's comments need, created once and found again afterwards:
  // the «N комментариев» toggle under the paragraph and the layer it opens.
  function commentsSectionFor(index) {
    const host = paragraphHostFor(index);
    if (!host) return null;
    let section = host.querySelector(":scope > .paragraph-comments");
    if (section) return section;

    section = document.createElement("div");
    section.className = "paragraph-comments";
    section.hidden = true; // syncSection() reveals it once there's something to show

    const layerId = `paragraph-comments-${index}`;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "paragraph-comments__toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", layerId);
    toggle.innerHTML = ICON_COMMENT;
    const toggleLabel = document.createElement("span");
    toggleLabel.className = "paragraph-comments__toggle-label";
    toggle.append(toggleLabel);
    toggle.addEventListener("click", () => {
      if (commentsExpandedByIndex.has(index)) closeThread(index);
      else openThread(index);
    });

    // PR 313: the paragraph's row - the comments pill, the reaction chips (renderStrip)
    // and a short-lived error note after a rolled-back reaction.
    const bar = document.createElement("div");
    bar.className = "paragraph-comments__bar";
    const strip = document.createElement("div");
    strip.className = "paragraph-reactions";
    strip.setAttribute("role", "group");
    strip.setAttribute("aria-label", "Реакции");
    const barError = document.createElement("span");
    barError.className = "paragraph-comments__bar-error";
    barError.setAttribute("role", "status");
    barError.hidden = true;
    bar.append(toggle, strip, barError);

    const layer = document.createElement("section");
    layer.className = "paragraph-comments__layer";
    layer.id = layerId;
    layer.hidden = true;
    layer.setAttribute("aria-label", `Комментарии к абзацу ${index + 1}`);

    // The paragraph this is about - the reader can lose sight of it once the thread
    // opens under it, and inside the bottom sheet it's not on screen at all.
    const head = document.createElement("header");
    head.className = "paragraph-comments__head";
    const contextBox = document.createElement("div");
    contextBox.className = "paragraph-comments__context";
    const kicker = document.createElement("span");
    kicker.className = "paragraph-comments__kicker";
    kicker.textContent = `Абзац ${index + 1}`;
    const quote = document.createElement("p");
    quote.className = "paragraph-comments__quote";
    quote.textContent = paragraphQuote(index);
    contextBox.append(kicker, quote);
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "paragraph-comments__icon-btn paragraph-comments__close";
    closeButton.setAttribute("aria-label", "Свернуть комментарии");
    closeButton.title = "Свернуть комментарии";
    closeButton.innerHTML = ICON_CLOSE;
    closeButton.addEventListener("click", () => {
      closeThread(index);
      toggle.focus();
    });
    head.append(contextBox, closeButton);

    const status = document.createElement("div");
    status.className = "paragraph-comments__status";
    status.setAttribute("aria-live", "polite");

    const list = document.createElement("div");
    list.className = "paragraph-comments__list";

    const footer = document.createElement("div");
    footer.className = "paragraph-comments__footer";
    if (isAuthenticated) {
      const composer = buildComposer({
        onSubmit: (text, attachmentFile) => submitComment(index, text, null, attachmentFile),
        placeholder: "Написать комментарий…",
      });
      composer.dataset.role = "paragraph-comment-composer";
      footer.append(composer);
    } else {
      const login = document.createElement("p");
      login.className = "paragraph-comments__login";
      login.innerHTML = '<a href="/login">Войдите</a>, чтобы комментировать и отвечать.';
      footer.append(login);
    }

    layer.append(head, status, list, footer);
    section.append(bar, layer);
    host.append(section);
    return section;
  }

  function partsFor(index) {
    const section = commentsSectionFor(index);
    if (!section) return null;
    return {
      section,
      toggle: section.querySelector(".paragraph-comments__toggle"),
      label: section.querySelector(".paragraph-comments__toggle-label"),
      strip: section.querySelector(".paragraph-reactions"),
      error: section.querySelector(".paragraph-comments__bar-error"),
      layer: document.getElementById(`paragraph-comments-${index}`),
    };
  }

  function layerPart(index, selector) {
    return document.getElementById(`paragraph-comments-${index}`)?.querySelector(selector);
  }

  // The row shows whenever there are comments or reactions, or the layer is open. The
  // pill's label is the count - «Комментарии» for an open layer nobody has commented in
  // yet, «Обсудить» next to reactions on a paragraph without comments.
  function syncSection(index) {
    const parts = partsFor(index);
    if (!parts) return;
    const count = commentCountByIndex.get(index) ?? 0;
    const expanded = commentsExpandedByIndex.has(index);
    parts.section.hidden = count <= 0 && !expanded && !hasReactions(index);
    parts.label.textContent =
      count > 0 ? `${count} ${pluralizeComments(count)}` : expanded ? "Комментарии" : "Обсудить";
    parts.toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    parts.section.classList.toggle("paragraph-comments--open", expanded);
  }

  function setCommentCount(index, count) {
    commentCountByIndex.set(index, count);
    syncSection(index);
    renderStatus(index);
  }

  const SKELETON =
    '<div class="paragraph-comment paragraph-comment--skeleton" aria-hidden="true">' +
    '<div class="paragraph-comment__side"><span class="paragraph-comment__avatar"></span></div>' +
    '<div class="paragraph-comment__main"><span class="paragraph-comments__bone"></span>' +
    '<span class="paragraph-comments__bone paragraph-comments__bone--long"></span></div></div>';

  // The line between the head and the list: skeletons while the thread loads, an error
  // with «Повторить», or the empty state - nothing once there are comments to show.
  function renderStatus(index) {
    const status = layerPart(index, ".paragraph-comments__status");
    const list = layerPart(index, ".paragraph-comments__list");
    if (!status) return;
    status.replaceChildren();
    list.removeAttribute("aria-busy");
    if (loadingIndexes.has(index)) {
      list.setAttribute("aria-busy", "true");
      status.innerHTML = SKELETON + SKELETON + '<span class="paragraph-comments__sr">Загружаем комментарии…</span>';
    } else if (loadFailedIndexes.has(index)) {
      const box = document.createElement("div");
      box.className = "paragraph-comments__load-error";
      const text = document.createElement("span");
      text.textContent = "Не удалось загрузить комментарии";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ui-btn ui-btn--sm paragraph-comments__retry";
      retry.textContent = "Повторить";
      retry.addEventListener("click", () => loadCommentTree(index));
      box.append(text, retry);
      status.append(box);
    } else if ((commentCountByIndex.get(index) ?? 0) === 0) {
      const empty = document.createElement("p");
      empty.className = "paragraph-comments__empty";
      empty.textContent = isAuthenticated
        ? "Здесь пока пусто — начните обсуждение этого абзаца."
        : "У этого абзаца пока нет комментариев.";
      status.append(empty);
    }
  }

  function renderCommentList(index, comments) {
    const list = layerPart(index, ".paragraph-comments__list");
    if (!list) return;
    list.replaceChildren(...comments.map((comment) => renderCommentNode(index, comment)));
  }

  async function loadCommentTree(index) {
    loadingIndexes.add(index);
    loadFailedIndexes.delete(index);
    renderStatus(index);
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments` +
          `?paragraph_index=${index}&branch_id=${encodeURIComponent(branchId)}`
      );
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      commentTreeByIndex.set(index, data.comments);
      renderCommentList(index, data.comments);
    } catch {
      loadFailedIndexes.add(index);
    } finally {
      loadingIndexes.delete(index);
      renderStatus(index);
    }
  }

  // Opens the layer - inline on desktop, in the bottom sheet on phones. `compose` puts
  // the caret into the new-comment box (menu «Комментировать»), `quote` (PR 156,
  // «Цитировать») drops the paragraph's text into it as a Markdown quote first.
  function openThread(index, { compose = false, quote = "" } = {}) {
    const parts = partsFor(index);
    if (!parts) return;
    commentsExpandedByIndex.add(index);
    syncSection(index);
    const loaded = commentTreeByIndex.has(index);
    if (!loaded && (commentCountByIndex.get(index) ?? 0) > 0) loadCommentTree(index);
    else renderStatus(index);

    const composer = parts.layer.querySelector('[data-role="paragraph-comment-composer"]');
    if (quote && composer) composer.insertText(quote);

    if (phoneQuery.matches && window.bottomSheet) {
      if (compose && composer) composer.textarea.dataset.autofocus = "";
      window.bottomSheet.open({
        title: "Комментарии к абзацу",
        content: parts.layer,
        opener: parts.toggle,
        onClose: () => {
          if (composer) delete composer.textarea.dataset.autofocus;
          commentsExpandedByIndex.delete(index);
          syncSection(index);
        },
      });
      return;
    }

    parts.layer.hidden = false;
    if (compose && composer) {
      composer.focusComposer();
      composer.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else {
      parts.layer.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function closeThread(index) {
    const parts = partsFor(index);
    if (!parts) return;
    if (window.bottomSheet?.isOpen() && parts.layer.closest('[data-role="bottom-sheet"]')) {
      window.bottomSheet.close(); // its onClose (openThread) updates the state
      return;
    }
    commentsExpandedByIndex.delete(index);
    parts.layer.hidden = true;
    syncSection(index);
  }

  function newestCommentId(comments) {
    let max = 0;
    for (const comment of comments) {
      max = Math.max(max, comment.id, newestCommentId(comment.replies));
    }
    return max;
  }

  // Shared tail of submitComment/editComment/removeComment - all three endpoints return
  // the same {count, comments} shape (app/api/chapters.py's _comments_response()). A
  // reply/edit re-renders the composer it came from away, so focus moves to the comment
  // it produced (marked briefly, --fresh) instead of falling back to <body>.
  function applyCommentTreeResponse(index, data, focusCommentId = null) {
    commentTreeByIndex.set(index, data.comments);
    loadFailedIndexes.delete(index);
    renderCommentList(index, data.comments);
    setCommentCount(index, data.count);
    if (focusCommentId == null) return;
    const node = layerPart(index, `[data-comment-id="${focusCommentId}"]`);
    if (!node) return;
    node.classList.add("paragraph-comment--fresh");
    node.focus({ preventScroll: true });
    node.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // PR 150: always FormData - the endpoint accepts multipart unconditionally (it has to,
  // for the attachment case). No Content-Type header - the browser adds the boundary.
  async function submitComment(index, body, parentCommentId, attachmentFile, { focusNewest = false } = {}) {
    const formData = new FormData();
    formData.set("paragraph_index", String(index));
    formData.set("body", body);
    formData.set("branch_id", branchId);
    if (parentCommentId != null) formData.set("parent_comment_id", String(parentCommentId));
    if (attachmentFile) formData.set("attachment", attachmentFile);
    let data;
    try {
      const response = await fetch(`/titles/${slugUrl}/chapters/${volume}/${number}/comments`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) return { ok: false, message: await errorMessage(response) };
      data = await response.json();
    } catch {
      return { ok: false, message: null };
    }
    applyCommentTreeResponse(index, data, focusNewest ? newestCommentId(data.comments) : null);
    return { ok: true };
  }

  // PR 172: «Изменить» - same {ok, message} contract as submitComment.
  async function editComment(index, commentId, body) {
    const formData = new FormData();
    formData.set("body", body);
    let data;
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments/${commentId}`,
        { method: "PATCH", body: formData }
      );
      if (!response.ok) return { ok: false, message: await errorMessage(response) };
      data = await response.json();
    } catch {
      return { ok: false, message: null };
    }
    applyCommentTreeResponse(index, data, commentId);
    return { ok: true };
  }

  // PR 172: «Удалить» - the confirm() lives at the call site. The comment stays in the
  // tree as «Комментарий удалён» when it has replies, so focus goes back to it.
  async function removeComment(index, commentId) {
    let data;
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments/${commentId}`,
        { method: "DELETE" }
      );
      if (!response.ok) return false;
      data = await response.json();
    } catch {
      return false;
    }
    applyCommentTreeResponse(index, data, commentId);
    if (!layerPart(index, `[data-comment-id="${commentId}"]`)) {
      layerPart(index, '[data-role="paragraph-comment-composer"] textarea')?.focus({ preventScroll: true });
    }
    return true;
  }

  // One bulk fetch for the whole chapter on load, not one per paragraph - same reasoning
  // as loadInitialReactions.
  async function loadInitialCommentCounts() {
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments/counts` +
          `?branch_id=${encodeURIComponent(branchId)}`
      );
      if (!response.ok) return;
      const data = await response.json();
      for (const [indexStr, count] of Object.entries(data.counts || {})) {
        setCommentCount(Number(indexStr), count);
      }
    } catch {
      // Same "fails silently" reasoning as loadInitialReactions.
    }
  }
  loadInitialCommentCounts();

  const panel = document.createElement("div");
  panel.className = "paragraph-menu__panel";
  panel.setAttribute("role", "menu");
  document.body.appendChild(panel);

  let lastX = 0;
  let lastY = 0;

  function isOpen() {
    return panel.classList.contains("paragraph-menu__panel--open");
  }

  function addLoginItem(label) {
    // Same principle as _locked_feature.html elsewhere in the app: an anonymous visitor
    // still sees the menu, but its actions route to /login instead of running (there is
    // nothing to react/comment as without an account).
    const item = document.createElement("a");
    item.className = "paragraph-menu__item";
    item.setAttribute("role", "menuitem");
    item.href = "/login";
    item.textContent = label;
    panel.append(item);
  }

  function renderReactionPicker(index, { standalone = false } = {}) {
    panel.replaceChildren();

    if (!standalone) {
      const back = document.createElement("button");
      back.type = "button";
      back.className = "paragraph-menu__back";
      back.textContent = "← Назад";
      back.addEventListener("click", (event) => {
        event.stopPropagation();
        renderMenuItems(index);
        position(lastX, lastY);
      });
      panel.append(back);
    }

    const picker = document.createElement("div");
    picker.className = "paragraph-menu__emoji-picker";
    picker.setAttribute("role", "menu");
    const mineEmoji = reactionsByIndex.get(index)?.mine ?? null;
    for (const [emoji, label] of EMOJI) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "paragraph-menu__emoji";
      button.setAttribute("role", "menuitemradio");
      button.setAttribute("aria-checked", emoji === mineEmoji ? "true" : "false");
      button.setAttribute("aria-label", label);
      button.title = label;
      button.textContent = emoji;
      button.addEventListener("click", () => pickReaction(index, emoji));
      picker.append(button);
    }
    panel.append(picker);
  }

  function renderMenuItems(index) {
    panel.replaceChildren();
    if (!isAuthenticated) {
      addLoginItem("Реакции");
      addLoginItem("Комментировать");
      addLoginItem("Цитировать");
      return;
    }

    const reactItem = document.createElement("button");
    reactItem.type = "button";
    reactItem.className = "paragraph-menu__item";
    reactItem.setAttribute("role", "menuitem");
    reactItem.textContent = "Реакции";
    reactItem.addEventListener("click", (event) => {
      event.stopPropagation();
      renderReactionPicker(index);
      position(lastX, lastY);
    });
    panel.append(reactItem);

    const commentItem = document.createElement("button");
    commentItem.type = "button";
    commentItem.className = "paragraph-menu__item";
    commentItem.setAttribute("role", "menuitem");
    commentItem.textContent = "Комментировать";
    // PR 312: opens the paragraph's comment layer with the caret in its composer,
    // instead of a composer squeezed into this menu.
    commentItem.addEventListener("click", (event) => {
      event.stopPropagation();
      close();
      openThread(index, { compose: true });
    });
    panel.append(commentItem);

    // PR 156: opens the same composer as "Комментировать", pre-filled with the chapter
    // paragraph's own text quoted (`> `-prefixed) - a starting point for a comment about
    // this specific paragraph, not a separate posting flow of its own.
    const quoteItem = document.createElement("button");
    quoteItem.type = "button";
    quoteItem.className = "paragraph-menu__item";
    quoteItem.setAttribute("role", "menuitem");
    quoteItem.textContent = "Цитировать";
    quoteItem.addEventListener("click", (event) => {
      event.stopPropagation();
      close();
      openThread(index, { compose: true, quote: quoteParagraphText(index) });
    });
    panel.append(quoteItem);
  }

  function position(x, y) {
    panel.style.left = "0px";
    panel.style.top = "0px";
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const left = Math.min(x, window.innerWidth - width - GAP);
    const top = Math.min(y, window.innerHeight - height - GAP);
    panel.style.left = `${Math.max(GAP, left)}px`;
    panel.style.top = `${Math.max(GAP, top)}px`;
  }

  // PR 313: `picker` opens straight onto the emoji grid (the row's «+»); `opener` gets
  // focus back when it closes.
  let menuOpener = null;
  function open(x, y, index, { picker = false, opener = null } = {}) {
    lastX = x;
    lastY = y;
    menuOpener = opener;
    if (picker) renderReactionPicker(index, { standalone: true });
    else renderMenuItems(index);
    panel.classList.add("paragraph-menu__panel--open");
    position(x, y);
    if (picker) {
      (panel.querySelector('[aria-checked="true"]') || panel.querySelector(".paragraph-menu__emoji"))?.focus({
        preventScroll: true,
      });
    }
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
  }

  function close(refocus = false) {
    panel.classList.remove("paragraph-menu__panel--open");
    window.removeEventListener("scroll", close, true);
    window.removeEventListener("resize", close);
    if (refocus === true && menuOpener?.isConnected) menuOpener.focus({ preventScroll: true });
    menuOpener = null;
  }

  content.addEventListener("contextmenu", (event) => {
    const paragraphEl = paragraphElementFor(event.target);
    if (!paragraphEl) return;
    event.preventDefault();
    const index = Array.prototype.indexOf.call(content.children, paragraphEl);
    open(event.clientX, event.clientY, index);
  });

  document.addEventListener("click", (event) => {
    if (isOpen() && !panel.contains(event.target)) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close(true);
  });
})();
