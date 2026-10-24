import sys
from tempfile import TemporaryDirectory
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .ui import MainWindow
from .theme import SystemTheme
from .qt_i18n import configure_language
from .i18n import tr


def main():
    application = QApplication(sys.argv)
    application.setOrganizationName("SimpleSync")
    application.setApplicationName("SFTPServer")
    language = configure_language(application)
    application.setApplicationDisplayName(tr("Simple Sync · SFTP 文件服务"))
    application.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "app-icon.png")))
    theme = SystemTheme(application)
    if "--smoke-test" in sys.argv:
        if language == "zh" and not hasattr(application, "_sync_qt_translator"):
            raise RuntimeError("Qt Chinese translations could not be loaded")
        # Exercise the packaged Qt plugins and UI without touching real user data.
        with TemporaryDirectory(prefix="simple-sync-smoke-") as temporary:
            if application.windowIcon().isNull():
                raise RuntimeError("Application icon could not be loaded")
            window = MainWindow(Path(temporary))
            window.show()
            QTimer.singleShot(500, window.close)
            return application.exec()
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    window = MainWindow(directory)
    window.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
