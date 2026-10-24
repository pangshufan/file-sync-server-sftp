from __future__ import annotations


import asyncio
import copy
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Qt, QDateTime
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDateTimeEdit,
    QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .i18n import status_label, tr
from .config import Config, ConfigStore, DirectoryRule, LABELS, PERMISSIONS, User, hash_password, virtual_path
from .server import SFTPService
from .theme import LIGHT, stylesheet


class Bridge(QObject):
    event = Signal(str, object)
    completed = Signal(str, object)

    def __init__(self, directory):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="sftp-network")
        self.directory = directory
        self.thread.start()
        self.ready.wait()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.service = SFTPService(self.directory, self.event.emit)
        self.ready.set()
        self.loop.run_forever()
        self.loop.close()

    def submit(self, action, *args):
        future = asyncio.run_coroutine_threadsafe(getattr(self.service, action)(*args), self.loop)

        def done(result):
            try:
                value = result.result()
                self.completed.emit(action, {"ok": True, "value": value})
            except Exception as exc:
                self.completed.emit(action, {"ok": False, "error": str(exc)})

        future.add_done_callback(done)


class PermissionEditor(QWidget):
    def __init__(self, permissions):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.boxes = {}
        for key in PERMISSIONS:
            box = QCheckBox(tr(LABELS[key]))
            box.setChecked(key in permissions)
            self.boxes[key] = box
            layout.addWidget(box)

    def values(self):
        return [key for key, box in self.boxes.items() if box.isChecked()]


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setAlternatingRowColors(True)
    return widget


class RuleDialog(QDialog):
    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.setWindowTitle(tr('子目录权限'))
        layout = QVBoxLayout(self)
        notice = QLabel(tr('填写共享根目录下的路径，如 /reports。权限完整覆盖上级规则；全部取消即禁止访问。'))
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.path = QLineEdit(rule.path if rule else "")
        self.path.setPlaceholderText("/reports")
        layout.addWidget(self.path)
        self.permissions = PermissionEditor(rule.permissions if rule else ["list", "download"])
        layout.addWidget(self.permissions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        try:
            self.result_rule = DirectoryRule(virtual_path(self.path.text().strip()), self.permissions.values())
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, tr('目录规则无效'), str(exc))


