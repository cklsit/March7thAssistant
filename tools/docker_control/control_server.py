# coding:utf-8
"""三月七小助手 Docker 控制接口（仅标准库）。

在容器内的调度器进程里起一个 HTTP 服务，供桌面端 Docker 模式使用：

- ``GET  /api/status``      调度器与运行状态、当日标记、最近一次运行、版本
- ``GET  /api/config``      白名单配置项及其取值
- ``POST /api/run``         立即触发一次任务（默认完整运行）
- ``POST /api/stop``        停止当前正在运行的任务
- ``POST /api/config``      修改白名单配置项（保留注释、原地写入）
- ``GET  /api/config_file`` 读取整份 config.yaml（Docker 模式镜像配置用）
- ``POST /api/config_file`` 用整份文本覆盖 config.yaml（校验后可解析、写前备份）
- ``GET  /api/run_log``     当前/最近一次运行的日志尾部

认证：请求头 ``X-Control-Token``（也接受 ``?token=``），需与进程环境变量
``M7A_CONTROL_TOKEN`` 一致。没有令牌时服务不启动，避免误暴露。

这个模块刻意不导入项目内任何代码：它会被单独挂进容器，只依赖标准库。
"""
import datetime
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_HEADER = "X-Control-Token"
MAX_BODY = 1024 * 1024
RUN_LOG_TAIL = 8000
# 允许远程单键读写的配置项：只放布尔开关与一个有界的整数，避免误改到路径、账号等敏感项
CONFIG_BOOL_KEYS = (
    "currencywars_enable",
    "weekly_divergent_enable",
    "power_enable",
    "daily_enable",
    "reward_enable",
    "echo_of_war_enable",
)
CONFIG_INT_KEYS = {"weekly_divergent_level": (1, 6)}
CONFIG_KEYS = CONFIG_BOOL_KEYS + tuple(CONFIG_INT_KEYS)
# Docker 模式允许触发的任务（其余任务在远程没有意义）
REMOTE_TASKS = {
    "main", "routine", "daily", "power", "currencywars", "divergent",
    "fight", "universe", "forgottenhall", "purefiction", "apocalyptic", "redemption",
}


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "on"):
            return True
        if lowered in ("false", "no", "0", "off"):
            return False
    raise ValueError(f"不是合法的布尔值：{value!r}")


