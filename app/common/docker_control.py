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
            raise DockerControlError(self._describe_connection_error(error)) from error

        try:
            data = response.json()
        except ValueError as error:
            raise DockerControlError(f"接口返回的不是 JSON（HTTP {response.status_code}）") from error
        if not isinstance(data, dict):
            raise DockerControlError("接口返回格式不正确")
        if response.status_code >= 400 or data.get("ok") is False:
            raise DockerControlError(str(data.get("error") or f"HTTP {response.status_code}"))
        return data

    def _describe_connection_error(self, error):
        """把底层网络异常翻成可读提示：界面里不展示 requests 的内部细节。"""
        _ = error
        target = f"{self.host}:{self.port}"
        if isinstance(error, requests.Timeout):
            return f"连接 {target} 超时，请确认地址可达且端口正确"
        if isinstance(error, requests.ConnectionError):
            return f"无法连接 {target}，请确认 Docker 小助手已开启控制接口"
        return f"连接 {target} 失败，请检查地址、端口与令牌"

    def status(self):
        return self._request("GET", "/api/status")

    def read_config(self):
        return self._request("GET", "/api/config")

    def update_config(self, values):
        return self._request("POST", "/api/config", payload=dict(values))

    def run_main(self):
        return self._request("POST", "/api/run", payload={})

    def run_task(self, task_id="main"):
        return self._request("POST", "/api/run", payload={"task": task_id})

    def stop_run(self):
        return self._request("POST", "/api/stop", payload={})

    def config_file(self):
        return self._request("GET", "/api/config_file")

    def replace_config_file(self, text):
        return self._request("POST", "/api/config_file", payload={"text": text})

    def run_log(self, tail=8000):
        return self._request("GET", f"/api/run_log?tail={int(tail)}")


def normalize_host(text):
    """容忍用户粘贴 http:// 前缀、端口与多余路径。"""
    value = (text or "").strip()
    value = re.sub(r"^https?://", "", value, flags=re.I)
    value = value.split("/", 1)[0]
    if ":" in value:
        value = value.split(":", 1)[0]
    return value
