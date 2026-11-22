# coding:utf-8
"""Docker 侧控制接口的桌面端客户端。

与 ``tools/docker_control/control_server.py`` 配套：容器里的调度器起 HTTP 服务，
这里负责带令牌发请求并把响应翻成异常或字典。只依赖 ``requests``（项目已有）。
"""
import re

import requests

DEFAULT_PORT = 8787
DEFAULT_TIMEOUT = 8
TOKEN_HEADER = "X-Control-Token"


class DockerControlError(RuntimeError):
    """请求失败：网络不通、令牌不对、接口返回错误。"""


class DockerControlClient:
    def __init__(self, host, port=DEFAULT_PORT, token="", session=None, timeout=DEFAULT_TIMEOUT):
        self.host = (host or "").strip()
        self.port = int(port or DEFAULT_PORT)
        self.token = (token or "").strip()
        self.timeout = timeout
        self._session = session or requests.Session()

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}"

    def _request(self, method, path, payload=None):
        url = self.base_url + path
        try:
            response = self._session.request(
                method, url,
                headers={TOKEN_HEADER: self.token},
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise DockerControlError(f"连接 {self.host}:{self.port} 失败：{error}") from error

        try:
            data = response.json()
        except ValueError as error:
            raise DockerControlError(f"接口返回的不是 JSON（HTTP {response.status_code}）") from error
        if not isinstance(data, dict):
            raise DockerControlError("接口返回格式不正确")
        if response.status_code >= 400 or data.get("ok") is False:
            raise DockerControlError(str(data.get("error") or f"HTTP {response.status_code}"))
        return data

    def status(self):
        return self._request("GET", "/api/status")

    def read_config(self):
        return self._request("GET", "/api/config")

    def update_config(self, values):
        return self._request("POST", "/api/config", payload=dict(values))

    def run_main(self):
        return self._request("POST", "/api/run", payload={})

    def stop_run(self):
        return self._request("POST", "/api/stop", payload={})


def normalize_host(text):
    """容忍用户粘贴 http:// 前缀、端口与多余路径。"""
    value = (text or "").strip()
    value = re.sub(r"^https?://", "", value, flags=re.I)
    value = value.split("/", 1)[0]
    if ":" in value:
        value = value.split(":", 1)[0]
    return value
