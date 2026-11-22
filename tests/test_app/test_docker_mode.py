# coding:utf-8
"""Docker 模式：配置镜像、目标保存、远程同步与任务白名单。"""
import os

import pytest

from app.common import docker_mode
from app.common.docker_control import DockerControlError


class FakeClient:
    def __init__(self, text="a: 1\n", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def config_file(self):
        self.calls.append("config_file")
        if self.error:
            raise self.error
        return {"ok": True, "text": self.text}

    def replace_config_file(self, text):
        self.calls.append(("replace", text))
        if self.error:
            raise self.error
        return {"ok": True}

    def run_task(self, task_id="main"):
        self.calls.append(("run", task_id))
        if self.error:
            raise self.error
        return {"ok": True, "task": task_id}


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(docker_mode, "_target", None)
    monkeypatch.setattr(docker_mode, "last_error", None)
    monkeypatch.setattr(docker_mode, "sync_failed", False)
    monkeypatch.delenv("MARCH7TH_CONFIG_PATH", raising=False)
    return tmp_path


def test_activate_sets_profile_config_and_env(workdir):
    target = docker_mode.activate(" 192.0.2.10 ", 8787, "tok")
    assert target == {"host": "192.0.2.10", "port": 8787, "token": "tok"}
    assert docker_mode.active() == target
    assert os.environ["MARCH7TH_CONFIG_PATH"] == docker_mode.mirror_config_path()
    assert docker_mode.mirror_config_path().endswith(os.path.join("docker_profile", "config.yaml"))


def test_save_and_load_target(workdir):
    docker_mode.save_target("192.0.2.10", 9000, "secret")
    assert docker_mode.load_target() == {"host": "192.0.2.10", "port": 9000, "token": "secret"}
    assert os.path.exists(docker_mode.state_path())


def test_load_target_defaults(workdir):
    assert docker_mode.load_target() == {"host": "", "port": 8787, "token": ""}


def test_pull_config_writes_mirror(workdir, monkeypatch):
    docker_mode.activate("host", 8787, "tok")
    monkeypatch.setattr(docker_mode, "client", lambda: FakeClient(text="version: 'x'\n"))
    assert docker_mode.pull_config() is True
    with open(docker_mode.mirror_config_path(), encoding="utf-8") as handle:
        assert handle.read() == "version: 'x'\n"


def test_pull_config_failure_keeps_error(workdir, monkeypatch):
    docker_mode.activate("host", 8787, "tok")
    monkeypatch.setattr(docker_mode, "client", lambda: FakeClient(error=DockerControlError("连不上")))
    assert docker_mode.pull_config() is False
    assert docker_mode.sync_failed is True
    assert "连不上" in docker_mode.last_error


def test_push_config_sends_mirror(workdir, monkeypatch):
    docker_mode.activate("host", 8787, "tok")
    docker_mode.ensure_profile()
    with open(docker_mode.mirror_config_path(), "w", encoding="utf-8", newline="") as handle:
        handle.write("power_enable: false\n")
    client = FakeClient()
    monkeypatch.setattr(docker_mode, "client", lambda: client)
    assert docker_mode.push_config() is None
    assert ("replace", "power_enable: false\n") in client.calls


def test_push_config_reports_error(workdir, monkeypatch):
    docker_mode.activate("host", 8787, "tok")
    docker_mode.ensure_profile()
    with open(docker_mode.mirror_config_path(), "w", encoding="utf-8") as handle:
        handle.write("a: 1\n")
    monkeypatch.setattr(docker_mode, "client", lambda: FakeClient(error=DockerControlError("运行中")))
    assert "运行中" in docker_mode.push_config()


def test_on_local_config_saved_ignores_local_mode(workdir, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(docker_mode, "client", lambda: client)
    docker_mode.on_local_config_saved()
    assert client.calls == []


def test_remote_task_whitelist():
    assert "main" in docker_mode.REMOTE_TASKS
    assert docker_mode.REMOTE_TASK_NAMES["main"] == "完整运行"
    assert "app_update" not in docker_mode.REMOTE_TASKS


def test_start_remote_task_reports_error(workdir, monkeypatch):
    docker_mode.activate("host", 8787, "tok")
    monkeypatch.setattr(docker_mode, "client", lambda: FakeClient(error=DockerControlError("忙")))
    assert docker_mode.start_remote_task("main") == "忙"
