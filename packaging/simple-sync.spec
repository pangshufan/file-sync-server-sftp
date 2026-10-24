"""Native builds only: run tools/build_app.py on the target operating system."""
import sys
from pathlib import Path
from importlib.metadata import version
from PyInstaller.utils.hooks import collect_submodules
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).parent
catalogs = [(str(root / "src/sync_server/locales/en.json"), "sync_server/locales")]
mac_locales = [(str(root / f"packaging/macos/{language}.lproj/InfoPlist.strings"), f"{language}.lproj")
               for language in ("en", "zh-Hans")] if sys.platform == "darwin" else []
qt_chinese = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_zh_CN.qm"
if not qt_chinese.is_file():
    raise RuntimeError("Qt Simplified Chinese translations are required for packaging")
icons = root / "packaging/icons"
icon = str(icons / ("app.icns" if sys.platform == "darwin" else "app.ico")) if sys.platform in ("darwin", "win32") else str(root / "src/sync_server/assets/app-icon.png")

gui = Analysis(
    [str(root / "packaging/gui_entry.py")],
    pathex=[str(root / "src")],
    datas=[(str(root / "src/sync_server/assets"), "sync_server/assets"),
           *catalogs, *mac_locales, (str(qt_chinese), "sync_server/locales")],
    hiddenimports=collect_submodules("watchdog.observers"),
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick"],
)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name="SimpleSyncServer", console=False, icon=icon)
gui_dist = COLLECT(gui_exe, gui.binaries, gui.datas, name="SimpleSyncServer")
if sys.platform == "darwin":
    BUNDLE(gui_dist, name="Simple Sync Server.app", icon=icon,
           bundle_identifier="org.simplesync.sftpserver",
           info_plist={"CFBundleShortVersionString": version("simple-sync-server"),
                       "CFBundleVersion": version("simple-sync-server"),
                       "CFBundleDevelopmentRegion": "en",
                       "CFBundleLocalizations": ["en", "zh-Hans"],
                       "NSHighResolutionCapable": True,
                       "NSLocalNetworkUsageDescription": "Provide SFTP access to configured shared folders on your local network."})

cli = Analysis([str(root / "packaging/cli_entry.py")], pathex=[str(root / "src")], datas=catalogs,
               excludes=["PySide6", "watchdog", "tkinter"])
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
              name="simple-sync-pull", console=True)
COLLECT(cli_exe, cli.binaries, cli.datas, name="simple-sync-pull")

server = Analysis([str(root / "packaging/headless_entry.py")], pathex=[str(root / "src")], datas=catalogs,
                  hiddenimports=collect_submodules("watchdog.observers"), excludes=["PySide6", "tkinter"])
server_exe = EXE(PYZ(server.pure), server.scripts, [], exclude_binaries=True,
                 name="simple-sync-serve", console=True)
COLLECT(server_exe, server.binaries, server.datas, name="simple-sync-serve")
