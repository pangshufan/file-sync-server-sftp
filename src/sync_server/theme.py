"""System-following widget palette and styles, including live scheme changes."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QColor, QPalette


LIGHT = dict(window="#f4f6fa", base="#ffffff", alternate="#f0f4fa",
             text="#24364e", heading="#172b4d", muted="#56647a",
             border="#bdc9d8", button="#ffffff", hover="#e5edfa",
             highlight="#245cd8", selected="#ffffff", primary="#245cd8",
             primary_hover="#1949b6", primary_text="#ffffff",
             disabled="#5d6b7d", disabled_base="#e7ecf3",
             link="#2057c4")
DARK = dict(window="#171c25", base="#202835", alternate="#263141",
            text="#e4eaf3", heading="#f4f7fc", muted="#b0bfd1",
            border="#596a81", button="#2c3748", hover="#384963",
            highlight="#325b9a", selected="#ffffff", primary="#9abaff",
            primary_hover="#b5cdff", primary_text="#101c32",
            disabled="#a0adbf", disabled_base="#252e3b",
            link="#aac7ff")


def stylesheet(colors):
    c = colors
    return f"""
QWidget {{ font-size: 13px; }}
QMainWindow, QDialog {{ background: palette(window); color: palette(window-text); }}
QLabel#heading {{ font-size: 26px; font-weight: 700; color: {c['heading']}; }}
QLabel#muted {{ color: {c['muted']}; }}
QGroupBox {{ font-weight: 600; color: palette(window-text); border: 1px solid {c['border']}; border-radius: 8px; margin-top: 10px; padding: 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
QPushButton {{ padding: 7px 14px; border: 1px solid {c['border']}; border-radius: 6px; background: {c['button']}; color: {c['text']}; }}
QPushButton:hover, QPushButton:checked {{ background: {c['hover']}; }}
QPushButton:focus {{ border: 2px solid {c['link']}; padding: 6px 13px; }}
QPushButton#primary {{ background: {c['primary']}; color: {c['primary_text']}; border-color: {c['primary']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['primary_hover']}; }}
QPushButton:disabled, QPushButton#primary:disabled {{ color: {c['disabled']}; background: {c['disabled_base']}; border-color: {c['border']}; }}
QLineEdit, QComboBox, QAbstractSpinBox {{ padding: 6px; border: 1px solid {c['border']}; border-radius: 5px; background: {c['base']}; color: {c['text']}; selection-background-color: {c['highlight']}; selection-color: {c['selected']}; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{ border-color: {c['link']}; }}
QLineEdit, QPlainTextEdit, QTextEdit {{ placeholder-text-color: {c['muted']}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ color: {c['disabled']}; background: {c['disabled_base']}; }}
QComboBox QAbstractItemView {{ background: {c['base']}; color: {c['text']}; selection-background-color: {c['highlight']}; selection-color: {c['selected']}; }}
QTableView, QPlainTextEdit, QTextEdit {{ background: {c['base']}; alternate-background-color: {c['alternate']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 5px; selection-background-color: {c['highlight']}; selection-color: {c['selected']}; gridline-color: {c['border']}; }}
QTableView::item:selected {{ background: {c['highlight']}; color: {c['selected']}; }}
QTableView:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{ color: {c['disabled']}; background: {c['disabled_base']}; }}
QHeaderView::section, QTableCornerButton::section {{ background: {c['alternate']}; color: {c['text']}; padding: 8px; border: none; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['base']}; }}
QTabBar::tab {{ padding: 10px 18px; background: {c['alternate']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
QTabBar::tab:selected {{ background: {c['base']}; color: {c['text']}; border-bottom: 2px solid {c['link']}; }}
QTabBar::tab:hover {{ background: {c['hover']}; color: {c['text']}; }}
QLabel:disabled, QGroupBox:disabled, QCheckBox:disabled, QRadioButton:disabled, QTabBar::tab:disabled {{ color: {c['disabled']}; }}
QCheckBox::indicator:unchecked {{ border: 1px solid {c['border']}; border-radius: 2px; background: {c['base']}; }}
QCheckBox::indicator:unchecked:hover {{ background: {c['hover']}; }}
QToolTip {{ background: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
"""


def apply_theme(application, dark):
    colors = DARK if dark else LIGHT
    palette = QPalette()
    roles = {
        "Window": "window", "WindowText": "text", "Base": "base",
        "AlternateBase": "alternate", "Text": "text", "Button": "button",
        "ButtonText": "text", "Highlight": "highlight", "HighlightedText": "selected",
        "ToolTipBase": "base", "ToolTipText": "text", "PlaceholderText": "muted",
        "Link": "link", "LinkVisited": "link", "BrightText": "selected", "Accent": "link",
        "Light": "border", "Midlight": "hover", "Mid": "border", "Dark": "window",
        "Shadow": "window",
    }
    for role, key in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[key]))
    for role in ("WindowText", "Text", "ButtonText", "PlaceholderText"):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(colors["disabled"]))
    for role in ("Base", "Button"):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(colors["disabled_base"]))
    application.setPalette(palette)
    application.setStyleSheet(stylesheet(colors))


class SystemTheme(QObject):
    def __init__(self, application):
        super().__init__(application)
        self.application = application
        # Capture the native fallback before installing our own palette.
        self.fallback_dark = application.palette().color(QPalette.ColorRole.Window).lightnessF() < 0.5
        application.setStyle("Fusion")
        application.styleHints().colorSchemeChanged.connect(self.scheme_changed)
        self.apply(application.styleHints().colorScheme())

    def apply(self, scheme):
        dark = self.fallback_dark if scheme == Qt.ColorScheme.Unknown else scheme == Qt.ColorScheme.Dark
        apply_theme(self.application, dark)

    def scheme_changed(self, scheme):
        # Qt updates the native palette after this signal; apply ours afterwards.
        QTimer.singleShot(0, lambda: self.apply(scheme))
