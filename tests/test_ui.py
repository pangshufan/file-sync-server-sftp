import asyncio
import os
import socket
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncssh
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QDateTime

from sync_server.config import Config, ConfigStore
from sync_server.ui import MainWindow, RuleDialog, STYLE, UserDialog


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


def wait_until(application, predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(10)
    assert predicate(), "GUI state update timed out"


def test_user_dialog_and_service_lifecycle(application, tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "ui.txt").write_text("ui download")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    store = ConfigStore(tmp_path / "state")
    store.save(Config(port=port))
    window = MainWindow(store.directory)
    window.show()
    try:
        dialog = UserDialog(window)
        dialog.username.setText("demo")
        dialog.password.setText("test-ui-password")
        dialog.root.setText(str(root))
        rule = RuleDialog(dialog)
        rule.path.setText("/private")
        for box in rule.permissions.boxes.values():
            box.setChecked(False)
        rule.validate()
        dialog.rules.append(rule.result_rule)
        dialog.validate()
        user = dialog.result_user
        assert user.permissions == ["list", "download"]
        edit = UserDialog(window, user)
        edit.validate()
        assert edit.result_user.password_hash == user.password_hash
        assert edit.result_user.userid == user.userid
        window.config.users = [user]
        window.refresh_users()
        assert window.user_table.rowCount() == 1
        assert window.save()
        window.start_button.click()
        wait_until(application, lambda: window.running)
        assert not window.settings.isEnabled()
        assert not window.user_buttons[0].isEnabled()

        async def connect():
            key = asyncssh.read_private_key(store.directory / "ssh_host_ed25519_key").convert_to_public()
            return await asyncssh.connect("127.0.0.1", port, username="demo", password="test-ui-password",
                                          known_hosts=([key], [], []), client_keys=[], agent_path=None)

        client = asyncio.run_coroutine_threadsafe(connect(), window.bridge.loop).result(5)
        wait_until(application, lambda: window.connection_table.rowCount() == 1)
        assert window.connection_table.item(0, 0).text() == "demo"
        assert window.connection_table.item(0, 1).text() == "127.0.0.1"
        assert not window.scan_interval.isEnabled()

        async def sync_task():
            from sync_server.client import PullClient
            for _ in range(100):
                state = await window.bridge.service.sync_status()
                if state["roots"] and state["roots"][0]["ready"]:
                    break
                await asyncio.sleep(0.05)
            async with client.start_sftp_client() as sftp:
                puller = PullClient(sftp, tmp_path / "download", tmp_path / "download-state", report=lambda *_: None)
                try:
                    return await puller.run_once()
                finally:
                    puller.close()

        result = asyncio.run_coroutine_threadsafe(sync_task(), window.bridge.loop).result(8)
        assert result["complete"]
        window.request_sync_status()
        wait_until(application, lambda: window.task_table.rowCount() == 1)
        assert window.task_table.item(0, 0).text() == "demo"
        assert window.task_table.item(0, 3).text() == "0"
        window.task_table.selectRow(0)
        window.show_sync_details("done")
        wait_until(application, lambda: window.details_dialog.rows.rowCount() == 1)
        assert window.details_dialog.rows.item(0, 0).text() == "/ui.txt"
        assert window.details_dialog.rows.item(0, 5).text() == "手动"
        window.details_dialog.close()
        window.connection_table.selectRow(0)
        window.disconnect_button.click()
        wait_until(application, lambda: window.connection_table.rowCount() == 0)
        wait_until(application, client.is_closed)
        # Closing a running window must asynchronously stop its listener first.
        window.close()
        wait_until(application, lambda: not window.isVisible())
        assert not window.bridge.thread.is_alive()
    finally:
        if window.bridge.thread.is_alive():
            future = asyncio.run_coroutine_threadsafe(window.bridge.service.stop(), window.bridge.loop)
            future.result(5)
            window.running = window.busy = False
            window.close()
            application.processEvents()


def test_user_sync_settings_and_explicit_restart(application, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    window = MainWindow(tmp_path / "state")
    try:
        dialog = UserDialog(window)
        dialog.username.setText("alice")
        dialog.password.setText("password")
        dialog.root.setText(str(root))
        dialog.allowed_ips.setText("127.0.0.1; ::1/128")
        dialog.sync_dirs.setPlainText("/reports\n/public")
        dialog.includes.setPlainText("*.csv\n*.txt")
        dialog.excludes.setPlainText("private/**")
        dialog.start_unlimited.setChecked(False)
        dialog.start_time.setDateTime(QDateTime.fromSecsSinceEpoch(1700000000))
        dialog.confirmation.setCurrentIndex(1)
        dialog.validate()
        user = dialog.result_user
        assert user.confirmation == "auto"
        assert user.sync_start == 1700000000
        assert user.allowed_ips == ["127.0.0.1", "::1/128"]
        assert user.sync_directories == ["/reports", "/public"]
        edit = UserDialog(window, user)
        edit.username.setText("renamed")
        edit.restart.setChecked(True)
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        edit.validate()
        assert edit.result_user.userid == user.userid
        assert edit.result_user.sync_generation == 1
    finally:
        window.close()
        application.processEvents()


def test_public_key_only_user_dialog_and_close_during_reload(application, tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    window = MainWindow(tmp_path / "state")
    try:
        dialog = UserDialog(window)
        dialog.username.setText("keyuser")
        dialog.root.setText(str(root))
        key = asyncssh.generate_private_key("ssh-ed25519")
        dialog.authorized_keys.setPlainText(key.export_public_key().decode())
        dialog.key_only.setChecked(True)
        dialog.validate()
        assert dialog.result_user.password_hash == ""
        assert dialog.result_user.authorized_keys
        window.store.save(window.config)
        window.running = window.busy = window.closing = True
        stopped = []
        monkeypatch.setattr(window, "stop", lambda: stopped.append(True))
        window.on_completed("reload", {"ok": True, "value": {"changed_accounts": 0}})
        assert stopped
    finally:
        window.running = window.busy = window.closing = False
        window.close()
        application.processEvents()
