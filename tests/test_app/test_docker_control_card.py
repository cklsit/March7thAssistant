# coding:utf-8
"""Docker 控制设置卡片与连接校验。"""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='GUI 测试仅在 Windows 平台运行')

from qfluentwidgets import FluentIcon  # noqa: E402

from app.card import docker_control_card as module  # noqa: E402
from app.card.docker_control_card import (  # noqa: E402
    DockerControlCard, check_target, parse_target,
)
from app.common import docker_mode  # noqa: E402
from app.common.docker_control import DockerControlError  # noqa: E402


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def status(self):
        self.calls.append("status")
        if self.error:
            raise self.error
        return {"ok": True}


@pytest.fixture
def local_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(docker_mode, "_target", None)
    monkeypatch.setattr(docker_mode, "load_target", lambda: {"host": "", "port": 8787, "token": ""})
    return tmp_path


def test_card_shows_local_state(qtbot, local_mode):
    card = DockerControlCard(FluentIcon.CLOUD, "Docker 控制")
    qtbot.addWidget(card)
    assert card.localButton.isHidden()
    assert card.connectButton.text() == "配置并连接"


def test_card_shows_connected_state(qtbot, local_mode, monkeypatch):
    monkeypatch.setattr(docker_mode, "_target", {"host": "192.0.2.10", "port": 8787, "token": "tok"})
    card = DockerControlCard(FluentIcon.CLOUD, "Docker 控制")
    qtbot.addWidget(card)
    assert card.localButton.isHidden() is False
    assert "192.0.2.10:8787" in card.contentLabel.text()
    assert card.connectButton.text() == "连接其他 Docker"


@pytest.mark.parametrize("host_text,port_text,token,expected", [
    ("http://192.0.2.10:8787/api", "8787", "tok", ("192.0.2.10", 8787, "tok")),
    ("  ", "", "", ("", 8787, "")),
    ("192.0.2.10", "abc", " tok ", ("192.0.2.10", 8787, "tok")),
])
def test_parse_target(host_text, port_text, token, expected):
    assert parse_target(host_text, port_text, token) == expected


def test_check_target_requires_host_and_token():
    assert check_target("", 8787, "tok") == "请填写地址"
    assert check_target("192.0.2.10", 8787, "") == "请填写访问令牌"


def test_check_target_probes_remote():
    client = FakeClient()
    assert check_target("192.0.2.10", 8787, "tok", client_factory=lambda *a, **k: client) is None
    assert client.calls == ["status"]


def test_check_target_reports_error():
    client = FakeClient(error=DockerControlError("令牌无效"))
    assert check_target("192.0.2.10", 8787, "bad", client_factory=lambda *a, **k: client) == "令牌无效"


def test_launch_new_window_builds_docker_args(monkeypatch, tmp_path):
    launched = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\App\March7th Launcher.exe")
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    module._launch_new_window("192.0.2.10", 8787, "tok")
    assert launched[0] == [r"C:\App\March7th Launcher.exe",
                           "--docker-host", "192.0.2.10", "--docker-port", "8787", "--docker-token", "tok"]


def test_launch_new_window_local_has_no_docker_args(monkeypatch, tmp_path):
    launched = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\App\March7th Launcher.exe")
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    module._launch_new_window()
    assert launched[0] == [r"C:\App\March7th Launcher.exe"]


class _LogStub:
    def __init__(self):
        self.logs = []

    def appendLog(self, text):
        self.logs.append(text)


@pytest.mark.parametrize("entry,expected", [
    ("main", "main"),
    ("daily", "daily"),
    ({"program": "self", "args": "power"}, "power"),
    ("app_update", None),
    ({"program": r"C:\tool.exe", "args": ""}, None),
    ("", None),
])
def test_docker_task_id_filtering(entry, expected):
    from app.log_interface import LogInterface

    stub = _LogStub()
    assert LogInterface._dockerTaskId(stub, entry) == expected
    if expected is None:
        assert stub.logs


class _MessageBoxProbe:
    """用假客户端验证：探活失败时 accept() 不关闭对话框。"""


def test_accept_blocks_on_unreachable_host(qtbot, local_mode, monkeypatch):
    from PySide6.QtWidgets import QWidget

    host = QWidget(); host.resize(900, 700); qtbot.addWidget(host)
    monkeypatch.setattr(module, "DockerControlClient",
                        lambda *a, **k: FakeClient(error=DockerControlError("连接失败")))
    dialog = module.DockerConnectDialog(host)
    qtbot.addWidget(dialog)
    dialog.hostEdit.setText("192.0.2.99")
    dialog.tokenEdit.setText("tok")
    dialog.show()

    dialog.accept()
    qtbot.wait(50)
    assert dialog._accepted is False, "探活失败时不得确认关闭对话框"
    assert dialog.errorLabel.text(), "探活失败必须就地显示错误原因"
    assert "连接失败" in dialog.errorLabel.text()
    dialog.close()


def test_accept_passes_when_reachable(qtbot, local_mode, monkeypatch):
    from PySide6.QtWidgets import QWidget

    host = QWidget(); host.resize(900, 700); qtbot.addWidget(host)
    client = FakeClient()
    monkeypatch.setattr(module, "DockerControlClient", lambda *a, **k: client)
    dialog = module.DockerConnectDialog(host)
    qtbot.addWidget(dialog)
    dialog.hostEdit.setText("192.0.2.10")
    dialog.tokenEdit.setText("tok")
    dialog.show()

    dialog.accept()
    qtbot.wait(50)
    assert dialog._accepted is True
    assert "status" in client.calls


def test_card_does_not_switch_when_probe_fails(qtbot, local_mode, monkeypatch):
    launched = []
    monkeypatch.setattr(module, "DockerControlClient",
                        lambda *a, **k: FakeClient(error=DockerControlError("地址不可达")))
    monkeypatch.setattr(module, "_launch_new_window", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(module.QApplication, "quit", lambda *a, **k: launched.append("quit"))

    card = DockerControlCard(FluentIcon.CLOUD, "Docker 控制")
    qtbot.addWidget(card)

    class _Dialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return True

        def values(self):
            return ("192.0.2.99", 8787, "tok")

    monkeypatch.setattr(module, "DockerConnectDialog", _Dialog)
    card._on_connect_clicked()
    assert launched == [], "探活失败不得新开窗口或退出本地界面"
