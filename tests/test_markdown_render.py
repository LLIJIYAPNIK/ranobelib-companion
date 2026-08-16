from app.markdown_render import render_comment_body


def test_renders_bold_italic_and_strikethrough() -> None:
    html = render_comment_body("**bold** *italic* ~~strike~~")

    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<s>strike</s>" in html


def test_renders_a_link() -> None:
    html = render_comment_body("[click here](https://example.com)")

    assert '<a href="https://example.com"' in html
    assert ">click here</a>" in html


def test_renders_a_bullet_list() -> None:
    html = render_comment_body("- one\n- two")

    assert "<ul>" in html
    assert "<li>one</li>" in html
    assert "<li>two</li>" in html


def test_single_newline_becomes_a_line_break() -> None:
    html = render_comment_body("line one\nline two")

    assert "<br>" in html


def test_blank_line_starts_a_new_paragraph() -> None:
    html = render_comment_body("first\n\nsecond")

    assert html.count("<p>") == 2


def test_a_script_tag_is_escaped_text_not_a_live_element() -> None:
    # Neither markdown-it (html_inline/html_block never enabled) nor nh3's tag allow-list
    # would let a real <script> element through - checked at both layers, not just one.
    html = render_comment_body("<script>alert(1)</script>")

    assert "<script>" not in html
    assert "<script" not in html.replace("&lt;script", "")


def test_an_img_tag_with_an_event_handler_is_not_rendered_as_an_element() -> None:
    html = render_comment_body('<img src=x onerror="alert(1)">')

    assert "<img" not in html.replace("&lt;img", "")


def test_a_javascript_scheme_link_never_becomes_a_clickable_anchor() -> None:
    # markdown-it's own link validator already refuses to turn this into an <a> at all
    # (it stays literal "[click](javascript:...)" text) - nh3's url_schemes allow-list is
    # the second, independent line of defense for anything that got past it.
    html = render_comment_body("[click](javascript:alert(1))")

    assert "<a " not in html
    assert "href" not in html


def test_raw_html_inside_a_link_label_does_not_survive_as_markup() -> None:
    html = render_comment_body('[<b onclick="evil()">text</b>](https://example.com)')

    assert "<b " not in html
    assert "<b>" not in html


def test_only_the_allowed_url_schemes_produce_a_clickable_link() -> None:
    html = render_comment_body("[mail me](mailto:a@example.com)")

    assert '<a href="mailto:a@example.com"' in html


def test_plain_text_with_no_markdown_syntax_round_trips_unescaped_for_the_reader() -> None:
    html = render_comment_body("Отличная глава, спасибо!")

    assert "Отличная глава, спасибо!" in html