class UserDialog(QDialog):
    def __init__(self, parent, user=None):
        super().__init__(parent)
        self.original = user
        self.identifier = user.userid if user else uuid.uuid4().hex
        self.rules = copy.deepcopy(user.rules) if user else []
        self.setWindowTitle(tr('编辑用户') if user else tr('添加用户'))
        self.resize(900, 690)
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        basic = QWidget()
        tabs.addTab(basic, tr('账号与目录权限'))
        layout = QVBoxLayout(basic)
        form = QFormLayout()
        self.userid = QLineEdit(self.identifier)
        self.userid.setReadOnly(True)
        self.single_connection = QCheckBox(tr('仅允许一个连接；已有连接时拒绝新登录'))
        self.single_connection.setChecked(user.single_connection if user else True)
        self.allowed_ips = QLineEdit("; ".join(user.allowed_ips) if user else "")
        self.allowed_ips.setPlaceholderText(tr('留空不限制，例如 192.168.1.10; 192.168.2.0/24; ::1'))
        self.username = QLineEdit(user.username if user else "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(tr('留空保留原密码') if user else tr('设置登录密码'))
        self.root = QLineEdit(user.root if user else "")
        self.root.setPlaceholderText(tr('选择此用户可访问的本机目录'))
        root_row = QHBoxLayout()
        root_row.addWidget(self.root)
        browse = QPushButton(tr('选择目录…'))
        browse.clicked.connect(self.browse)
        root_row.addWidget(browse)
        self.enabled = QCheckBox(tr('允许登录'))
        self.enabled.setChecked(user.enabled if user else True)
        form.addRow(tr('固定 userid'), self.userid)
        form.addRow(tr('用户名'), self.username)
        form.addRow(tr('密码'), self.password)
        form.addRow(tr('共享根目录'), root_row)
        form.addRow(tr('账号状态'), self.enabled)
        form.addRow(tr('登录连接限制'), self.single_connection)
        form.addRow(tr('登录 IP / CIDR'), self.allowed_ips)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr('根目录默认权限')))
        self.permissions = PermissionEditor(user.permissions if user else ["list", "download"])
        layout.addWidget(self.permissions)
        layout.addWidget(QLabel(tr('子目录覆盖规则 · 使用最长路径匹配 · 上传权限包含覆盖文件')))
        self.rules_table = table([tr('目录'), tr('允许的操作')])
        layout.addWidget(self.rules_table)
        actions = QHBoxLayout()
        for label, callback in ((tr('添加规则'), self.add_rule), (tr('编辑规则'), self.edit_rule), (tr('移除规则'), self.remove_rule)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        sync_tab = QWidget()
        tabs.addTab(sync_tab, tr('增量同步设置'))
        sync_layout = QVBoxLayout(sync_tab)
        sync_form = QFormLayout()
        self.sync_enabled = QCheckBox(tr('启用此 userid 的增量清单'))
        self.sync_enabled.setChecked(user.sync_enabled if user else True)
        self.sync_dirs = QPlainTextEdit("\n".join(user.sync_directories) if user else "/")
        self.sync_dirs.setMaximumHeight(85)
        self.sync_dirs.setPlaceholderText(tr('每行一个共享目录内的虚拟路径，如 /reports'))
        self.start_unlimited = QCheckBox(tr('不限制起始时间（Unix 时间 0）'))
        self.start_unlimited.setChecked(not user or user.sync_start == 0)
        self.start_time = QDateTimeEdit()
        self.start_time.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start_time.setCalendarPopup(True)
        self.start_time.setMinimumDateTime(QDateTime.fromSecsSinceEpoch(0))
        self.start_time.setDateTime(QDateTime.fromSecsSinceEpoch(int(user.sync_start)) if user and user.sync_start else QDateTime.currentDateTime())
        self.start_time.setEnabled(not self.start_unlimited.isChecked())
        self.start_unlimited.toggled.connect(lambda checked: self.start_time.setEnabled(not checked))
        self.includes = QPlainTextEdit("\n".join(user.include_patterns) if user else "*")
        self.excludes = QPlainTextEdit("\n".join(user.exclude_patterns) if user else "")
        self.includes.setMaximumHeight(60)
        self.excludes.setMaximumHeight(60)
        self.confirmation = QComboBox()
        self.confirmation.addItem(tr('手动：客户端校验落盘后主动确认'), "manual")
        self.confirmation.addItem(tr('自动：单句柄完整传出并正常关闭'), "auto")
        self.confirmation.setCurrentIndex(1 if user and user.confirmation == "auto" else 0)
        self.restart = QCheckBox(tr('从新时间重新开始：清除该用户待同步、成功及归档状态'))
        sync_form.addRow(tr('同步状态'), self.sync_enabled)
        sync_form.addRow(tr('同步目录（每行一个）'), self.sync_dirs)
        sync_form.addRow(tr('时间下限'), self.start_unlimited)
        sync_form.addRow(tr('增量起始时间（server 本地）'), self.start_time)
        sync_form.addRow(tr('包含规则（每行一个）'), self.includes)
        sync_form.addRow(tr('排除规则（每行一个）'), self.excludes)
        sync_form.addRow(tr('成功确认方式'), self.confirmation)
        sync_form.addRow(tr('重设策略'), self.restart)
        sync_layout.addLayout(sync_form)
        notice = QLabel(tr('默认修改起始时间会保留未完成项。勾选重新开始会丢弃旧进度；全量下载请使用普通 SFTP。\n自动确认只代表 server 完整传出，不证明客户端校验和落盘。断点续传及删除事件仍需手动确认。'))
        notice.setWordWrap(True)
        sync_layout.addWidget(notice)
        sync_layout.addStretch()
        keys_tab = QWidget()
        tabs.addTab(keys_tab, tr('SSH 公钥'))
        keys_layout = QVBoxLayout(keys_tab)
        keys_notice = QLabel(tr('每行填写一个 OpenSSH 公钥（最多 32 个），不要粘贴私钥。密码与公钥可同时使用。\n勾选仅公钥登录后清除账号密码；需至少配置一个公钥。'))
        keys_notice.setWordWrap(True)
        keys_layout.addWidget(keys_notice)
        self.authorized_keys = QPlainTextEdit("\n".join(user.authorized_keys) if user else "")
        self.authorized_keys.setPlaceholderText("ssh-ed25519 AAAA... comment")
        keys_layout.addWidget(self.authorized_keys)
        self.key_only = QCheckBox(tr('仅允许公钥登录（清除密码）'))
        self.key_only.setChecked(bool(user and not user.password_hash))
        keys_layout.addWidget(self.key_only)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.refresh_rules()

    def browse(self):
        directory = QFileDialog.getExistingDirectory(self, tr('选择共享根目录'), self.root.text())
        if directory:
            self.root.setText(directory)

    def refresh_rules(self):
        self.rules_table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            self.rules_table.setItem(row, 0, QTableWidgetItem(rule.path))
            self.rules_table.setItem(row, 1, QTableWidgetItem(tr("、").join(tr(LABELS[p]) for p in rule.permissions) or tr('禁止访问')))

    def add_rule(self):
        dialog = RuleDialog(self)
        if dialog.exec():
            self.rules.append(dialog.result_rule)
            self.refresh_rules()

    def edit_rule(self):
        row = self.rules_table.currentRow()
        if row >= 0:
            dialog = RuleDialog(self, self.rules[row])
            if dialog.exec():
                self.rules[row] = dialog.result_rule
                self.refresh_rules()

    def remove_rule(self):
        row = self.rules_table.currentRow()
        if row >= 0:
            self.rules.pop(row)
            self.refresh_rules()

    def validate(self):
        try:
            encoded = hash_password(self.password.text()) if self.password.text() else (self.original.password_hash if self.original else "")
            if self.key_only.isChecked():
                encoded = ""
            previous_generation = self.original.sync_generation if self.original else 0
            self.result_user = User(self.username.text().strip(), encoded, self.root.text().strip(), self.enabled.isChecked(), self.permissions.values(), self.rules,
                userid=self.identifier, single_connection=self.single_connection.isChecked(),
                allowed_ips=[part.strip() for part in self.allowed_ips.text().replace(",", ";").split(";") if part.strip()],
                sync_enabled=self.sync_enabled.isChecked(), sync_directories=[p.strip() for p in self.sync_dirs.toPlainText().splitlines() if p.strip()],
                sync_start=0 if self.start_unlimited.isChecked() else self.start_time.dateTime().toSecsSinceEpoch(),
                include_patterns=[p.strip() for p in self.includes.toPlainText().splitlines() if p.strip()],
                exclude_patterns=[p.strip() for p in self.excludes.toPlainText().splitlines() if p.strip()],
                confirmation=self.confirmation.currentData(), sync_generation=previous_generation + int(self.restart.isChecked()),
                authorized_keys=[line.strip() for line in self.authorized_keys.toPlainText().splitlines() if line.strip()])
            Config(users=[self.result_user]).validate(self.parent().store.directory)
            if self.restart.isChecked() and QMessageBox.question(self, tr('确认丢弃旧同步状态'), tr('将清除该用户待同步和已完成状态，较早的遗漏文件可能不再补发。确认从所选时间重新开始？')) != QMessageBox.StandardButton.Yes:
                return
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, tr('用户配置无效'), str(exc))


