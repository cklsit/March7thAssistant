# coding:utf-8
"""Docker 模式：桌面端只显示、只操作 Docker 里的那台小助手。

工作方式：
- 启动参数带 ``--docker-host/--docker-port/--docker-token`` 时进入 Docker 模式；
- 配置不再用本机 ``config.yaml``，改成 ``docker_profile/config.yaml`` 的镜像文件，
  启动时从远程拉一份，此后本地每次保存都整份推回远程（运行中远程会拒绝）；
- 定时把远程配置拉回来，变了就覆盖镜像文件，主界面的配置监听会像"外部修改"一样刷新页面；
- 任务不再在本机跑，统一交给远程 ``/api/run``，日志与状态从远程读。

这里只做状态与网络同步，不碰 Qt；界面层通过 ``active()`` 判断模式。
"""
import json
import os
import threading

from .docker_control import DockerControlClient, DockerControlError

PROFILE_DIR = "docker_profile"
CONFIG_PATH = os.path.join(PROFILE_DIR, "config.yaml")
STATE_PATH = os.path.join(PROFILE_DIR, "target.json")
SYNC_ATTEMPTS = 3
# 允许在 Docker 模式里触发的任务（与容器控制接口的白名单保持一致）
REMOTE_TASKS = frozenset({
    "main", "routine", "daily", "power", "currencywars", "divergent",
    "fight", "universe", "forgottenhall", "purefiction", "apocalyptic", "redemption",
})
REMOTE_TASK_NAMES = {
    "main": "完整运行",
    "routine": "日常",
    "daily": "每日实训",
    "power": "清体力",
    "currencywars": "货币战争",
    "divergent": "差分宇宙",
    "fight": "锄大地",
    "universe": "模拟宇宙",
    "forgottenhall": "混沌回忆",
    "purefiction": "虚构叙事",
    "apocalyptic": "末日幻影",
    "redemption": "兑换码",
}

_lock = threading.RLock()
_target = None
last_error = None
sync_failed = False


def program_dir():
    return os.getcwd()


def mirror_config_path():
    return os.path.abspath(CONFIG_PATH)


def state_path():
    return os.path.abspath(STATE_PATH)


def ensure_profile():
    os.makedirs(os.path.join(program_dir(), PROFILE_DIR), exist_ok=True)


def activate(host, port, token):
    """进入 Docker 模式；必须在导入 module.config 之前调用。"""
    global _target, last_error, sync_failed
    with _lock:
        _target = {
            "host": (host or "").strip(),
            "port": int(port or 8787),
            "token": (token or "").strip(),
        }
        last_error = None
        sync_failed = False
        ensure_profile()
        os.environ["MARCH7TH_CONFIG_PATH"] = mirror_config_path()
        return dict(_target)


def active():
    return _target


def client():
    if _target is None:
        raise DockerControlError("当前不是 Docker 模式")
    return DockerControlClient(_target["host"], _target["port"], _target["token"])


def save_target(host, port, token):
    """记住上次填写的地址，供设置对话框预填；不改变当前模式。"""
    ensure_profile()
    try:
        with open(state_path(), "w", encoding="utf-8") as handle:
            json.dump({"host": host, "port": int(port or 8787), "token": token or ""}, handle,
                      ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_target():
    try:
        with open(state_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return {
            "host": str(data.get("host") or ""),
            "port": int(data.get("port") or 8787),
            "token": str(data.get("token") or ""),
        }
    except (OSError, ValueError, TypeError):
        return {"host": "", "port": 8787, "token": ""}


def pull_config():
    """从远程拉整份配置写入镜像文件；返回是否成功。"""
    global last_error, sync_failed
    ensure_profile()
    try:
        data = client().config_file()
        text = data.get("text") or ""
        if not text.strip():
            raise DockerControlError("远程返回的配置为空")
        with _lock:
            with open(mirror_config_path(), "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        sync_failed = False
        last_error = None
        return True
    except (DockerControlError, OSError) as error:
        sync_failed = True
        last_error = str(error)
        return False


def push_config():
    """把镜像文件整份推回远程；返回错误消息，成功返回 None。"""
    global last_error, sync_failed
    try:
        with open(mirror_config_path(), encoding="utf-8", newline="") as handle:
            text = handle.read()
        client().replace_config_file(text)
        sync_failed = False
        last_error = None
        return None
    except (DockerControlError, OSError) as error:
        sync_failed = True
        last_error = str(error)
        return str(error)


def on_local_config_saved():
    """Config.save_config 的钩子：Docker 模式把改动推回远程。"""
    if _target is None:
        return
    error = push_config()
    if error:
        from module.logger import log
        log.warning(f"Docker 模式：配置同步到远程失败：{error}")


def start_remote_task(task_id):
    """请求远程运行指定任务；返回错误消息，成功返回 None。"""
    try:
        client().run_task(task_id)
        return None
    except DockerControlError as error:
        return str(error)
