import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QTabWidget

from sync_server import i18n
from sync_server.qt_i18n import configure_language
from sync_server.ui import MainWindow, RuleDialog, SyncDetailsDialog, UserDialog


@pytest.mark.parametrize("preferences,expected", [(["zh-Hans-CN"], "zh"), (["zh_TW"], "zh"),
    (["en-GB"], "en"), (["de-DE", "zh-CN"], "zh"), (["fr-FR"], "en"), (["C"], "en"), ([], "en")])
def test_system_language_matching(preferences, expected, monkeypatch):
    monkeypatch.delenv("SIMPLE_SYNC_LANGUAGE", raising=False)
    assert i18n.detect_language(preferences) == expected


def test_override_placeholders_and_core_without_qt(monkeypatch):
    monkeypatch.setenv("SIMPLE_SYNC_LANGUAGE", "en_US")
    assert i18n.detect_language(["zh_CN"]) == "en"
    result = subprocess.run([sys.executable, "-c", "import sys; from sync_server.i18n import tr; from sync_server import headless; print(tr('服务已停止')); assert 'PySide6' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Server stopped"


def test_catalog_and_english_documents():
    path = Path(__file__).resolve().parents[1] / "tools/check_translations.py"
    spec = importlib.util.spec_from_file_location("check_translations", path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert not checker.check_catalog()
    assert not checker.check_documents()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_gui_language_and_display_only_statuses(language, tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_SYNC_LANGUAGE", language)
    app = QApplication.instance() or QApplication([])
    configure_language(app)
    window = MainWindow(tmp_path)
    user = UserDialog(window)
    rule = RuleDialog(window)
    details = SyncDetailsDialog(window, "a" * 32, "pending")
    try:
        window.show()
        app.processEvents()
        assert window.start_button.text() == ("Start server" if language == "en" else "启动服务")
        assert user.findChild(QTabWidget).tabText(2) == ("SSH public keys" if language == "en" else "SSH 公钥")
        assert user.confirmation.itemData(0) == "manual"
        assert details.status == "pending"
        details.populate({"files": [{"file_id": "f", "path": "/a.txt", "version": "1", "synced_version": "0",
            "updated_at": 1, "status": "pending", "confirmation": "manual", "ip": "", "error": ""}]})
        assert details.rows.item(0, 4).text() == ("Pending" if language == "en" else "待同步")
        assert details.rows.item(0, 5).text() == ("Manual" if language == "en" else "手动")
        buttons = user.findChild(QDialogButtonBox)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel).text().replace("&", "")
        assert cancel == ("Cancel" if language == "en" else "取消")
        assert "List" in user.permissions.boxes["list"].text() if language == "en" else user.permissions.boxes["list"].text() == "浏览"
    finally:
        for widget in (details, rule, user, window):
            widget.close()
        monkeypatch.setenv("SIMPLE_SYNC_LANGUAGE", "zh")
        configure_language(app)
        app.processEvents()


def test_english_cli_help_and_errors():
    env = {**os.environ, "SIMPLE_SYNC_LANGUAGE": "en"}
    help_result = subprocess.run([sys.executable, "-m", "sync_server.client", "--help"], env=env, capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "Download incremental updates" in help_result.stdout
    assert not any("\u4e00" <= char <= "\u9fff" for char in help_result.stdout)
    error = subprocess.run([sys.executable, "-m", "sync_server.client", "localhost", "downloads", "--username", "alice", "--batch"], env=env, capture_output=True, text=True)
    assert error.returncode == 2
    assert "Batch mode requires" in error.stderr