class SyncDetailsDialog(QDialog):
    def __init__(self, parent, userid, status):
        super().__init__(parent)
        self.userid, self.status, self.after, self.pending = userid, status, "", False
        self.setWindowTitle(tr('同步文件明细 · {value0}', value0=status_label(status)))
        self.resize(1100, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('userid：{value0} · 每页 200 条；成功归档仅保留版本摘要，不显示在明细中', value0=userid)))
        self.rows = table([tr('路径'), tr('目标版本'), tr('成功版本'), tr('server 更新时间'), tr('状态'), tr('最近成功方式'), tr('来源 IP'), tr('错误')])
        layout.addWidget(self.rows)
        self.next_button = QPushButton(tr('下一页'))
        self.next_button.clicked.connect(self.load_next)
        layout.addWidget(self.next_button)

    def load_next(self):
        if not self.pending:
            self.pending = True
            self.next_button.setEnabled(False)
            self.parent().bridge.submit("sync_details", self.userid, self.status, self.after)

    def populate(self, page):
        rows = page["files"]
        self.pending = False
        self.rows.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [item['path'], str(item['version']), str(item['synced_version']),
                      datetime.fromtimestamp(item['updated_at']).strftime("%Y-%m-%d %H:%M:%S"),
                      status_label(item['status']), status_label(item['confirmation'] or ""), item['ip'] or "", item['error'] or ""]
            for col, value in enumerate(values):
                self.rows.setItem(row, col, QTableWidgetItem(value))
        if rows:
            self.after = rows[-1]["file_id"]
        self.next_button.setEnabled(len(rows) == 200)


