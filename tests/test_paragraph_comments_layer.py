"""PR 312 (Webnovells): the paragraph comment layer - thread and composer - in
app/static/js/paragraph-menu.js and app.css. The endpoints themselves are covered by
tests/test_chapters.py; these pin what the layer must keep doing on top of them."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/js/paragraph-menu.js").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, a media block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_every_existing_comment_action_still_calls_its_endpoint() -> None:
    comments = "/titles/${slugUrl}/chapters/${volume}/${number}/comments"
    # Counts on load, the thread on open, post (with reply parent and attachment).
    assert f"`{comments}/counts` +" in JS
    assert f"`{comments}` +\n          `?paragraph_index=${{index}}" in JS
    assert f'fetch(`{comments}`, {{\n        method: "POST",' in JS
    assert 'formData.set("parent_comment_id", String(parentCommentId));' in JS
    assert 'formData.set("attachment", attachmentFile);' in JS
    # Edit, delete, like/dislike on a comment.
    assert f'`{comments}/${{commentId}}`,\n        {{ method: "PATCH", body: formData }}' in JS
    assert f'`{comments}/${{commentId}}`,\n        {{ method: "DELETE" }}' in JS
    assert f"`{comments}/${{comment.id}}/reactions`" in JS
    # Reply / edit / delete controls, the delete confirmation, guests sent to /login.
    for label in ('"Ответить"', '"Изменить"', '"Удалить"', '"Войти, чтобы ответить"'):
        assert label in JS
    assert 'window.confirm("Удалить комментарий?")' in JS


def test_composer_keeps_the_limit_and_the_markdown_hint() -> None:
    assert "const MAX_COMMENT_LENGTH = 2000; // mirrors MAX_COMMENT_LENGTH" in JS
    assert "textarea.maxLength = MAX_COMMENT_LENGTH;" in JS
    assert "**жирный**, *курсив*, ~~зачёркнутый~~, [ссылка](url), списки" in JS
    # One primary action per composer; emoji and attachment are secondary icon buttons.
    assert JS.count("ui-btn--primary") == 1
    assert '"ui-btn ui-btn--primary ui-btn--sm paragraph-comments__submit"' in JS


def test_composer_shows_server_errors_and_keeps_the_text() -> None:
    assert "return { ok: false, message: await errorMessage(response) };" in JS
    assert "if (result.ok) {" in JS
    assert 'showError(result.message || "Не удалось отправить.' in JS
    assert 'submit.setAttribute("aria-busy", "true");' in JS


def test_thread_has_loading_error_and_empty_states() -> None:
    assert "loadingIndexes.has(index)" in JS
    assert '"Не удалось загрузить комментарии"' in JS
    assert 'retry.addEventListener("click", () => loadCommentTree(index));' in JS
    assert '"Здесь пока пусто — начните обсуждение этого абзаца."' in JS


def test_menu_opens_the_layer_instead_of_a_floating_composer() -> None:
    assert "openThread(index, { compose: true });" in JS
    assert "openThread(index, { compose: true, quote: quoteParagraphText(index) });" in JS
    assert "renderCommentComposer" not in JS


def test_phones_use_the_shared_bottom_sheet() -> None:
    assert 'const phoneQuery = window.matchMedia("(max-width: 767px)");' in JS
    assert "window.bottomSheet.open({" in JS
    assert "content: parts.layer," in JS
    # Inside the always-dark sheet the layer switches to the Webnovells palette.
    sheet = _rule(".bottom-sheet .paragraph-comments__layer")
    assert "--pc-text: var(--wn-text);" in sheet
    assert "display: none;" in _rule(".bottom-sheet .paragraph-comments__close")


def test_desktop_layer_sits_in_the_flow_under_the_paragraph() -> None:
    layer = _rule(".paragraph-comments__layer")
    assert "position:" not in layer
    assert "margin-top: 12px;" in layer
    # Reader-theme colors (all five themes), not the app's fixed dark palette.
    tokens = _rule(".paragraph-comments,\n.paragraph-comments__layer")
    assert "--pc-text: var(--r-text);" in tokens
    assert "--pc-surface: var(--r-surface);" in tokens


def test_touch_targets_are_44px() -> None:
    touch = ".paragraph-comments__toggle,\n  .paragraph-comment__action,\n  "
    assert "min-height: 44px;" in _rule(touch + ".paragraph-comment__reaction", indent="  ")
    icon = _rule(".paragraph-comments__icon-btn", indent="  ")
    assert "width: 44px;" in icon
    assert "height: 44px;" in icon


def test_keyboard_and_focus() -> None:
    # Ctrl/Cmd+Enter sends, Escape cancels a reply/edit, focus lands on the result.
    assert 'event.key === "Enter" && (event.ctrlKey || event.metaKey)' in JS
    assert 'event.key === "Escape" && onCancel' in JS
    assert "node.focus({ preventScroll: true });" in JS
    assert 'toggle.setAttribute("aria-controls", layerId);' in JS
    assert 'collapseToggle.setAttribute("aria-controls", repliesDiv.id);' in JS
