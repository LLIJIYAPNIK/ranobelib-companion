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

  function renderStrip(index, counts, mineEmoji) {
    const host = paragraphHostFor(index);
    if (!host) return;
    let strip = host.querySelector(":scope > .paragraph-reactions");
    const entries = Object.entries(counts || {}).filter(([, n]) => n > 0);
    if (entries.length === 0) {
      strip?.remove();
      return;
    }
    if (!strip) {
      strip = document.createElement("div");
      strip.className = "paragraph-reactions";
      host.append(strip);
    }
    strip.replaceChildren();
    for (const [emoji, n] of entries) {
      const pill = document.createElement("span");
      pill.className = "paragraph-reactions__pill";
      if (emoji === mineEmoji) pill.classList.add("paragraph-reactions__pill--mine");
      pill.textContent = `${emoji} ${n}`;
      strip.append(pill);
    }
  }

  // The visitor's own current pick per paragraph, kept in memory from the initial bulk
  // fetch and updated after every toggle - the picker (renderReactionPicker below) reads
  // this synchronously to highlight the active emoji instead of firing a request every
  // time the menu opens.
  const mineByIndex = new Map();

  // One bulk fetch for the whole chapter on load, not one per paragraph - a chapter page
  // can have dozens, and this is the same "counts under an already-revealed paragraph
  // shouldn't need a request of its own" reasoning as the endpoint itself (see
  // app/db/reactions.py's count_reactions()).
  async function loadInitialReactions() {
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/reactions?branch_id=${encodeURIComponent(branchId)}`
      );
      if (!response.ok) return;
      const data = await response.json();
      for (const [indexStr, emoji] of Object.entries(data.mine || {})) {
        mineByIndex.set(Number(indexStr), emoji);
      }
      for (const [indexStr, counts] of Object.entries(data.counts || {})) {
        const index = Number(indexStr);
        renderStrip(index, counts, mineByIndex.get(index) ?? null);
      }
    } catch {
      // No network, or the server errored - the chapter itself still reads fine without
      // reaction counts, so this fails silently rather than surfacing an error banner
      // over content that has nothing to do with reactions.
    }
  }
  loadInitialReactions();

  async function pickReaction(index, emoji) {
    const body = new URLSearchParams({
      paragraph_index: String(index),
      emoji,
      branch_id: branchId,
    });
    let data;
    try {
      const response = await fetch(`/titles/${slugUrl}/chapters/${volume}/${number}/reactions`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });
      if (!response.ok) return;
      data = await response.json();
    } catch {
      return;
    }
    if (data.mine) {
      mineByIndex.set(index, data.mine);
    } else {
      mineByIndex.delete(index);
    }
    renderStrip(index, data.counts, data.mine);
    close();
  }

  // --- PR 133: comments -----------------------------------------------------------

  function pluralizeComments(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return "комментарий";
    if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) return "комментария";
    return "комментариев";
  }

  function formatCommentTime(iso) {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // PR 147: the same picture-or-initials pairing base.html's Jinja templates render via
  // avatar_url(user)/avatar_initials(user) - comment.avatar_url/avatar_initials arrive
  // already computed by the same server-side helpers (app/auth/avatar.py), so this just
  // picks which one to show, same as {% if avatar_url(user) %} does there.
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
  // - commentCountByIndex is the single source of truth for the number shown on the
  //   toggle ("N комментариев") - set from the initial bulk fetch and refreshed after
  //   every post, never recomputed from the tree below (whose root-level length isn't
  //   the same "replies included" count the server reports).
  // - commentTreeByIndex is the full nested tree, populated lazily the first time that
  //   paragraph's "▾" is clicked (loadCommentTree below) and reused on every later
  //   toggle, so reopening an already-loaded thread needs no request.
  // - commentsExpandedByIndex tracks only whether the list is currently shown, for the
  //   toggle's own arrow direction.
  const commentCountByIndex = new Map();
  const commentTreeByIndex = new Map();
  const commentsExpandedByIndex = new Set();

  // PR 149: one shared floating emoji picker for every composer's own free-form
  // insertion into its textarea - not the fixed 10-emoji EMOJI reaction picker above,
  // which reacts to a whole paragraph rather than typing into anything. Same "one
  // portaled node, position: fixed, reused by however many composers/reply forms exist
  // on the page" approach as `panel` below, just anchored to the trigger button's own
  // rect (getBoundingClientRect()) instead of a right-click point, and reusing that
  // panel's exact CSS classes (.paragraph-menu__panel/__emoji-picker/__emoji) rather than
  // inventing a second visual language for what's already the same kind of floating menu.
  const COMMENT_EMOJI = [
    "😀", "😁", "😂", "🤣", "😊", "😉", "😍", "😘", "😜", "🤔",
    "😐", "😴", "😭", "😢", "😡", "🥳", "😱", "🤯", "🥰", "😎",
    "👍", "👎", "👏", "🙏", "💪", "🤝", "👋", "✌️", "🤞", "👌",
    "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "💔", "💯", "🔥",
    "🎉", "✨", "⭐", "☀️", "🌙", "☕", "🍕", "🎮",
  ];

  const emojiPicker = document.createElement("div");
  emojiPicker.className = "paragraph-menu__panel";
  emojiPicker.setAttribute("role", "menu");
  const emojiGrid = document.createElement("div");
  emojiGrid.className = "paragraph-menu__emoji-picker comment-emoji-picker__grid";
  for (const emoji of COMMENT_EMOJI) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "paragraph-menu__emoji";
    button.setAttribute("role", "menuitem");
    button.textContent = emoji;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      insertAtCursor(emojiPickerTarget, emoji);
      closeEmojiPicker();
    });
    emojiGrid.append(button);
  }
  emojiPicker.append(emojiGrid);
  document.body.append(emojiPicker);

  let emojiPickerTarget = null;

  function isEmojiPickerOpen() {
    return emojiPicker.classList.contains("paragraph-menu__panel--open");
  }

  function positionEmojiPicker(anchor) {
    const rect = anchor.getBoundingClientRect();
    emojiPicker.style.left = "0px";
    emojiPicker.style.top = "0px";
    const width = emojiPicker.offsetWidth;
    const height = emojiPicker.offsetHeight;
    const left = Math.min(rect.left, window.innerWidth - width - GAP);
    const top = Math.min(rect.bottom + 4, window.innerHeight - height - GAP);
    emojiPicker.style.left = `${Math.max(GAP, left)}px`;
    emojiPicker.style.top = `${Math.max(GAP, top)}px`;
  }

  function openEmojiPicker(anchor, textarea) {
    emojiPickerTarget = textarea;
    emojiPicker.classList.add("paragraph-menu__panel--open");
    positionEmojiPicker(anchor);
  }

  function closeEmojiPicker() {
    emojiPicker.classList.remove("paragraph-menu__panel--open");
    emojiPickerTarget = null;
  }

  // Inserts at the caret (replacing any current selection) rather than always appending
  // to the end, so picking an emoji mid-sentence lands where the visitor was actually
  // typing.
  function insertAtCursor(textarea, text) {
    if (!textarea) return;
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? textarea.value.length;
    textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
    const caret = start + text.length;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
  }

  document.addEventListener("click", (event) => {
    if (isEmojiPickerOpen() && !emojiPicker.contains(event.target)) closeEmojiPicker();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isEmojiPickerOpen()) closeEmojiPicker();
  });

  // A reusable textarea + emoji-picker/attachment triggers + "Отправить" button, shared
  // by the menu's "Комментировать" composer and every comment's own "Ответить" reply
  // form - the only difference between them is what `onSubmit` does with the typed body
  // (and, PR 150/151, the staged attachment file, if any).
  // PR 156: `initialValue` pre-fills the textarea at creation time - used for the
  // context menu's "Цитировать" (renderCommentComposer below builds a fresh composer per
  // open, so a constructor argument is all that's needed there).
  // PR 172: `allowAttachment: false` (used for "Изменить") hides the attachment picker -
  // editComment() only ever overwrites `body`, so a staged file there would silently be
  // discarded rather than actually changing the comment's attachment.
  function buildComposer(onSubmit, placeholder, initialValue = "", { allowAttachment = true } = {}) {
    const wrap = document.createElement("div");
    wrap.className = "paragraph-comments__composer";
    const textarea = document.createElement("textarea");
    textarea.className = "paragraph-comments__textarea";
    textarea.placeholder = placeholder;
    textarea.rows = 3;
    textarea.maxLength = 2000; // mirrors MAX_COMMENT_LENGTH in app/db/comments.py
    textarea.value = initialValue;
    const emojiToggle = document.createElement("button");
    emojiToggle.type = "button";
    emojiToggle.className = "paragraph-comments__emoji-toggle";
    emojiToggle.setAttribute("aria-label", "Вставить эмодзи");
    emojiToggle.title = "Вставить эмодзи";
    emojiToggle.textContent = "🙂";
    emojiToggle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (isEmojiPickerOpen() && emojiPickerTarget === textarea) {
        closeEmojiPicker();
      } else {
        openEmojiPicker(emojiToggle, textarea);
      }
    });

    // PR 148: the same minimal subset app/markdown_render.py actually renders - not a
    // full Markdown cheatsheet, so it doesn't promise syntax (headings, code blocks,
    // images) this feature silently drops.
    const hint = document.createElement("p");
    hint.className = "paragraph-comments__hint";
    hint.textContent = "Поддерживается: **жирный**, *курсив*, ~~зачёркнутый~~, [ссылка](url), списки";

    // PR 150/151: staged client-side until the visitor actually hits "Отправить" - the
    // file itself is what travels to the server (as multipart, submitComment below), this
    // composer never does its own upload request. null when nothing's staged, the steady
    // state for the overwhelming majority of comments. One button for image/video/GIF
    // alike (not a separate button per file type) - app/comment_attachment.py sniffs the
    // actual bytes server-side to decide what to do with it.
    let stagedAttachment = null;
    let attachmentInput = null;
    let attachmentChip = null;
    let attachmentPreviewUrl = null;

    function clearStagedAttachment() {
      stagedAttachment = null;
      if (attachmentInput) attachmentInput.value = "";
      attachmentChip?.remove();
      attachmentChip = null;
      // Revoked only after the <img> using it is gone - revoking first would leave that
      // preview broken for the brief moment before removal takes effect.
      if (attachmentPreviewUrl) URL.revokeObjectURL(attachmentPreviewUrl);
      attachmentPreviewUrl = null;
    }

    function stageAttachment(file) {
      stagedAttachment = file;
      attachmentChip?.remove();
      if (attachmentPreviewUrl) URL.revokeObjectURL(attachmentPreviewUrl);
      attachmentPreviewUrl = null;

      attachmentChip = document.createElement("div");
      attachmentChip.className = "paragraph-comments__attachment-chip";

      // PR 151: a real thumbnail for an image (GIF included - it animates in the
      // preview just like it will once posted), a filename is preview enough for a
      // video (a moving preview client-side isn't worth the complexity here).
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
      remove.className = "paragraph-comments__attachment-chip-remove";
      remove.setAttribute("aria-label", "Убрать вложение");
      remove.title = "Убрать вложение";
      remove.textContent = "×";
      remove.addEventListener("click", clearStagedAttachment);
      attachmentChip.append(name, remove);
      wrap.insertBefore(attachmentChip, hint);
    }

    let attachmentToggle = null;
    if (allowAttachment) {
      attachmentInput = document.createElement("input");
      attachmentInput.type = "file";
      attachmentInput.accept = "image/*,video/*";
      attachmentInput.hidden = true;
      attachmentInput.addEventListener("change", () => {
        const file = attachmentInput.files?.[0];
        if (file) stageAttachment(file);
      });

      attachmentToggle = document.createElement("button");
      attachmentToggle.type = "button";
      attachmentToggle.className = "paragraph-comments__attachment-toggle";
      attachmentToggle.setAttribute("aria-label", "Прикрепить файл");
      attachmentToggle.title = "Прикрепить изображение, GIF или видео";
      attachmentToggle.textContent = "📎";
      attachmentToggle.addEventListener("click", (event) => {
        event.stopPropagation();
        attachmentInput.click();
      });
    }

    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "btn btn--sm";
    submit.textContent = "Отправить";
    submit.addEventListener("click", async () => {
      const body = textarea.value.trim();
      if (!body && !stagedAttachment) return;
      submit.disabled = true;
      try {
        // Only clears the box on a confirmed success - a rejected/network-failed post
        // (onSubmit returning false) leaves the typed text (and any staged attachment)
        // in place so neither is lost, same reasoning a normal <form> submit failure
        // wouldn't wipe the field either.
        if (await onSubmit(body, stagedAttachment)) {
          textarea.value = "";
          clearStagedAttachment();
        }
      } finally {
        submit.disabled = false;
      }
    });

    const toolbar = document.createElement("div");
    toolbar.className = "paragraph-comments__toolbar";
    const triggers = document.createElement("div");
    triggers.className = "paragraph-comments__triggers";
    triggers.append(emojiToggle);
    if (allowAttachment) triggers.append(attachmentToggle, attachmentInput);
    toolbar.append(triggers, submit);

    wrap.append(textarea, hint, toolbar);
    return wrap;
  }

  // PR 155: like/dislike on a comment itself - a separate feature and endpoint from
  // pickReaction() above (which reacts to a whole paragraph via a 10-emoji palette). Same
  // toggle-by-clicking-again/switch-by-clicking-the-other semantics, server-enforced
  // (app/db/comment_reactions.py), mirrored here just to update the UI immediately without
  // waiting on a second round-trip.
  //
  // `comment` is the exact object instance stored in commentTreeByIndex (renderCommentList
  // passes tree entries straight through, never a copy), so mutating comment.reactions/
  // comment.my_reaction here keeps that shared state in sync the same way pickReaction
  // keeps mineByIndex in sync - a later unrelated re-render of this same tree (e.g. after
  // posting a new reply) won't revert this comment's reaction display.
  async function pickCommentReaction(comment, value, onUpdate) {
    const body = new URLSearchParams({ value: String(value) });
    let data;
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments/${comment.id}/reactions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body,
        }
      );
      if (!response.ok) return;
      data = await response.json();
    } catch {
      return;
    }
    comment.reactions = data.counts;
    comment.my_reaction = data.mine;
    onUpdate();
  }

  // PR 162: outline thumb icon (Feather-style, same viewBox/stroke convention as the
  // rest of the app's inline SVGs - see toc-tap-progress.js/base.html) - one shared path
  // for both buttons, the dislike button just flips it vertically via CSS
  // (.paragraph-comment__reaction--down) rather than carrying a second, mirrored path.
  const THUMB_ICON =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z"/>' +
    '<path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';

  // Unauthenticated visitors still see the counts (reading who reacted what needs no
  // account, same as the paragraph reactions strip) but clicking sends them to /login
  // instead of posting - the same "action needs a session, viewing doesn't" split as the
  // reply toggle just below, just without swapping the whole control for an <a> for it.
  function buildCommentReactions(comment) {
    const wrap = document.createElement("span");
    wrap.className = "paragraph-comment__reactions";

    function renderButtons() {
      wrap.replaceChildren();
      for (const [value, modifier, label] of [
        [1, null, "Нравится"],
        [-1, "paragraph-comment__reaction--down", "Не нравится"],
      ]) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "paragraph-comment__reaction";
        if (modifier) btn.classList.add(modifier);
        if (comment.my_reaction === value) {
          btn.classList.add("paragraph-comment__reaction--mine");
        }
        btn.setAttribute("aria-pressed", comment.my_reaction === value ? "true" : "false");
        btn.setAttribute("aria-label", label);
        const icon = document.createElement("span");
        icon.className = "paragraph-comment__reaction-icon";
        icon.innerHTML = THUMB_ICON;
        const count = (comment.reactions?.[value === 1 ? "like" : "dislike"]) || 0;
        const countEl = document.createElement("span");
        countEl.className = "paragraph-comment__reaction-count";
        countEl.textContent = String(count);
        btn.append(icon, countEl);
        btn.addEventListener("click", () => {
          if (!isAuthenticated) {
            window.location.href = "/login";
            return;
          }
          pickCommentReaction(comment, value, renderButtons);
        });
        wrap.append(btn);
      }
    }
    renderButtons();
    return wrap;
  }

  // PR 162: .paragraph-comment__side (avatar + vote buttons) vs .paragraph-comment__main
  // (everything else) - the split that will become the left/right columns once the next
  // commit turns .paragraph-comment into an actual grid. For now these are just two
  // stacked blocks; the visual "column" doesn't exist yet.
  function renderCommentNode(index, comment, depth = 0) {
    const el = document.createElement("div");
    el.className = "paragraph-comment";

    const side = document.createElement("div");
    side.className = "paragraph-comment__side";
    side.append(buildCommentAvatar(comment), buildCommentReactions(comment));
    el.append(side);

    const main = document.createElement("div");
    main.className = "paragraph-comment__main";

    const meta = document.createElement("div");
    meta.className = "paragraph-comment__meta";
    const author = document.createElement("a");
    author.className = "paragraph-comment__author";
    author.href = `/profile/${comment.user_id}`;
    author.textContent = comment.author;
    const time = document.createElement("span");
    time.className = "paragraph-comment__time";
    time.textContent = formatCommentTime(comment.created_at);
    meta.append(author, time);
    // PR 172: "(изменено)" next to the timestamp once edit_comment() has overwritten the
    // body - never shown for a deleted comment (delete_comment() also sets updated_at, but
    // there's no edit to call out once the whole body is gone), same "edited" convention as
    // most forums/social apps.
    if (comment.updated_at && !comment.is_deleted) {
      const edited = document.createElement("span");
      edited.className = "paragraph-comment__edited";
      edited.textContent = "(изменено)";
      meta.append(edited);
    }
    main.append(meta);

    // PR 148: comment.body_html is already-sanitized HTML from the same server-side
    // renderer (app/markdown_render.py) for every comment, regardless of source - setting
    // it via innerHTML rather than textContent is what actually turns Markdown into
    // formatting; safe here specifically because nh3.clean() ran server-side on an
    // allow-list, not because this is "just our own data". A <div>, not a <p>, since the
    // rendered HTML brings its own block-level structure (paragraphs, <br>, lists) -
    // nesting that inside a <p> would be invalid.
    const body = document.createElement("div");
    body.className = "paragraph-comment__body";
    body.innerHTML = comment.body_html;
    main.append(body);

    // PR 150/151: the one attachment a comment can carry - "gif" is a plain upload
    // converted server-side (app/gif_video.py) into a silent looping mp4, rendered the
    // same way as one so it behaves like a video (autoplay/loop/muted/no controls)
    // instead of like the picture it visually resembles. "image"/"video" are stored
    // as-is (app/comment_attachment.py) and rendered plainly - <img>, or <video controls>
    // since an intentional video upload isn't meant to be a silent background loop.
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
      img.alt = "";
      main.append(img);
    }

    // PR 172: "Изменить"/"Удалить" - only on the visitor's own, non-deleted comments.
    // Hiding these client-side is purely a UI nicety: the real check is server-side
    // (edit_comment()/delete_comment() scope their UPDATE by user_id), so this can't be
    // bypassed into actually editing/deleting someone else's comment even if someone
    // forced these buttons to render.
    if (isAuthenticated && currentUserId === comment.user_id && !comment.is_deleted) {
      const editToggle = document.createElement("button");
      editToggle.type = "button";
      editToggle.className = "paragraph-comment__edit-toggle";
      editToggle.textContent = "Изменить";
      const editForm = buildComposer(
        (text) => editComment(index, comment.id, text),
        "Текст комментария…",
        comment.body,
        { allowAttachment: false }
      );
      editForm.hidden = true;
      // "Изменить" swaps .paragraph-comment__body itself for the composer in place (not
      // shown alongside it, unlike "Ответить"'s reply form below the original text) -
      // matches the roadmap's "turns .paragraph-comment__body back into a composer".
      // Clicking again while editing cancels back to the plain body without saving.
      editToggle.addEventListener("click", () => {
        const entering = editForm.hidden;
        body.hidden = entering;
        editForm.hidden = !entering;
        editToggle.textContent = entering ? "Отмена" : "Изменить";
      });
      body.after(editForm);

      const deleteToggle = document.createElement("button");
      deleteToggle.type = "button";
      deleteToggle.className = "paragraph-comment__delete-toggle";
      deleteToggle.textContent = "Удалить";
      deleteToggle.addEventListener("click", () => {
        if (window.confirm("Удалить комментарий?")) removeComment(index, comment.id);
      });

      main.append(editToggle, deleteToggle);
    }

    if (isAuthenticated) {
      const replyToggle = document.createElement("button");
      replyToggle.type = "button";
      replyToggle.className = "paragraph-comment__reply-toggle";
      replyToggle.textContent = "Ответить";
      const replyForm = buildComposer(
        (text, attachmentFile) => submitComment(index, text, comment.id, attachmentFile),
        "Ваш ответ…"
      );
      replyForm.hidden = true;
      replyToggle.addEventListener("click", () => {
        replyForm.hidden = !replyForm.hidden;
      });

      main.append(replyToggle, replyForm);
    } else {
      const link = document.createElement("a");
      link.className = "paragraph-comment__reply-toggle";
      link.href = "/login";
      link.textContent = "Войти, чтобы ответить";
      main.append(link);
    }

    // PR 152: two equivalent triggers for the same collapse state - the "[–]"/"[+]"
    // button next to "Ответить", and a click on the reply thread's own vertical guide
    // line (.paragraph-comment__replies' left border/padding, the same visual element
    // YouTube uses). Both just flip repliesDiv.hidden - commentTreeByIndex isn't touched,
    // so re-expanding never needs a request, and a collapsed parent thread takes every
    // nested sub-thread with it for free, since they're all inside this one DOM node.
    let repliesDiv = null;
    let collapseToggle = null;

    function setRepliesCollapsed(collapsed) {
      if (repliesDiv) repliesDiv.hidden = collapsed;
      if (collapseToggle) {
        collapseToggle.textContent = collapsed ? "[+]" : "[–]";
        collapseToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      }
    }

    if (comment.replies.length > 0) {
      collapseToggle = document.createElement("button");
      collapseToggle.type = "button";
      collapseToggle.className = "paragraph-comment__collapse-toggle";
      collapseToggle.textContent = "[–]";
      collapseToggle.setAttribute("aria-expanded", "true");
      collapseToggle.setAttribute("aria-label", "Свернуть ветку ответов");
      collapseToggle.addEventListener("click", () => setRepliesCollapsed(!repliesDiv.hidden));
      main.append(collapseToggle);

      repliesDiv = document.createElement("div");
      repliesDiv.className = "paragraph-comment__replies";
      if (depth + 1 > MAX_INDENT_DEPTH) {
        repliesDiv.classList.add("paragraph-comment__replies--flat");
      }

      // PR 164: the guide line as its own real element, not a border drawn on repliesDiv
      // itself - a plain border/`:hover` on the container matched anywhere in its
      // full-width box (any reply's text, not just the line), and a nested .replies'
      // hover bubbled up and lit every ancestor's line too, since nested boxes sit
      // geometrically inside their parent's. A pseudo-element (::before) doesn't fix
      // this either - real browsers don't hit-test `:hover` against a pseudo-element's
      // own rendered box independently of its host, `::before:hover` behaves exactly
      // like `:hover::before` (still keyed off the host's own hover state, still the
      // same bug). A real element sitting at its own fixed position/width does get
      // proper independent :hover matching, and each nesting level's own line sits at a
      // different x-offset than every other level's (see app.css), so they can never
      // geometrically overlap.
      const line = document.createElement("span");
      line.className = "paragraph-comment__replies-line";
      repliesDiv.append(line);

      for (const reply of comment.replies) {
        repliesDiv.append(renderCommentNode(index, reply, depth + 1));
      }
      // Either the line itself or the bare strip around it (repliesDiv's own padding,
      // not any nested reply) should toggle - event.target is one of those two exactly
      // when the click landed there, since every actual reply fills the rest of the
      // width.
      repliesDiv.addEventListener("click", (event) => {
        if (event.target === repliesDiv || event.target === line) {
          setRepliesCollapsed(!repliesDiv.hidden);
        }
      });
    }

    el.append(main);
    if (repliesDiv) el.append(repliesDiv);

    return el;
  }

  // Everything under a paragraph's own comments toggle - created once per paragraph,
  // found again on every later call instead of rebuilt (renderCommentsToggle mutates
  // its label/visibility in place, toggleComments shows/hides `.list`).
  function commentsSectionFor(index) {
    const host = paragraphHostFor(index);
    if (!host) return null;
    let section = host.querySelector(":scope > .paragraph-comments");
    if (section) return section;

    section = document.createElement("div");
    section.className = "paragraph-comments";
    section.hidden = true; // renderCommentsToggle below reveals it once count > 0

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "paragraph-comments__toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", () => toggleComments(index));
    section.append(toggle);

    const list = document.createElement("div");
    list.className = "paragraph-comments__list";
    list.hidden = true;
    section.append(list);

    host.append(section);
    return section;
  }

  // Re-renders the toggle's label/arrow from commentCountByIndex/commentsExpandedByIndex
  // - callers update one of those two maps/sets first, then call this to reflect it.
  function renderCommentsToggle(index) {
    const section = commentsSectionFor(index);
    if (!section) return;
    const count = commentCountByIndex.get(index) ?? 0;
    section.hidden = count <= 0; // empty state = show nothing, same as the reactions strip
    const toggle = section.querySelector(":scope > .paragraph-comments__toggle");
    const expanded = commentsExpandedByIndex.has(index);
    toggle.textContent = "";
    toggle.append(`${count} ${pluralizeComments(count)} `);
    const arrow = document.createElement("span");
    arrow.className = "paragraph-comments__arrow";
    arrow.textContent = expanded ? "▴" : "▾";
    toggle.append(arrow);
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function setCommentCount(index, count) {
    commentCountByIndex.set(index, count);
    renderCommentsToggle(index);
  }

  function renderCommentList(index, comments) {
    const section = commentsSectionFor(index);
    if (!section) return;
    const list = section.querySelector(":scope > .paragraph-comments__list");
    list.replaceChildren();
    for (const comment of comments) {
      list.append(renderCommentNode(index, comment));
    }
  }

  async function loadCommentTree(index) {
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments` +
          `?paragraph_index=${index}&branch_id=${encodeURIComponent(branchId)}`
      );
      if (!response.ok) return;
      const data = await response.json();
      commentTreeByIndex.set(index, data.comments);
      renderCommentList(index, data.comments);
    } catch {
      // Same "fails silently, the chapter itself still reads fine" reasoning as
      // loadInitialReactions/loadInitialCommentCounts.
    }
  }

  async function toggleComments(index) {
    const expanded = commentsExpandedByIndex.has(index);
    if (expanded) {
      commentsExpandedByIndex.delete(index);
    } else {
      commentsExpandedByIndex.add(index);
      if (!commentTreeByIndex.has(index)) await loadCommentTree(index);
    }
    const section = commentsSectionFor(index);
    const list = section?.querySelector(":scope > .paragraph-comments__list");
    if (list) list.hidden = !commentsExpandedByIndex.has(index);
    renderCommentsToggle(index); // just the arrow direction - the count itself is untouched
  }

  // Returns whether the post actually went through - buildComposer's caller uses this to
  // decide whether to clear the textarea/staged attachment (a rejected/network-failed
  // post shouldn't lose what the visitor typed or picked).
  //
  // PR 150: always FormData now, even for a plain text comment with no attachment - a
  // second urlencoded-vs-multipart code path here just to avoid a FormData object for the
  // common case isn't worth carrying once the endpoint itself already accepts multipart
  // unconditionally (it has to, for the attachment case). No Content-Type header set -
  // the browser fills in FormData's own multipart boundary, which a hardcoded header
  // would break.
  async function submitComment(index, body, parentCommentId, attachmentFile) {
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
      if (!response.ok) return false;
      data = await response.json();
    } catch {
      return false;
    }
    applyCommentTreeResponse(index, data);
    return true;
  }

  // Shared tail of submitComment/editComment/removeComment below - all three POST/PATCH/
  // DELETE endpoints return the exact same {count, comments} shape (app/api/chapters.py's
  // _comments_response()), so re-rendering from it is identical regardless of which one
  // just ran.
  function applyCommentTreeResponse(index, data) {
    commentTreeByIndex.set(index, data.comments);
    commentsExpandedByIndex.add(index);
    renderCommentList(index, data.comments);
    const section = commentsSectionFor(index);
    const list = section?.querySelector(":scope > .paragraph-comments__list");
    if (list) list.hidden = false;
    // The response always carries its own authoritative count right there, replies
    // included - no reason to leave the toggle showing a stale number until the next full
    // page load.
    setCommentCount(index, data.count);
  }

  // PR 172: "Изменить" on a comment's own body - returns whether it went through, same
  // "leave the composer's text in place on failure" contract as submitComment above.
  async function editComment(index, commentId, body) {
    const formData = new FormData();
    formData.set("body", body);
    let data;
    try {
      const response = await fetch(
        `/titles/${slugUrl}/chapters/${volume}/${number}/comments/${commentId}`,
        { method: "PATCH", body: formData }
      );
      if (!response.ok) return false;
      data = await response.json();
    } catch {
      return false;
    }
    applyCommentTreeResponse(index, data);
    return true;
  }

  // PR 172: "Удалить" on a comment's own body - the confirm() prompt lives at the call
  // site (renderCommentNode), not here, so this stays a plain "do the delete" action.
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
    applyCommentTreeResponse(index, data);
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

  // `quotedText` (PR 156) pre-fills the composer when opened via "Цитировать" instead of
  // "Комментировать" - empty for the latter, same composer either way.
  function renderCommentComposer(index, quotedText = "") {
    panel.replaceChildren();

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

    const composer = buildComposer(
      async (text, attachmentFile) => {
        const ok = await submitComment(index, text, null, attachmentFile);
        if (ok) close();
        return ok;
      },
      "Написать комментарий…",
      quotedText
    );
    panel.append(composer);
  }

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

  function renderReactionPicker(index) {
    panel.replaceChildren();

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

    const picker = document.createElement("div");
    picker.className = "paragraph-menu__emoji-picker";
    picker.setAttribute("role", "menu");
    const mineEmoji = mineByIndex.get(index) ?? null;
    for (const [emoji, label] of EMOJI) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "paragraph-menu__emoji";
      if (emoji === mineEmoji) button.classList.add("paragraph-menu__emoji--active");
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
    commentItem.addEventListener("click", (event) => {
      event.stopPropagation();
      renderCommentComposer(index);
      position(lastX, lastY);
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
      renderCommentComposer(index, quoteParagraphText(index));
      position(lastX, lastY);
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

  function open(x, y, index) {
    lastX = x;
    lastY = y;
    renderMenuItems(index);
    panel.classList.add("paragraph-menu__panel--open");
    position(x, y);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
  }

  function close() {
    panel.classList.remove("paragraph-menu__panel--open");
    window.removeEventListener("scroll", close, true);
    window.removeEventListener("resize", close);
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
    if (event.key === "Escape" && isOpen()) close();
  });
})();
