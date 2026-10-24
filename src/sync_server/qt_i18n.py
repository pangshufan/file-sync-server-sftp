"""Initialize app and stock Qt translations from OS UI language preferences."""
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

from .i18n import detect_language, set_language


def configure_language(application):
    previous = getattr(application, "_sync_qt_translator", None)
    if previous is not None:
        application.removeTranslator(previous)
        del application._sync_qt_translator
    language = detect_language(QLocale.system().uiLanguages())
    set_language(language)
    # Resolve Qt's built-in buttons and calendar as well as our own messages.
    QLocale.setDefault(QLocale("zh_CN" if language == "zh" else "en_US"))
    if language == "zh":
        translator = QTranslator(application)
        bundled = Path(__file__).parent / "locales"
        native = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        if translator.load("qtbase_zh_CN", str(bundled)) or translator.load("qtbase_zh_CN", str(native)):
            application.installTranslator(translator)
            # Qt does not take ownership of an installed translator's Python wrapper.
            application._sync_qt_translator = translator
    return language
