# coding:utf-8
"""远程控制页面：在桌面端查看并操作 Docker（NAS 容器）里的小助手。

页面只做三件事：
- 连接配置（地址 / 端口 / 令牌），保存在本机 QSettings，不写进 config.yaml；
- 展示调度器状态、当日处理标记与最近一次运行；
- 下发动作：立即运行完整运行、停止当前任务、修改常用配置开关。

服务端见 ``tools/docker_control/control_server.py``。
"""
from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon, InfoBar, InfoBarPosition,
    LineEdit, PasswordLineEdit, PrimaryPushButton, PushButton, ScrollArea, SpinBox,
    StrongBodyLabel, SubtitleLabel, SwitchButton, TextEdit, TitleLabel,
)

from module.localization import tr
from .common.docker_control import (
    DEFAULT_PORT, DockerControlClient, DockerControlError, normalize_host,
)

SETTINGS_ORG = "March7thAssistant"
SETTINGS_APP = "DockerControl"
REFRESH_INTERVAL_MS = 30000

BOOL_OPTIONS = (
    ("currencywars_enable", "货币战争积分奖励"),
    ("weekly_divergent_enable", "差分宇宙积分奖励"),
    ("power_enable", "清体力"),
    ("daily_enable", "每日实训"),
    ("reward_enable", "领取奖励"),
    ("echo_of_war_enable", "历战余响"),
)


