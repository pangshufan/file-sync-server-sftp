"""Readability and live propagation of system color scheme notifications."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from sync_server.theme import DARK, LIGHT, SystemTheme, apply_theme
from sync_server.ui import MainWindow, UserDialog


def contrast(first, second):
    def luminance(value):
        rgb = QColor(value).getRgbF()[:3]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b + 0.05) / (a + 0.05)


@pytest.mark.parametrize("colors", [LIGHT, DARK])
def test_text_contrast(colors):
    for foreground, background in (("text", "window"), ("text", "base"), ("text", "alternate"),
                                    ("muted", "window"), ("muted", "base"), ("muted", "alternate"),
                                    ("disabled", "disabled_base"), ("selected", "highlight"),
                                    ("primary_text", "primary"), ("primary_text", "primary_hover")):
        assert contrast(colors[foreground], colors[background]) >= 4.5, (foreground, background)


def test_live_scheme_updates_open_window_and_dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    theme = SystemTheme(app)
    window = MainWindow(tmp_path)
    dialog = UserDialog(window)
    window.show()
    dialog.show()
    try:
        for scheme, colors in ((Qt.ColorScheme.Dark, DARK), (Qt.ColorScheme.Light, LIGHT),
                               (Qt.ColorScheme.Dark, DARK)):
            # Drive the same signal that the platform sends, with both windows open.
            app.styleHints().colorSchemeChanged.emit(scheme)
            app.processEvents()
            for widget in (window, dialog, dialog.username, window.logs, window.user_table):
                assert widget.palette().color(QPalette.ColorRole.Text).name() == colors["text"]
            assert window.findChild(QLabel, "heading").palette().color(QPalette.ColorRole.WindowText).name() == colors["heading"]
            assert dialog.username.palette().color(QPalette.ColorRole.Base).name() == colors["base"]
            assert dialog.password.palette().color(QPalette.ColorRole.PlaceholderText).name() == colors["muted"]
            assert dialog.username.palette().color(QPalette.ColorRole.HighlightedText).name() == colors["selected"]
            assert dialog.username.palette().color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Text).name() == colors["text"]
            window.host.setEnabled(False)
            assert window.host.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name() == colors["disabled"]
            window.host.setEnabled(True)
        # Unknown platform schemes retain the native startup fallback.
        theme.apply(Qt.ColorScheme.Unknown)
        expected = DARK if theme.fallback_dark else LIGHT
        assert app.palette().color(QPalette.ColorRole.Window).name() == expected["window"]
    finally:
        dialog.close()
        window.close()
        app.styleHints().colorSchemeChanged.disconnect(theme.scheme_changed)
        theme.deleteLater()
        apply_theme(app, False)
        app.processEvents()