class ConfigFile:
    """按行改写 config.yaml：保留注释与其它键，写入前备份。"""

    def __init__(self, path, log=print):
        self.path = os.path.abspath(path)
        self.log = log
        self._lock = threading.Lock()

    def read(self):
        values = {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as error:
            self.log(f"读取配置失败：{error}")
            return values
        for key in CONFIG_KEYS:
            match = re.search(r"^" + re.escape(key) + r":\s*(.*)$", text, re.M)
            if not match:
                continue
            raw = match.group(1).split("#", 1)[0].strip()
            if key in CONFIG_INT_KEYS:
                try:
                    values[key] = int(raw)
                except (TypeError, ValueError):
                    values[key] = None
            else:
                try:
                    values[key] = _as_bool(raw)
                except ValueError:
                    values[key] = None
        return values

    def write(self, updates):
        """只接受白名单键；返回实际写入的键值。"""
        validated = {}
        for key, value in updates.items():
            if key in CONFIG_INT_KEYS:
                low, high = CONFIG_INT_KEYS[key]
                number = int(value)
                if not low <= number <= high:
                    raise ValueError(f"{key} 需要介于 {low} 和 {high} 之间")
                validated[key] = number
            elif key in CONFIG_BOOL_KEYS:
                validated[key] = _as_bool(value)
            else:
                raise ValueError(f"不支持修改配置项：{key}")
        if not validated:
            return {}

        with self._lock:
            with open(self.path, encoding="utf-8", newline="") as handle:
                text = handle.read()
            for key, value in validated.items():
                if isinstance(value, bool):
                    literal = "true" if value else "false"
                else:
                    literal = str(value)
                pattern = re.compile(r"^(" + re.escape(key) + r":)([^\n]*)$", re.M)
                match = pattern.search(text)
                if not match:
                    self.log(f"配置里没有 {key}，跳过")
                    validated.pop(key)
                    continue
                rest = match.group(2)
                comment = ""
                gap = ""
                hash_index = rest.find("#")
                if hash_index >= 0:
                    comment = rest[hash_index:]
                    before = rest[:hash_index]
                    gap = before[len(before.rstrip()):]
                line = f"{key}: {literal}{gap}{comment}"
                text = text[:match.start()] + line + text[match.end():]
            self._backup()
            try:
                with open(self.path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as error:
                raise RuntimeError(f"写入配置失败：{error}") from error
            self.log(f"配置已更新：{validated}")
        return validated

    def read_text(self):
        with open(self.path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def write_text(self, text):
        """整份覆盖：校验可解析且是映射，写前备份，原地写入。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("配置内容为空")
        if "\x00" in text:
            raise ValueError("配置内容含非法字符")
        self._validate_yaml(text)
        with self._lock:
            self._backup()
            try:
                with open(self.path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as error:
                raise RuntimeError(f"写入配置失败：{error}") from error
        self.log("整份配置已由桌面端覆盖写入")

    @staticmethod
    def _validate_yaml(text):
        try:
            from ruamel.yaml import YAML
        except Exception:
            # 容器里没有 YAML 库时退化为形状检查，避免把明显损坏的内容写进去
            if "\n" not in text or ":" not in text:
                raise ValueError("配置内容不像 YAML")
            return
        try:
            data = YAML(typ="safe").load(text)
        except Exception as error:
            raise ValueError(f"配置不是合法 YAML：{error}") from error
        if not isinstance(data, dict):
            raise ValueError("配置顶层不是映射")

    def _backup(self):
        try:
            with open(self.path, "rb") as source:
                data = source.read()
            with open(self.path + ".bak-control", "wb") as backup:
                backup.write(data)
        except OSError as error:
            self.log(f"配置备份失败（忽略）：{error}")


class ControlServer:
    """HTTP 控制服务；业务状态与动作全部由调度器通过回调提供。"""

    def __init__(self, *, token, status_provider, trigger_callback, stop_callback,
                 config_path, run_active, host="0.0.0.0", port=8787, log=print,
                 run_log_provider=None):
        self.token = (token or "").strip()
        self.status_provider = status_provider
        self.trigger_callback = trigger_callback
        self.stop_callback = stop_callback
        self.run_active = run_active
        self.run_log_provider = run_log_provider
        self.config = ConfigFile(config_path, log=log)
        self.host = host
        self.port = int(port)
        self.log = log
        self._trigger = threading.Event()
        self._trigger_task = None
        self._httpd = None
        self._thread = None

    # ── 供调度器使用 ──────────────────────────────────────────────
    def take_trigger(self):
        """取出一次「立即运行」请求并返回任务 id；没有请求时返回 None。"""
        if not self._trigger.is_set():
            return None
        self._trigger.clear()
        task = self._trigger_task
        self._trigger_task = None
        return task or "main"

    def pending_trigger(self):
        """是否有待处理的「立即运行」请求（不消费）。"""
        return self._trigger.is_set()

    def start(self):
        if not self.token:
            self.log("未设置 M7A_CONTROL_TOKEN，控制接口不启动")
            return False
        try:
            handler = self._build_handler()
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self._httpd.daemon_threads = True
            # 端口 0 时由内核分配，回读真实端口供测试与日志使用
            self.port = self._httpd.server_address[1]
            self._thread = threading.Thread(target=self._httpd.serve_forever, name="m7a-control", daemon=True)
            self._thread.start()
        except OSError as error:
            self.log(f"控制接口启动失败（{self.host}:{self.port}）：{error}")
            return False
        self.log(f"控制接口已启动：http://{self.host}:{self.port}/api/status")
        return True

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

    # ── 内部 ─────────────────────────────────────────────────────
    def _authorized(self, headers, query):
        supplied = headers.get(TOKEN_HEADER, "")
        if not supplied and query:
            supplied = query.get("token", [""])[0]
        return bool(supplied) and supplied == self.token

    def _status_payload(self):
        payload = {"server_time": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        try:
            payload.update(self.status_provider() or {})
        except Exception as error:  # 状态采集失败也要给出可读结果
            payload["status_error"] = str(error)
        payload["config"] = self.config.read()
        return payload

    def _perform_trigger(self, task="main"):
        if task not in REMOTE_TASKS:
            return 400, {"ok": False, "error": f"不支持远程触发的任务：{task}"}
        if self.run_active():
            return 409, {"ok": False, "error": "已有任务在运行"}
        if self.trigger_callback is not None and not bool(self.trigger_callback(task)):
            return 409, {"ok": False, "error": "调度器当前不可触发（可能在处理更新）"}
        # 由调度器主循环轮询 take_trigger() 消费；这里只做标记
        self._trigger_task = task
        self._trigger.set()
        return 200, {"ok": True, "accepted": True, "task": task}

    def _replace_config_file(self, text):
        if self.run_active():
            return 409, {"ok": False, "error": "任务运行中，不能覆盖配置"}
        try:
            self.config.write_text(text)
        except (ValueError, RuntimeError, OSError) as error:
            return 400, {"ok": False, "error": str(error)}
        return 200, {"ok": True}

    def _run_log(self, tail=RUN_LOG_TAIL):
        path = None
        if self.run_log_provider is not None:
            try:
                path = self.run_log_provider()
            except Exception as error:
                return 500, {"ok": False, "error": str(error)}
        if not path or not os.path.exists(path):
            return 200, {"ok": True, "path": path, "tail": ""}
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - int(tail))
                handle.seek(start)
                data = handle.read()
        except OSError as error:
            return 500, {"ok": False, "error": f"读取日志失败：{error}"}
        text = data.decode("utf-8", "replace")
        if start > 0:
            text = text.split("\n", 1)[-1] if "\n" in text else text
        return 200, {"ok": True, "path": path, "tail": text}

    def _perform_stop(self):
        if not self.run_active():
            return 404, {"ok": False, "error": "当前没有正在运行的任务"}
        if self.stop_callback is None:
            return 500, {"ok": False, "error": "调度器不支持停止"}
        run_id = self.stop_callback()
        if run_id is None:
            return 500, {"ok": False, "error": "停止指令下发失败"}
        return 200, {"ok": True, "stopped": run_id}

    def _update_config(self, updates):
        if self.run_active():
            return 409, {"ok": False, "error": "任务运行中，配置改动可能不生效"}
        try:
            changed = self.config.write(updates)
        except (ValueError, RuntimeError) as error:
            return 400, {"ok": False, "error": str(error)}
        return 200, {"ok": True, "changed": changed, "config": self.config.read()}

    def _build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "M7AControl/1.0"

            def log_message(self, fmt, *args):  # 不写访问日志，交给调度器日志
                return

            def _reply(self, status, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _reject(self):
                self._reply(401, {"ok": False, "error": "令牌无效"})

            def _read_json(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BODY:
                    return None
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return None

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 接口
                from urllib.parse import urlparse, parse_qs

                parsed = urlparse(self.path)
                if not server._authorized(self.headers, parse_qs(parsed.query)):
                    return self._reject()
                if parsed.path in ("/api/status", "/api/status/"):
                    return self._reply(200, server._status_payload())
                if parsed.path in ("/api/config", "/api/config/"):
                    return self._reply(200, {"ok": True, "config": server.config.read()})
                if parsed.path in ("/api/config_file", "/api/config_file/"):
                    try:
                        text = server.config.read_text()
                    except OSError as error:
                        return self._reply(500, {"ok": False, "error": f"读取配置失败：{error}"})
                    return self._reply(200, {"ok": True, "text": text})
                if parsed.path in ("/api/run_log", "/api/run_log/"):
                    query = parse_qs(parsed.query)
                    try:
                        tail = int(query.get("tail", [RUN_LOG_TAIL])[0])
                    except (TypeError, ValueError):
                        tail = RUN_LOG_TAIL
                    status, payload = server._run_log(tail)
                    return self._reply(status, payload)
                return self._reply(404, {"ok": False, "error": "未知接口"})

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 接口
                from urllib.parse import urlparse, parse_qs

                parsed = urlparse(self.path)
                if not server._authorized(self.headers, parse_qs(parsed.query)):
                    return self._reject()
                path = parsed.path.rstrip("/")
                if path == "/api/run":
                    body = self._read_json() or {}
                    task = body.get("task") or "main" if isinstance(body, dict) else "main"
                    status, payload = server._perform_trigger(task)
                elif path == "/api/stop":
                    status, payload = server._perform_stop()
                elif path == "/api/config":
                    updates = self._read_json()
                    if not isinstance(updates, dict):
                        return self._reply(400, {"ok": False, "error": "请求体需要是 JSON 对象"})
                    status, payload = server._update_config(updates)
                elif path == "/api/config_file":
                    body = self._read_json()
                    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                        return self._reply(400, {"ok": False, "error": "请求体需要包含 text"})
                    status, payload = server._replace_config_file(body["text"])
                else:
                    status, payload = 404, {"ok": False, "error": "未知接口"}
                return self._reply(status, payload)

        return Handler


def server_from_env(*, status_provider, trigger_callback, stop_callback, config_path,
                    run_active, run_log_provider=None, log=print):
    """按环境变量创建服务；控制接口未启用时返回 None。

    - ``M7A_CONTROL_TOKEN``：访问令牌，未设置则不启动
    - ``M7A_CONTROL_PORT``：监听端口，默认 8787
    - ``M7A_CONTROL_HOST``：监听地址，默认 0.0.0.0
    - ``M7A_CONTROL_ENABLE``：置 0/false 可整体关闭
    """
    if os.environ.get("M7A_CONTROL_ENABLE", "1").strip().lower() in ("0", "false", "no"):
        return None
    token = os.environ.get("M7A_CONTROL_TOKEN", "").strip()
    if not token:
        return None
    return ControlServer(
        token=token,
        host=os.environ.get("M7A_CONTROL_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=int(os.environ.get("M7A_CONTROL_PORT", "8787") or 8787),
        status_provider=status_provider,
        trigger_callback=trigger_callback,
        stop_callback=stop_callback,
        config_path=config_path,
        run_active=run_active,
        run_log_provider=run_log_provider,
        log=log,
    )
