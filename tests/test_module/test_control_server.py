# coding:utf-8
"""测试容器侧控制接口：配置改写、令牌校验、状态与动作路由。"""
import json
import threading
import urllib.error
import urllib.request

import pytest

from tools.docker_control import control_server
from tools.docker_control.control_server import ConfigFile, ControlServer

TOKEN = "test-token"
CONFIG_TEXT = """# 示例配置
currencywars_enable: true  # 是否启用「货币战争」积分奖励。true 开启，false 关闭。
weekly_divergent_enable: false # 差分宇宙
weekly_divergent_level: 5 # 难度
power_enable: true
other_key: keep
"""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


def test_config_read_and_write_keeps_comments(config_path):
    config = ConfigFile(str(config_path), log=lambda *_: None)
    values = config.read()
    assert values["currencywars_enable"] is True
    assert values["weekly_divergent_enable"] is False
    assert values["weekly_divergent_level"] == 5
    assert "other_key" not in values

    changed = config.write({"currencywars_enable": False, "weekly_divergent_level": 6})
    assert changed == {"currencywars_enable": False, "weekly_divergent_level": 6}
    text = config_path.read_text(encoding="utf-8")
    assert "currencywars_enable: false  # 是否启用「货币战争」积分奖励。true 开启，false 关闭。" in text
    assert "weekly_divergent_level: 6 # 难度" in text
    assert "other_key: keep" in text
    assert config_path.with_suffix(".yaml.bak-control").exists()


@pytest.mark.parametrize("updates", [
    {"weekly_divergent_level": 7},
    {"currencywars_enable": "maybe"},
    {"model_path": "x"},
])
def test_config_rejects_invalid_updates(config_path, updates):
    config = ConfigFile(str(config_path), log=lambda *_: None)
    with pytest.raises(ValueError):
        config.write(updates)
    assert config_path.read_text(encoding="utf-8") == CONFIG_TEXT


class _State:
    def __init__(self):
        self.active = None
        self.triggered = 0
        self.stopped = []

    def run_active(self):
        return self.active is not None

    def trigger(self):
        self.triggered += 1
        return True

    def stop(self):
        run_id = self.active
        self.active = None
        self.stopped.append(run_id)
        return run_id

    def status(self):
        return {
            "version": "v-test",
            "scheduler": {"alive": True, "trigger": "04:00"},
            "next_trigger": "2026-11-23T04:00:00",
            "active_run": None if self.active is None else {"run_id": self.active, "task": "main"},
            "last_run": {"task": "main", "status": "success", "exit_code": 0},
            "slot": {"key": "20261122-0400", "done": True, "done_reason": "executed: main_exit=0"},
        }


@pytest.fixture
def server(config_path):
    state = _State()
    instance = ControlServer(
        token=TOKEN,
        status_provider=state.status,
        trigger_callback=state.trigger,
        stop_callback=state.stop,
        config_path=str(config_path),
        run_active=state.run_active,
        host="127.0.0.1",
        port=0,
        log=lambda *_: None,
    )
    assert instance.start()
    instance._state = state
    yield instance
    instance.stop()


def _call(server, path, method="GET", payload=None, token=TOKEN):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        request.add_header(control_server.TOKEN_HEADER, token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_status_requires_token(server):
    status, payload = _call(server, "/api/status", token=None)
    assert status == 401
    assert payload["ok"] is False


def test_status_payload(server):
    status, payload = _call(server, "/api/status")
    assert status == 200
    assert payload["version"] == "v-test"
    assert payload["config"]["currencywars_enable"] is True
    assert payload["server_time"]


def test_run_triggers_once_and_respects_active_run(server):
    status, payload = _call(server, "/api/run", method="POST", payload={})
    assert status == 200 and payload["accepted"] is True
    assert server._state.triggered == 1
    assert server.take_trigger() is True
    assert server.take_trigger() is False

    server._state.active = "20261122-040000-abcd"
    status, payload = _call(server, "/api/run", method="POST", payload={})
    assert status == 409


def test_stop_writes_only_when_running(server):
    status, payload = _call(server, "/api/stop", method="POST", payload={})
    assert status == 404

    server._state.active = "20261122-040000-abcd"
    status, payload = _call(server, "/api/stop", method="POST", payload={})
    assert status == 200 and payload["stopped"] == "20261122-040000-abcd"


def test_config_endpoint_round_trip(server, config_path):
    status, payload = _call(server, "/api/config", method="POST",
                            payload={"weekly_divergent_enable": True, "weekly_divergent_level": 6})
    assert status == 200
    assert payload["changed"]["weekly_divergent_enable"] is True
    assert payload["config"]["weekly_divergent_enable"] is True
    assert "weekly_divergent_enable: true" in config_path.read_text(encoding="utf-8")

    status, payload = _call(server, "/api/config", method="POST", payload={"weekly_divergent_level": 9})
    assert status == 400
    assert "介于" in payload["error"]


def test_config_write_refused_while_running(server):
    server._state.active = "20261122-040000-abcd"
    status, payload = _call(server, "/api/config", method="POST", payload={"power_enable": False})
    assert status == 409


def test_server_disabled_without_token(config_path):
    instance = ControlServer(
        token="",
        status_provider=lambda: {},
        trigger_callback=None,
        stop_callback=None,
        config_path=str(config_path),
        run_active=lambda: False,
        host="127.0.0.1",
        port=0,
        log=lambda *_: None,
    )
    assert instance.start() is False


def test_server_from_env_requires_token(monkeypatch, config_path):
    monkeypatch.delenv("M7A_CONTROL_TOKEN", raising=False)
    assert control_server.server_from_env(
        status_provider=lambda: {}, trigger_callback=None, stop_callback=None,
        config_path=str(config_path), run_active=lambda: False, log=lambda *_: None,
    ) is None
    monkeypatch.setenv("M7A_CONTROL_TOKEN", "abc")
    monkeypatch.setenv("M7A_CONTROL_ENABLE", "0")
    assert control_server.server_from_env(
        status_provider=lambda: {}, trigger_callback=None, stop_callback=None,
        config_path=str(config_path), run_active=lambda: False, log=lambda *_: None,
    ) is None


def test_threaded_server_serves_multiple_requests(server):
    results = []

    def worker():
        results.append(_call(server, "/api/status")[0])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200, 200, 200, 200]
