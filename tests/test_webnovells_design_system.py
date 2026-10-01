from pathlib import Path

CSS_PATH = Path(__file__).parents[1] / "app" / "static" / "css" / "app.css"


def _css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def test_webnovells_redesign_tokens_match_design_source() -> None:
    css = _css()

    expected_tokens = {
        "--wn-canvas": "#0c0c12",
        "--wn-surface": "#13131b",
        "--wn-surface-raised": "#1a1a24",
        "--wn-border": "#24222f",
        "--wn-border-strong": "#33304a",
        "--wn-text": "#eeecf4",
        "--wn-text-secondary": "#a3a0b5",
        "--wn-text-muted": "#8c889f",
        "--wn-teal": "#3dd6c3",
        "--wn-primary": "#7a52f4",
        "--wn-danger": "#f2687a",
        "--wn-radius-sm": "8px",
        "--wn-radius-md": "12px",
        "--wn-radius-lg": "16px",
        "--wn-radius-xl": "20px",
        "--wn-space-1": "8px",
        "--wn-space-2": "16px",
        "--wn-space-3": "24px",
        "--wn-space-4": "32px",
        "--wn-space-5": "40px",
    }

    for name, value in expected_tokens.items():
        assert f"{name}: {value};" in css


def test_webnovells_redesign_exposes_reusable_component_states() -> None:
    css = _css()

    selectors = (
        ".wn-btn--primary",
        ".wn-btn--secondary",
        ".wn-btn--destructive:hover",
        '.wn-btn[aria-disabled="true"]',
        ".wn-field__control:focus",
        '.wn-field__control[aria-invalid="true"]',
        ".wn-field__caption",
        ".wn-technical",
    )

    for selector in selectors:
        assert selector in css

    assert 'Manrope, "Segoe UI", system-ui, sans-serif' in css
    assert '"JetBrains Mono", ui-monospace, monospace' in css
