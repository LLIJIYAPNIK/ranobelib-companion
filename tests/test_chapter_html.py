from app.chapter_html import sanitize_chapter_html


def test_sanitize_keeps_allowed_tags() -> None:
    raw = "<p>Hello <strong>world</strong> <em>italic</em><br></p><hr><p>next</p>"

    assert sanitize_chapter_html(raw) == raw


def test_sanitize_keeps_image_src() -> None:
    raw = '<img src="https://example.com/x.jpg" alt="pic" loading="lazy">'

    assert sanitize_chapter_html(raw) == raw


def test_sanitize_strips_script_tags() -> None:
    raw = "<p>hi</p><script>alert('xss')</script>"

    result = sanitize_chapter_html(raw)

    assert "<script>" not in result
    assert "alert" not in result


def test_sanitize_strips_event_handler_attributes() -> None:
    raw = "<img src=\"x.jpg\" onerror=\"alert('xss')\">"

    result = sanitize_chapter_html(raw)

    assert "onerror" not in result
    assert "alert" not in result


def test_sanitize_strips_disallowed_tags_but_keeps_text() -> None:
    raw = '<div class="fancy">wrapped</div>'

    assert sanitize_chapter_html(raw) == "wrapped"
