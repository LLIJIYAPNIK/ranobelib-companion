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
// composer, nested reddit-style under its parent via CSS alone (no per-depth styling
// computed in JS - see .paragraph-comment__replies in app.css).
//
// PR 134: readerSettings.showParagraphSocial (default true, like every other reading
// setting) gates this whole file - once it's explicitly false there is nothing left for
// a right-click to open (both PR 132's reactions and PR 133's comments are it), so
// nothing here so much as attaches a listener rather than just hiding what would've
// rendered. Existing reactions/comments aren't touched server-side by this - the setting
// only ever decides whether this script fetches/renders them, never deletes anything.
(() => {
  const GAP = 8;

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

  // A reusable textarea + "Отправить" button, shared by the menu's "Комментировать"
  // composer and every comment's own "Ответить" reply form - the only difference between
  // them is what `onSubmit` does with the typed body.
  function buildComposer(onSubmit, placeholder) {
    const wrap = document.createElement("div");
    wrap.className = "paragraph-comments__composer";
    const textarea = document.createElement("textarea");
    textarea.className = "paragraph-comments__textarea";
    textarea.placeholder = placeholder;
    textarea.rows = 3;
    textarea.maxLength = 2000; // mirrors MAX_COMMENT_LENGTH in app/db/comments.py
    // PR 148: the same minimal subset app/markdown_render.py actually renders - not a
    // full Markdown cheatsheet, so it doesn't promise syntax (headings, code blocks,
    // images) this feature silently drops.
    const hint = document.createElement("p");
    hint.className = "paragraph-comments__hint";
    hint.textContent = "Поддерживается: **жирный**, *курсив*, ~~зачёркнутый~~, [ссылка](url), списки";
    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "btn btn--sm";
    submit.textContent = "Отправить";
    submit.addEventListener("click", async () => {
      const body = textarea.value.trim();
      if (!body) return;
      submit.disabled = true;
      try {
        // Only clears the box on a confirmed success - a rejected/network-failed post
        // (onSubmit returning false) leaves the typed text in place so it isn't lost,
        // same reasoning a normal <form> submit failure wouldn't wipe the field either.
        if (await onSubmit(body)) textarea.value = "";
      } finally {
        submit.disabled = false;
      }
    });
    wrap.append(textarea, hint, submit);
    return wrap;
  }

  function renderCommentNode(index, comment) {
    const el = document.createElement("div");
    el.className = "paragraph-comment";

    const meta = document.createElement("div");
    meta.className = "paragraph-comment__meta";
    const author = document.createElement("span");
    author.className = "paragraph-comment__author";
    author.textContent = comment.author;
    const time = document.createElement("span");
    time.className = "paragraph-comment__time";
    time.textContent = formatCommentTime(comment.created_at);
    meta.append(author, time);
    el.append(meta);

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
    el.append(body);

    if (isAuthenticated) {
      const replyToggle = document.createElement("button");
      replyToggle.type = "button";
      replyToggle.className = "paragraph-comment__reply-toggle";
      replyToggle.textContent = "Ответить";
      const replyForm = buildComposer(
        (text) => submitComment(index, text, comment.id),
        "Ваш ответ…"
      );
      replyForm.hidden = true;
      replyToggle.addEventListener("click", () => {
        replyForm.hidden = !replyForm.hidden;
      });
      el.append(replyToggle, replyForm);
    } else {
      const link = document.createElement("a");
      link.className = "paragraph-comment__reply-toggle";
      link.href = "/login";
      link.textContent = "Войти, чтобы ответить";
      el.append(link);
    }

    if (comment.replies.length > 0) {
      const replies = document.createElement("div");
      replies.className = "paragraph-comment__replies";
      for (const reply of comment.replies) {
        replies.append(renderCommentNode(index, reply));
      }
      el.append(replies);
    }

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
  // decide whether to clear the textarea (a rejected/network-failed post shouldn't lose
  // what the visitor typed).
  async function submitComment(index, body, parentCommentId) {
    const params = new URLSearchParams({
      paragraph_index: String(index),
      body,
      branch_id: branchId,
    });
    if (parentCommentId != null) params.set("parent_comment_id", String(parentCommentId));
    let data;
    try {
      const response = await fetch(`/titles/${slugUrl}/chapters/${volume}/${number}/comments`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: params,
      });
      if (!response.ok) return false;
      data = await response.json();
    } catch {
      return false;
    }
    commentTreeByIndex.set(index, data.comments);
    commentsExpandedByIndex.add(index);
    renderCommentList(index, data.comments);
    const section = commentsSectionFor(index);
    const list = section?.querySelector(":scope > .paragraph-comments__list");
    if (list) list.hidden = false;
    // A fresh post always carries its own authoritative count right there in the
    // response, replies included - no reason to leave the toggle showing a stale number
    // until the next full page load.
    setCommentCount(index, data.count);
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

  function renderCommentComposer(index) {
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

    const composer = buildComposer(async (text) => {
      const ok = await submitComment(index, text, null);
      if (ok) close();
      return ok;
    }, "Написать комментарий…");
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
