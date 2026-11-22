# coding:utf-8
"""桌面端 Docker 控制客户端：错误映射与地址归一化。"""
import pytest

from app.common.docker_control import (
    TOKEN_HEADER, DockerControlClient, DockerControlError, normalize_host,
)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_successful_status_request():
    session = _Session(_Response(200, {"version": "v1", "ok": True}))
    client = DockerControlClient("192.0.2.10", 8787, "tok", session=session)
    assert client.status()["version"] == "v1"
    call = session.calls[0]
    assert call["url"] == "http://192.0.2.10:8787/api/status"
    assert call["headers"][TOKEN_HEADER] == "tok"


def test_error_payload_becomes_exception():
    session = _Session(_Response(409, {"ok": False, "error": "已有任务在运行"}))
    client = DockerControlClient("host", 8787, "tok", session=session)
    with pytest.raises(DockerControlError, match="已有任务在运行"):
        client.run_main()


def test_invalid_token_message():
    session = _Session(_Response(401, {"ok": False, "error": "令牌无效"}))
    client = DockerControlClient("host", 8787, "bad", session=session)
    with pytest.raises(DockerControlError, match="令牌无效"):
        client.status()


def test_non_json_response():
    import requests

    session = _Session(_Response(200, ValueError("no json")))
    client = DockerControlClient("host", 8787, "tok", session=session)
    with pytest.raises(DockerControlError, match="JSON"):
        client.status()

    session = _Session(requests.RequestException("boom"))
    client = DockerControlClient("host", 8787, "tok", session=session)
    with pytest.raises(DockerControlError, match="连接 host:8787 失败"):
        client.status()


def test_update_config_payload():
    session = _Session(_Response(200, {"ok": True, "changed": {"power_enable": False}}))
    client = DockerControlClient("host", 8787, "tok", session=session)
    assert client.update_config({"power_enable": False})["changed"]["power_enable"] is False
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["json"] == {"power_enable": False}


@pytest.mark.parametrize("raw,expected", [
    ("192.0.2.10", "192.0.2.10"),
    ("  http://192.0.2.10:8787/api ", "192.0.2.10"),
    ("https://nas.local/", "nas.local"),
    ("", ""),
])
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected
