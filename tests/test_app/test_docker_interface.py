# coding:utf-8
"""远程控制页面：状态渲染、动作下发、配置保存与失败提示。"""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='GUI 测试仅在 Windows 平台运行')

from app.docker_interface import DockerInterface  # noqa: E402
from app.common.docker_control import DockerControlError  # noqa: E402

STATUS = {
    "ok": True,
    "server_time": "2026-11-22T16:00:00+08:00",
    "version": "v2026.11.20",
    "scheduler": {"alive": True, "trigger": "04:00", "window_hours": 9},
    "next_trigger": "2026-11-23T04:00:00",
    "active_run": {"run_id": "20261122-040000-abcd", "task": "main", "started_at": "2026-11-22T04:00:00"},
    "last_run": {"task": "main", "status": "success", "exit_code": 0, "ended_at": "2026-11-22T04:41:00"},
    "slot": {"key": "20261122-0400", "done": True, "done_reason": "executed: main_exit=0", "update": "none"},
    "config": {
        "currencywars_enable": True,
        "weekly_divergent_enable": False,
        "power_enable": True,
        "daily_enable": True,
        "reward_enable": True,
        "echo_of_war_enable": False,
        "weekly_divergent_level": 6,
    },
}


class FakeStore:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value


class FakeClient:
    def __init__(self, status=None, error=None):
        self.host = "192.0.2.10"
        self.port = 8787
        self.calls = []
        self._status = status or STATUS
        self._error = error

    def status(self):
        self.calls.append("status")
        if self._error:
            raise self._error
        return self._status

    def run_main(self):
        self.calls.append("run")
        return {"ok": True, "accepted": True}

    def stop_run(self):
        self.calls.append("stop")
        return {"ok": True, "stopped": "20261122-040000-abcd"}

    def update_config(self, values):
        self.calls.append(("config", dict(values)))
        return {"ok": True, "changed": dict(values), "config": self._status["config"]}


@pytest.fixture
def make_page(qtbot, monkeypatch):
    monkeypatch.setattr(DockerInterface, '_settings', lambda self: FakeStore())

    def build(client):
        page = DockerInterface()
        qtbot.addWidget(page)
        page.resize(980, 720)
        page._make_client = lambda: client
        return page

    return build


def _wait_idle(qtbot, page):
    qtbot.waitUntil(lambda: page._thread is None, timeout=8000)


def test_status_is_rendered(make_page, qtbot):
    client = FakeClient()
    page = make_page(client)
    page.hostEdit.setText("192.0.2.10")
    page.portEdit.setText("8787")
    page.tokenEdit.setText("token")
    page.connectButton.click()
    _wait_idle(qtbot, page)

    assert "status" in client.calls
    assert page._status_values["version"].text() == "v2026.11.20"
    assert page._status_values["next_trigger"].text() == "2026-11-23T04:00:00"
    assert "main" in page._status_values["active_run"].text()
    assert "success" in page._status_values["last_run"].text()
    assert "20261122-0400" in page._status_values["today"].text()
    assert page._switches["currencywars_enable"].isChecked() is True
    assert page._switches["weekly_divergent_enable"].isChecked() is False
    assert page.levelSpin.value() == 6
    assert "已连接" in page.connectionLabel.text()


def test_run_and_stop_buttons(make_page, qtbot):
    client = FakeClient()
    page = make_page(client)
    page._client = client
    page.runButton.click()
    _wait_idle(qtbot, page)
    assert "run" in client.calls

    page.stopButton.click()
    _wait_idle(qtbot, page)
    assert "stop" in client.calls
    assert "已下发停止指令" in page.logView.toPlainText()


def test_save_config_sends_switch_states(make_page, qtbot):
    client = FakeClient()
    page = make_page(client)
    page._client = client
    page._switches["weekly_divergent_enable"].setChecked(True)
    page._switches["echo_of_war_enable"].setChecked(True)
    page.levelSpin.setValue(5)
    page.saveConfigButton.click()
    _wait_idle(qtbot, page)

    payload = [call[1] for call in client.calls if isinstance(call, tuple)][0]
    assert payload["weekly_divergent_enable"] is True
    assert payload["echo_of_war_enable"] is True
    assert payload["weekly_divergent_level"] == 5
    assert "已保存" in page.logView.toPlainText()


def test_status_failure_is_reported(make_page, qtbot):
    client = FakeClient(error=DockerControlError("令牌无效"))
    page = make_page(client)
    page.connectButton.click()
    _wait_idle(qtbot, page)

    assert page.connectionLabel.text() == "连接失败"
    assert "令牌无效" in page.logView.toPlainText()


def test_missing_host_reports_without_request(qtbot, monkeypatch):
    monkeypatch.setattr(DockerInterface, '_settings', lambda self: FakeStore())
    page = DockerInterface()
    qtbot.addWidget(page)
    client = FakeClient()
    page._client = client
    page.hostEdit.setText("")
    page.refresh_status()
    assert client.calls == []
    assert "地址" in page.logView.toPlainText()


def test_connection_settings_persist(qtbot, monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(DockerInterface, '_settings', lambda self: store)
    page = DockerInterface()
    qtbot.addWidget(page)
    page.hostEdit.setText("192.0.2.10")
    page.portEdit.setText("9000")
    page.tokenEdit.setText("secret")
    page._save_settings()
    assert store.data == {"host": "192.0.2.10", "port": "9000", "token": "secret"}
