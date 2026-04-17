from ui.theme import TOKENS, build_app_stylesheet


def test_design_tokens_match_core_values() -> None:
    assert TOKENS["primary"] == "#056687"
    assert TOKENS["primary_dim"] == "#005977"
    assert TOKENS["surface"] == "#f8f9fa"
    assert TOKENS["surface_low"] == "#f1f4f6"
    assert TOKENS["surface_lowest"] == "#ffffff"
    assert TOKENS["inverse_surface"] == "#0c0f10"


def test_app_stylesheet_includes_key_sections() -> None:
    stylesheet = build_app_stylesheet()

    assert "QMainWindow" in stylesheet
    assert "QGroupBox" in stylesheet
    assert "QLineEdit" in stylesheet
    assert "QTextEdit" in stylesheet
    assert "QPushButton" in stylesheet
    assert TOKENS["primary"] in stylesheet
    assert TOKENS["primary_dim"] in stylesheet