class _RequestThread(QThread):
    """把一次 HTTP 请求放到线程里跑，避免卡住界面。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task

    def run(self):
        try:
            self.succeeded.emit(self._task())
        except DockerControlError as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - 线程里任何异常都要回报到界面
            self.failed.emit(f"请求失败：{error}")


class _Card(CardWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.addWidget(SubtitleLabel(title, self))


class DockerInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("dockerInterface")
        self._client = None
        self._thread = None
        self._switches = {}
        self._status_values = {}

        self.view = QWidget(self)
        self.view.setObjectName("dockerView")
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(36, 20, 36, 24)
        self.vBoxLayout.setSpacing(16)

        self._build_header()
        self._build_connection_card()
        self._build_status_card()
        self._build_action_card()
        self._build_config_card()
        self._build_log_card()

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._load_settings()
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(lambda: self.refresh_status(silent=True))
        self._timer.start()

    # ── 界面搭建 ─────────────────────────────────────────────────
    def _build_header(self):
        title = TitleLabel(tr("远程控制（Docker）"), self.view)
        hint = CaptionLabel(
            tr("控制 NAS 上运行的小助手：查看调度器状态、立即运行完整运行、停止当前任务、修改常用开关。"
               "需要容器开启控制接口并配置令牌。"),
            self.view,
        )
        hint.setWordWrap(True)
        self.vBoxLayout.addWidget(title)
        self.vBoxLayout.addWidget(hint)

    def _build_connection_card(self):
        card = _Card(tr("连接"), self.view)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.hostEdit = LineEdit(card)
        self.hostEdit.setPlaceholderText(tr("地址，例如 192.0.2.10"))
        self.hostEdit.setClearButtonEnabled(True)
        self.portEdit = LineEdit(card)
        self.portEdit.setPlaceholderText(tr("端口"))
        self.portEdit.setFixedWidth(100)
        self.tokenEdit = PasswordLineEdit(card)
        self.tokenEdit.setPlaceholderText(tr("访问令牌"))
        row.addWidget(self.hostEdit, 1)
        row.addWidget(self.portEdit, 0)
        row.addWidget(self.tokenEdit, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.connectButton = PrimaryPushButton(FluentIcon.LINK, tr("保存并连接"), card)
        self.refreshButton = PushButton(FluentIcon.SYNC, tr("刷新状态"), card)
        self.connectionLabel = CaptionLabel(tr("尚未连接"), card)
        self.connectButton.clicked.connect(self._on_connect_clicked)
        self.refreshButton.clicked.connect(lambda: self.refresh_status())
        buttons.addWidget(self.connectButton)
        buttons.addWidget(self.refreshButton)
        buttons.addWidget(self.connectionLabel, 1)

        card.vBoxLayout.addLayout(row)
        card.vBoxLayout.addLayout(buttons)
        self.vBoxLayout.addWidget(card)

    def _build_status_card(self):
        card = _Card(tr("调度器状态"), self.view)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        for row, (key, label) in enumerate((
            ("version", "版本"),
            ("scheduler", "调度器"),
            ("next_trigger", "下次触发"),
            ("active_run", "当前运行"),
            ("last_run", "最近一次运行"),
            ("today", "当日处理"),
            ("config_summary", "关键开关"),
        )):
            grid.addWidget(StrongBodyLabel(tr(label), card), row, 0)
            value = BodyLabel(tr("—"), card)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, 1)
            self._status_values[key] = value
        grid.setColumnStretch(1, 1)
        card.vBoxLayout.addLayout(grid)
        self.vBoxLayout.addWidget(card)

    def _build_action_card(self):
        card = _Card(tr("操作"), self.view)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.runButton = PrimaryPushButton(FluentIcon.PLAY, tr("立即运行完整运行"), card)
        self.stopButton = PushButton(FluentIcon.CANCEL, tr("停止当前运行"), card)
        self.runButton.clicked.connect(self._on_run_clicked)
        self.stopButton.clicked.connect(self._on_stop_clicked)
        row.addWidget(self.runButton)
        row.addWidget(self.stopButton)
        row.addStretch(1)
        hint = CaptionLabel(
            tr("立即运行会按当日触发点的流程在本机（Docker 内）执行完整运行；已有任务在运行时会被拒绝。"),
            card,
        )
        hint.setWordWrap(True)
        card.vBoxLayout.addLayout(row)
        card.vBoxLayout.addWidget(hint)
        self.vBoxLayout.addWidget(card)

    def _build_config_card(self):
        card = _Card(tr("配置开关"), self.view)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        for row, (key, label) in enumerate(BOOL_OPTIONS):
            grid.addWidget(BodyLabel(tr(label), card), row, 0)
            switch = SwitchButton(card)
            switch.setOnText(tr("开"))
            switch.setOffText(tr("关"))
            grid.addWidget(switch, row, 1, Qt.AlignmentFlag.AlignLeft)
            self._switches[key] = switch
        grid.addWidget(BodyLabel(tr("差分宇宙难度"), card), len(BOOL_OPTIONS), 0)
        self.levelSpin = SpinBox(card)
        self.levelSpin.setRange(1, 6)
        self.levelSpin.setValue(6)
        level_row = QHBoxLayout()
        level_row.addWidget(self.levelSpin)
        level_row.addWidget(CaptionLabel(tr("6 = 难度 X（星阶模式）"), card))
        level_row.addStretch(1)
        grid.addLayout(level_row, len(BOOL_OPTIONS), 1)
        grid.setColumnStretch(1, 1)
        card.vBoxLayout.addLayout(grid)

        row = QHBoxLayout()
        self.saveConfigButton = PushButton(FluentIcon.SAVE, tr("保存到 Docker"), card)
        self.saveConfigButton.clicked.connect(self._on_save_config_clicked)
        row.addWidget(self.saveConfigButton)
        row.addStretch(1)
        card.vBoxLayout.addLayout(row)
        self.vBoxLayout.addWidget(card)

    def _build_log_card(self):
        card = _Card(tr("消息"), self.view)
        self.logView = TextEdit(card)
        self.logView.setReadOnly(True)
        self.logView.setFixedHeight(150)
        card.vBoxLayout.addWidget(self.logView)
        self.vBoxLayout.addWidget(card)
        self.vBoxLayout.addStretch(1)

    # ── 设置读写 ─────────────────────────────────────────────────
    def _settings(self):
        return QSettings(SETTINGS_ORG, SETTINGS_APP)

    def _load_settings(self):
        store = self._settings()
        self.hostEdit.setText(str(store.value("host", "") or ""))
        self.portEdit.setText(str(store.value("port", DEFAULT_PORT) or DEFAULT_PORT))
        self.tokenEdit.setText(str(store.value("token", "") or ""))
        if self.hostEdit.text().strip():
            self.connectionLabel.setText(tr("已保存连接信息，点击「保存并连接」验证"))

    def _save_settings(self):
        store = self._settings()
        store.setValue("host", self.hostEdit.text().strip())
        store.setValue("port", self.portEdit.text().strip() or str(DEFAULT_PORT))
        store.setValue("token", self.tokenEdit.text().strip())

    def _make_client(self):
        host = normalize_host(self.hostEdit.text())
        try:
            port = int(self.portEdit.text().strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT
        token = self.tokenEdit.text().strip()
        if not host:
            raise DockerControlError(tr("请先填写 Docker 所在机器的地址"))
        return DockerControlClient(host=host, port=port, token=token)

    # ── 请求调度 ─────────────────────────────────────────────────
    def _busy(self):
        return self._thread is not None and self._thread.isRunning()

    def _start_request(self, task, on_success, on_failure=None):
        if self._busy():
            self.append_log(tr("上一个请求还没结束，稍后再试"))
            return
        thread = _RequestThread(task, self)
        thread.succeeded.connect(lambda result: self._finish_request(result, on_success))
        thread.failed.connect(lambda message: self._finish_request(None, None, message, on_failure))
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _finish_request(self, result, on_success, message=None, on_failure=None):
        self._thread = None
        if message is not None:
            self.append_log(message)
            if on_failure is not None:
                on_failure(message)
            return
        on_success(result)

    def _on_connect_clicked(self):
        self._save_settings()
        self.refresh_status(announce=True)

    def refresh_status(self, silent=False, announce=False):
        try:
            client = self._make_client()
        except DockerControlError as error:
            if not silent:
                self.append_log(str(error))
            return
        self._client = client
        self.refreshButton.setEnabled(False)
        self._start_request(
            client.status,
            lambda payload: self._apply_status(payload, silent=silent, announce=announce),
            lambda message: self._status_failed(message, silent=silent),
        )

    def _status_failed(self, message, silent=False):
        self.refreshButton.setEnabled(True)
        self.connectionLabel.setText(tr("连接失败"))
        if not silent:
            self._toast(tr("刷新失败"), message, success=False)

    def _apply_status(self, payload, silent=False, announce=False):
        self.refreshButton.setEnabled(True)
        self.connectionLabel.setText(tr("已连接：{host}:{port}").format(
            host=(self._client.host if self._client else ""),
            port=(self._client.port if self._client else ""),
        ))
        self._status_values["version"].setText(str(payload.get("version") or "—"))
        scheduler = payload.get("scheduler") or {}
        trigger = scheduler.get("trigger") or "—"
        self._status_values["scheduler"].setText(
            tr("每天 {time} 检查更新后运行完整运行").format(time=trigger)
            if scheduler.get("alive", True) else tr("调度器未响应")
        )
        self._status_values["next_trigger"].setText(str(payload.get("next_trigger") or "—"))
        self._status_values["active_run"].setText(self._describe_active(payload.get("active_run")))
        self._status_values["last_run"].setText(self._describe_last(payload.get("last_run")))
        self._status_values["today"].setText(self._describe_slot(payload.get("slot") or {}))
        self._status_values["config_summary"].setText(self._describe_config(payload.get("config") or {}))

        config = payload.get("config") or {}
        for key, switch in self._switches.items():
            if key in config and config[key] is not None:
                switch.setChecked(bool(config[key]))
        if config.get("weekly_divergent_level"):
            self.levelSpin.setValue(int(config["weekly_divergent_level"]))

        if announce:
            self._toast(tr("连接成功"), tr("已读取调度器状态"), success=True)
        if not silent:
            self.append_log(tr("已刷新状态（{time}）").format(time=payload.get("server_time", "")))

    def _on_run_clicked(self):
        if self._client is None:
            self._toast(tr("还没有连接"), tr("请先保存并连接"), success=False)
            return
        self.runButton.setEnabled(False)
        self._start_request(
            self._client.run_main,
            lambda _payload: self._after_action(tr("已提交立即运行"), refresh=True),
            lambda message: self._after_action(tr("立即运行失败：{error}").format(error=message), success=False),
        )

    def _on_stop_clicked(self):
        if self._client is None:
            self._toast(tr("还没有连接"), tr("请先保存并连接"), success=False)
            return
        self.stopButton.setEnabled(False)
        self._start_request(
            self._client.stop_run,
            lambda payload: self._after_action(
                tr("已下发停止指令：{run_id}").format(run_id=payload.get("stopped", "")), refresh=True),
            lambda message: self._after_action(tr("停止失败：{error}").format(error=message), success=False),
        )

    def _after_action(self, message, refresh=False, success=True):
        self.runButton.setEnabled(True)
        self.stopButton.setEnabled(True)
        self._toast(tr("操作完成") if success else tr("操作失败"), message, success=success)
        self.append_log(message)
        if refresh:
            QTimer.singleShot(1200, lambda: self.refresh_status(silent=True))

    def _on_save_config_clicked(self):
        if self._client is None:
            self._toast(tr("还没有连接"), tr("请先保存并连接"), success=False)
            return
        updates = {key: switch.isChecked() for key, switch in self._switches.items()}
        updates["weekly_divergent_level"] = int(self.levelSpin.value())
        self.saveConfigButton.setEnabled(False)
        self._start_request(
            lambda: self._client.update_config(updates),
            lambda payload: self._after_config_saved(payload),
            lambda message: self._after_config_saved(None, message),
        )

    def _after_config_saved(self, payload, message=None):
        self.saveConfigButton.setEnabled(True)
        if message is not None:
            self._toast(tr("保存失败"), message, success=False)
            self.append_log(message)
            return
        changed = (payload or {}).get("changed") or {}
        text = tr("已保存：{items}").format(items=", ".join(sorted(changed)) or tr("无变化"))
        self._toast(tr("保存成功"), text, success=True)
        self.append_log(text)
        self.refresh_status(silent=True)

    # ── 展示辅助 ─────────────────────────────────────────────────
    def _describe_active(self, active):
        if not active:
            return tr("空闲")
        return tr("{task} 运行中（{run_id}，开始于 {started}）").format(
            task=active.get("task") or "?",
            run_id=active.get("run_id") or "?",
            started=active.get("started_at") or "?",
        )

    def _describe_last(self, last):
        if not last:
            return tr("暂无记录")
        return tr("{task} / {status} / 退出码 {code} / {ended}").format(
            task=last.get("task") or "?",
            status=last.get("status") or "?",
            code=last.get("exit_code"),
            ended=last.get("ended_at") or "?",
        )

    def _describe_slot(self, slot):
        key = slot.get("key")
        if not key:
            return tr("未到触发点")
        reason = slot.get("done_reason") or tr("未处理")
        update = slot.get("update") or ""
        text = tr("{key}：{reason}").format(key=key, reason=reason)
        if update:
            text += tr("（更新检查：{result}）").format(result=update)
        return text

    def _describe_config(self, config):
        parts = []
        for key, label in BOOL_OPTIONS:
            if key in config and config[key] is not None:
                parts.append(f"{tr(label)}={'on' if config[key] else 'off'}")
        if config.get("weekly_divergent_level"):
            parts.append(tr("差分宇宙难度={level}").format(level=config["weekly_divergent_level"]))
        return "，".join(parts) or "—"

    def append_log(self, text):
        lines = self.logView.toPlainText().splitlines()
        lines.append(str(text))
        if len(lines) > 200:
            lines = lines[-200:]
        self.logView.setPlainText("\n".join(lines))

    def _toast(self, title, content, success=True):
        target = self.window() or self
        if success:
            InfoBar.success(title, content, duration=3000,
                            position=InfoBarPosition.TOP_RIGHT, parent=target)
        else:
            InfoBar.error(title, content, duration=4000,
                          position=InfoBarPosition.TOP_RIGHT, parent=target)
