# coding:utf-8
"""设置里的「Docker 控制」：连接后关闭本地小助手，另开一个只操作 Docker 的窗口。

- 卡片显示当前状态：本机小助手 / 已连接某台 Docker；
- 对话框填地址、端口、令牌，连接前先探活，连不上不切模式；
- 进入 Docker 模式后本卡片提供「切回本地小助手」。
"""
import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton

from qfluentwidgets import (
    BodyLabel, InfoBar, InfoBarPosition, LineEdit, MessageBox, PasswordLineEdit, SettingCard,
)

from app.common import docker_mode
from app.common.docker_control import DockerControlClient, DockerControlError, normalize_host
from module.localization import tr


def _launch_new_window(docker_host=None, docker_port=8787, docker_token=""):
    """按当前程序位置新开一个窗口；frozen 时就是 Launcher.exe，源码时是 app.py。"""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, os.path.abspath("app.py")]
    if docker_host:
        command += ["--docker-host", docker_host,
                    "--docker-port", str(int(docker_port or 8787)),
                    "--docker-token", docker_token or ""]
    subprocess.Popen(command, cwd=os.getcwd(), close_fds=True)


def parse_target(host_text, port_text, token):
    """把界面输入规整成 (host, port, token)。"""
    host = normalize_host(host_text)
    try:
        port = int(str(port_text).strip() or 8787)
    except (TypeError, ValueError):
        port = 8787
    return host, port, (token or "").strip()


def check_target(host, port, token, client_factory=None):
    """连接前探活；返回错误消息，可用时返回 None。"""
    if not host:
        return tr("请填写地址")
    if not token:
        return tr("请填写访问令牌")
    factory = client_factory or DockerControlClient
    try:
        factory(host, port, token, timeout=4).status()
    except DockerControlError as error:
        return str(error)
    return None


class DockerConnectDialog(MessageBox):
    """填写 Docker 地址并探活；确认后由调用方负责重启。"""

    def __init__(self, parent=None):
        super().__init__(tr("连接 Docker 小助手"),
                         tr("连接后会关闭本地小助手，打开一个只显示并操作 Docker 的新窗口；"
                            "新窗口的标题栏会标注 Docker，配置与任务都以 Docker 上的为准。"),
                         parent)
        self.yesButton.setText(tr("连接"))
        self.cancelButton.setText(tr("取消"))
        self.buttonGroup.setMinimumWidth(480)

        saved = docker_mode.load_target()
        # 移除后必须清空并隐藏，否则旧文案会留在原位和新控件叠在一起
        self.textLayout.removeWidget(self.contentLabel)
        self.contentLabel.clear()
        self.contentLabel.hide()

        self.hostEdit = LineEdit(self)
        self.hostEdit.setPlaceholderText(tr("Docker 主机地址，例如 192.0.2.10"))
        self.hostEdit.setText(saved.get("host") or "")
        self.portEdit = LineEdit(self)
        self.portEdit.setPlaceholderText(tr("端口"))
        self.portEdit.setFixedWidth(100)
        self.portEdit.setText(str(saved.get("port") or 8787))
        self.tokenEdit = PasswordLineEdit(self)
        self.tokenEdit.setPlaceholderText(tr("访问令牌"))
        self.tokenEdit.setText(saved.get("token") or "")

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.hostEdit, 1)
        row.addWidget(self.portEdit, 0)
        row.addWidget(self.tokenEdit, 1)
        self.textLayout.addLayout(row)
        self.textLayout.addWidget(BodyLabel(tr("地址与令牌需要与 Docker 上的控制接口一致"), self))

    def values(self):
        return parse_target(self.hostEdit.text(), self.portEdit.text(), self.tokenEdit.text())

    def validate(self):
        host, port, token = self.values()
        error = check_target(host, port, token, getattr(self, "client_factory", None))
        if error is None:
            return True
        kind = InfoBar.warning if error in (tr("请填写地址"), tr("请填写访问令牌")) else InfoBar.error
        kind(tr("连接失败") if kind is InfoBar.error else tr("请补全连接信息"),
             error, parent=self, position=InfoBarPosition.TOP, duration=4000)
        return False


class DockerControlCard(SettingCard):
    """设置页的 Docker 控制入口。"""

    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent)
        self.connectButton = QPushButton(tr("配置并连接"), self)
        self.localButton = QPushButton(tr("切回本地小助手"), self)
        self.hBoxLayout.addWidget(self.connectButton, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(self.localButton, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.connectButton.clicked.connect(self._on_connect_clicked)
        self.localButton.clicked.connect(self._on_back_to_local_clicked)
        self.refresh_state()

    def refresh_state(self):
        state = docker_mode.active()
        if state:
            self.setContent(tr("已连接 Docker：{host}:{port}；标题栏标注 Docker 的窗口只操作远程")
                            .format(host=state["host"], port=state["port"]))
            self.connectButton.setText(tr("连接其他 Docker"))
            self.localButton.show()
        else:
            self.setContent(tr("填写 Docker 地址与令牌，连接后另开一个只操作远程的小助手窗口"))
            self.connectButton.setText(tr("配置并连接"))
            self.localButton.hide()

    def _on_connect_clicked(self):
        dialog = DockerConnectDialog(self.window())
        if not dialog.exec():
            return
        host, port, token = dialog.values()
        docker_mode.save_target(host, port, token)
        _launch_new_window(host, port, token)
        QApplication.quit()

    def _on_back_to_local_clicked(self):
        _launch_new_window()
        QApplication.quit()