class MainWindow(QMainWindow):
    def __init__(self, directory: Path):
        super().__init__()
        self.store = ConfigStore(directory)
        self.load_error = None
        try:
            self.config = self.store.load()
        except Exception as exc:
            self.load_error = str(exc)
            self.config = Config()
        self.running = False
        self.busy = False
        self.closing = False
        self.sessions = {}
        self.bridge = Bridge(directory)
        self.bridge.event.connect(self.on_event)
        self.bridge.completed.connect(self.on_completed)
        self.setWindowTitle(tr('Simple Sync · SFTP 文件服务'))
        self.resize(1120, 780)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        heading = QLabel(tr('SFTP 文件服务'))
        heading.setObjectName("heading")
        layout.addWidget(heading)
        subheading = QLabel(tr('配置监听地址、管理用户目录权限，并查看实时连接。'))
        subheading.setObjectName("muted")
        layout.addWidget(subheading)
        self.settings = QGroupBox(tr('服务配置'))
        settings_layout = QHBoxLayout(self.settings)
        settings_layout.addWidget(QLabel(tr('监听 IP')))
        self.host = QComboBox()
        self.host.setEditable(True)
        addresses = ["127.0.0.1", "0.0.0.0", "::1", "::"]
        addresses += [a.toString() for a in QNetworkInterface.allAddresses() if "%" not in a.toString()]
        self.host.addItems(list(dict.fromkeys(addresses)))
        self.host.setCurrentText(self.config.host)
        settings_layout.addWidget(self.host, 1)
        settings_layout.addWidget(QLabel(tr('端口')))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.config.port)
        settings_layout.addWidget(self.port)
        self.save_button = QPushButton(tr('保存配置'))
        self.save_button.clicked.connect(self.save)
        settings_layout.addWidget(self.save_button)
        limits_button = QPushButton(tr('登录保护…'))
        limits_button.clicked.connect(self.edit_limits)
        settings_layout.addWidget(limits_button)
        layout.addWidget(self.settings)
        scan_row = QHBoxLayout()
        self.scan_interval = QSpinBox()
        self.scan_interval.setRange(0, 86400)
        self.scan_interval.setSpecialValueText(tr('自动（按规模）'))
        self.scan_interval.setSuffix(tr(' 秒'))
        self.scan_interval.setValue(self.config.scan_interval)
        self.audit_days = QSpinBox()
        self.audit_days.setRange(1, 365)
        self.audit_days.setValue(self.config.audit_days)
        self.retention_days = QSpinBox()
        self.retention_days.setRange(1, 3650)
        self.retention_days.setValue(self.config.retention_days)
        for label, field in ((tr('全量元数据巡检'), self.scan_interval), (tr('摘要复核间隔（天）'), self.audit_days), (tr('变更日志保留（天）'), self.retention_days)):
            scan_row.addWidget(QLabel(label))
            scan_row.addWidget(field)
        layout.addLayout(scan_row)
        row = QHBoxLayout()
        self.state_label = QLabel(tr('● 已停止'))
        row.addWidget(self.state_label)
        self.count_label = QLabel(tr('0 个在线连接'))
        row.addWidget(self.count_label)
        row.addStretch()
        self.reload_button = QPushButton(tr('热加载配置文件'))
        self.reload_button.clicked.connect(self.reload_from_disk)
        row.addWidget(self.reload_button)
        self.start_button = QPushButton(tr('启动服务'))
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton(tr('停止服务'))
        self.stop_button.clicked.connect(self.stop)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.fingerprint = QLabel(tr('首次启动时生成主机密钥；客户端连接时请核对指纹。'))
        self.fingerprint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fingerprint.setObjectName("muted")
        layout.addWidget(self.fingerprint)
        tabs = QTabWidget()
        user_tab = QWidget()
        user_layout = QVBoxLayout(user_tab)
        self.user_table = table([tr('用户名'), tr('状态'), tr('共享根目录'), tr('默认权限'), tr('子目录规则'), "userid", tr('确认方式')])
        self.user_table.doubleClicked.connect(self.edit_user)
        user_layout.addWidget(self.user_table)
        user_actions = QHBoxLayout()
        self.user_buttons = []
        for label, callback in ((tr('添加用户'), self.add_user), (tr('编辑用户 / 权限'), self.edit_user), (tr('启用 / 停用'), self.toggle_user), (tr('删除用户'), self.remove_user)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.user_buttons.append(button)
            user_actions.addWidget(button)
        user_actions.addStretch()
        user_layout.addLayout(user_actions)
        user_layout.addWidget(QLabel(tr('修改用户与权限前请停止服务；保存后下次启动生效。每个客户端的 / 对应其共享根目录。')))
        tabs.addTab(user_tab, tr('用户与目录权限'))
        connection_tab = QWidget()
        connection_layout = QVBoxLayout(connection_tab)
        self.connection_table = table([tr('用户'), tr('来源 IP'), tr('来源端口'), tr('连接时间'), tr('在线时长'), tr('共享目录')])
        connection_layout.addWidget(self.connection_table)
        self.disconnect_button = QPushButton(tr('断开选中连接'))
        self.disconnect_button.clicked.connect(self.disconnect)
        connection_layout.addWidget(self.disconnect_button, alignment=Qt.AlignmentFlag.AlignRight)
        tabs.addTab(connection_tab, tr('当前连接'))
        sync_tab = QWidget()
        sync_layout = QVBoxLayout(sync_tab)
        self.index_label = QLabel(tr('启动服务后自动建立索引；同步设置由 server 管理，每个 userid 一份进度。'))
        self.index_label.setWordWrap(True)
        sync_layout.addWidget(self.index_label)
        self.task_table = table([tr('用户'), "userid", tr('增量起始时间'), tr('待同步'), tr('失败'), tr('已同步（含归档）'), tr('已处理位置'), tr('最近成功')])
        sync_layout.addWidget(self.task_table)
        details_row = QHBoxLayout()
        for label, status in ((tr('查看待同步'), "pending"), (tr('查看失败'), "failed"), (tr('查看成功明细'), "done")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=status: self.show_sync_details(value))
            details_row.addWidget(button)
        details_row.addStretch()
        sync_layout.addLayout(details_row)
        self.scan_button = QPushButton(tr('立即巡检文件索引'))
        self.scan_button.clicked.connect(lambda: self.bridge.submit("scan_now"))
        sync_layout.addWidget(self.scan_button, alignment=Qt.AlignmentFlag.AlignRight)
        sync_layout.addWidget(QLabel(tr('文件级增量 · 每个 userid 独立进度 · 同步设置在“编辑用户”中修改 · 最多显示 500 个用户')))
        tabs.addTab(sync_tab, tr('增量同步'))
        layout.addWidget(tabs, 1)
        layout.addWidget(QLabel(tr('运行日志')))
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(2000)
        self.logs.setMaximumHeight(155)
        layout.addWidget(self.logs)
        self.setCentralWidget(central)
        self.statusBar().showMessage(tr('配置目录：{value0}', value0=directory))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_connections)
        self.timer.start(1000)
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.request_sync_status)
        self.sync_timer.start(3000)
        self.sync_status_busy = False
        self.refresh_users()
        self.update_controls()
        if self.load_error:
            QTimer.singleShot(0, self.report_load_error)

    def report_load_error(self):
        QMessageBox.critical(self, tr('配置读取失败'), tr('原配置未覆盖，请修复或备份后重新启动。\n{value0}', value0=self.load_error))
        self.settings.setEnabled(False)
        self.start_button.setEnabled(False)
        for button in self.user_buttons:
            button.setEnabled(False)

    def append_log(self, message):
        message = str(message).replace("\n", " ").replace("\r", " ")
        self.logs.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")

    def collect(self):
        self.config.host = self.host.currentText().strip()
        self.config.port = self.port.value()
        self.config.scan_interval = self.scan_interval.value()
        self.config.audit_days = self.audit_days.value()
        self.config.retention_days = self.retention_days.value()
        self.config.validate(self.store.directory)
        return self.config

    def save(self):
        try:
            self.store.save(self.collect())
            self.append_log(tr('配置已保存'))
            return True
        except Exception as exc:
            QMessageBox.warning(self, tr('保存失败'), str(exc))
            return False

    def edit_limits(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('登录保护'))
        layout = QFormLayout(dialog)
        fields = {}
        for name, label, upper in (("auth_max_inflight", tr('并发密码计算上限'), 64),
                                   ("auth_ip_attempts", tr('每 IP 窗口内校验次数'), 10000),
                                   ("auth_user_attempts", tr('每账号窗口内校验次数'), 10000),
                                   ("auth_window_seconds", tr('滑动窗口（秒）'), 86400),
                                   ("auth_max_connections", tr('在线连接总上限'), 10000)):
            field = QSpinBox()
            field.setRange(1, upper)
            field.setValue(getattr(self.config, name))
            fields[name] = field
            layout.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            old = copy.deepcopy(self.config)
            for name, field in fields.items():
                setattr(self.config, name, field.value())
            if not self.save():
                self.config = old

    def reload_from_disk(self):
        try:
            config = self.store.load()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr('配置读取失败'), str(exc))
            return
        self.busy = True
        self.update_controls()
        self.bridge.submit("reload", config)

    def start(self):
        if self.save():
            self.busy = True
            self.state_label.setText(tr('● 正在启动…'))
            self.update_controls()
            self.bridge.submit("start", copy.deepcopy(self.config))

    def stop(self):
        self.busy = True
        self.state_label.setText(tr('● 正在停止…'))
        self.update_controls()
        self.bridge.submit("stop")

    def update_controls(self):
        editable = not self.running and not self.busy and not self.load_error
        self.settings.setEnabled(editable)
        for button in self.user_buttons:
            button.setEnabled(editable)
        self.start_button.setEnabled(editable)
        self.stop_button.setEnabled(self.running and not self.busy)
        self.reload_button.setEnabled(self.running and not self.busy)
        self.disconnect_button.setEnabled(self.running and not self.busy)
        self.scan_button.setEnabled(self.running and not self.busy)
        for field in (self.scan_interval, self.audit_days, self.retention_days):
            field.setEnabled(editable)

    def on_completed(self, action, result):
        if action == "reload":
            self.busy = False
            if result["ok"]:
                self.config = self.store.load()
                self.scan_interval.setValue(self.config.scan_interval)
                self.audit_days.setValue(self.config.audit_days)
                self.retention_days.setValue(self.config.retention_days)
                self.refresh_users()
            self.update_controls()
        if action == "sync_details":
            if result["ok"] and hasattr(self, "details_dialog") and self.details_dialog.userid == result["value"]["userid"] and self.details_dialog.status == result["value"]["status"]:
                self.details_dialog.populate(result["value"])
            elif not result["ok"]:
                self.append_log(result["error"])
            return
        if action == "sync_status":
            self.sync_status_busy = False
            if result["ok"]:
                self.render_sync_status(result["value"])
            else:
                self.append_log(result["error"])
            return
        if action in ("start", "stop"):
            self.busy = False
            if result["ok"]:
                self.running = action == "start"
                if self.running:
                    self.fingerprint.setText(tr('主机指纹：{value0}', value0=result['value']))
            self.state_label.setText(tr('● 运行中 · {value0}:{value1}', value0=self.config.host, value1=self.config.port) if self.running else tr('● 已停止'))
            self.update_controls()
        if not result["ok"]:
            self.append_log(result["error"])
            QMessageBox.warning(self, tr('操作失败'), result["error"])
            self.closing = False
        if self.closing and action in ("start", "stop", "reload") and result["ok"]:
            if self.running:
                self.stop()
            else:
                self.close()

    def on_event(self, event, payload):
        if event == "connected":
            self.sessions[payload["id"]] = payload
            self.refresh_connections()
        elif event == "disconnected":
            self.sessions.pop(payload, None)
            self.refresh_connections()
        elif event == "log":
            self.append_log(payload)

    def request_sync_status(self):
        if not self.sync_status_busy and not self.busy and not self.closing:
            self.sync_status_busy = True
            self.bridge.submit("sync_status")

    def render_sync_status(self, status):
        roots = status["roots"]
        if roots:
            self.index_label.setText(tr("；").join(
                tr('{value0} · {value1} 项 · {value2}', value0=row['path'], value1=row['files'], value2=tr('已就绪') if row['ready'] else tr('初次扫描中'))
                + (f" · {row['error']}" if row['error'] else "") for row in roots))
        logging_status = status.get("logging", {})
        if logging_status.get("write_errors") or logging_status.get("dropped_records"):
            self.index_label.setText(self.index_label.text() + tr('；日志异常：写入错误 {value0}，队列丢弃 {value1}', value0=logging_status.get('write_errors', 0), value1=logging_status.get('dropped_records', 0)))
        self.task_table.setRowCount(len(status["users"]))
        for index, task in enumerate(status["users"]):
            def stamp(value):
                return datetime.fromtimestamp(value).strftime("%m-%d %H:%M:%S") if value else tr('尚未完成')
            values = [task['username'], task['userid'], stamp(task['start_time']) if task['start_time'] else tr('不限制'),
                      str(task['pending_count']), str(task['failed_count']), str(task['done_count'] + task['archived_count']),
                      str(task['processed']), stamp(task['last_sync'])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, task['userid'])
                self.task_table.setItem(index, col, item)

    def show_sync_details(self, status):
        row = self.task_table.currentRow()
        if row < 0 or not self.bridge.service.sync_api:
            return
        userid = self.task_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.details_dialog = SyncDetailsDialog(self, userid, status)
        self.details_dialog.show()
        self.details_dialog.load_next()

    def refresh_connections(self):
        selected = self.connection_table.currentRow()
        selected_id = self.connection_table.item(selected, 0).data(Qt.ItemDataRole.UserRole) if selected >= 0 else None
        self.connection_table.setRowCount(len(self.sessions))
        for row, (identifier, session) in enumerate(self.sessions.items()):
            seconds = int((datetime.now().astimezone() - datetime.fromisoformat(session["since"])).total_seconds())
            values = [session["username"], session["ip"], str(session["port"]), session["since"].replace("T", " "), f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}", session["root"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, identifier)
                self.connection_table.setItem(row, col, item)
            if identifier == selected_id:
                self.connection_table.selectRow(row)
        self.count_label.setText(tr('{value0} 个在线连接', value0=len(self.sessions)))

    def disconnect(self):
        row = self.connection_table.currentRow()
        if row >= 0:
            self.bridge.submit("disconnect", self.connection_table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def refresh_users(self):
        self.user_table.setRowCount(len(self.config.users))
        for row, user in enumerate(self.config.users):
            values = [user.username, tr('已启用') if user.enabled else tr('已停用'), user.root, tr("、").join(tr(LABELS[p]) for p in user.permissions) or tr('禁止访问'), str(len(user.rules)), user.userid, tr('手动') if user.confirmation == "manual" else tr('自动')]
            for col, value in enumerate(values):
                self.user_table.setItem(row, col, QTableWidgetItem(value))

    def change_user(self, row=None):
        if self.running or self.busy or self.load_error:
            return
        dialog = UserDialog(self, self.config.users[row] if row is not None else None)
        if dialog.exec():
            if any(u.username == dialog.result_user.username for i, u in enumerate(self.config.users) if i != row):
                QMessageBox.warning(self, tr('用户名重复'), tr('请使用不同的用户名'))
                return
            previous = copy.deepcopy(self.config.users)
            if row is None:
                self.config.users.append(dialog.result_user)
            else:
                self.config.users[row] = dialog.result_user
            if not self.save():
                self.config.users = previous
            self.refresh_users()

    def add_user(self):
        self.change_user()

    def edit_user(self):
        row = self.user_table.currentRow()
        if row >= 0:
            self.change_user(row)

    def toggle_user(self):
        row = self.user_table.currentRow()
        if row >= 0:
            self.config.users[row].enabled = not self.config.users[row].enabled
            if not self.save():
                self.config.users[row].enabled = not self.config.users[row].enabled
            self.refresh_users()

    def remove_user(self):
        row = self.user_table.currentRow()
        if row >= 0:
            user = self.config.users[row]
            if QMessageBox.question(self, tr('删除用户'), tr('删除账号 {value0}？共享目录和文件将保留。', value0=user.username)) == QMessageBox.StandardButton.Yes:
                self.config.users.pop(row)
                if not self.save():
                    self.config.users.insert(row, user)
                self.refresh_users()

    def closeEvent(self, event):
        if self.running or self.busy:
            event.ignore()
            self.closing = True
            if not self.busy:
                self.stop()
        else:
            self.timer.stop()
            self.sync_timer.stop()
            self.bridge.loop.call_soon_threadsafe(self.bridge.loop.stop)
            self.bridge.thread.join(timeout=3)
            event.accept()


# Backwards-compatible light stylesheet for embedded UI consumers.
STYLE = stylesheet(LIGHT)
