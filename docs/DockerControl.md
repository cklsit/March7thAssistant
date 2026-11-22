# 远程控制（Docker）

桌面端「远程控制」页面用来操作跑在 NAS/服务器 Docker 里的小助手：查看调度器状态、
手动触发一次完整运行、停止正在跑的任务、改常用配置开关。适合 7×24 挂机的场景——
自动任务在容器里跑，你也可以随时在电脑上点一下。

## 组成

| 位置 | 文件 | 作用 |
|---|---|---|
| 容器内 | `control_server.py`（来自仓库 `tools/docker_control/control_server.py`） | 标准库实现的 HTTP 控制接口，由容器里的 `scheduler.py` 启动 |
| 容器内 | `scheduler.py` | 调度器主循环；启动控制接口、提供状态、消费「立即运行」请求 |
| 桌面端 | `app/docker_interface.py` | 「远程控制」页面 |
| 桌面端 | `app/common/docker_control.py` | 带令牌的 HTTP 客户端 |

桌面端只与本机的 `QSettings`（注册表）打交道保存地址、端口和令牌，不改 `config.yaml`；
容器侧的配置修改通过接口完成。

## 容器侧部署

1. 把 `tools/docker_control/control_server.py` 放到容器工程目录（例如 `/srv/m7a/`），
   与 `scheduler.py` 并列。
2. 在 `docker-compose.yml` 增加端口、挂载和令牌：

```yaml
services:
  march7thassistant:
    # …已有内容…
    ports:
      - "8787:8787"
    volumes:
      # …已有内容…
      - ./control_server.py:/m7a/control_server.py:ro
    environment:
      # …已有内容…
      - M7A_CONTROL_TOKEN=换成一串随机字符串
```

3. 重建容器：`docker compose up -d --force-recreate`。
4. 验证：`curl -H "X-Control-Token: 令牌" http://<NAS-IP>:8787/api/status`。

环境变量：

- `M7A_CONTROL_TOKEN`：访问令牌，未设置时控制接口不启动；
- `M7A_CONTROL_PORT`：监听端口，默认 `8787`；
- `M7A_CONTROL_HOST`：监听地址，默认 `0.0.0.0`；
- `M7A_CONTROL_ENABLE`：置 `0` 可整体关闭，即使令牌存在。

## 接口

所有接口都要带 `X-Control-Token` 请求头（或 `?token=` 查询参数），未授权返回 401。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/status` | 版本、触发时间、下次触发、当前运行、最近一次运行、当日标记、配置快照 |
| `GET /api/config` | 白名单配置项当前取值 |
| `POST /api/run` | 立即触发一次完整运行；已有任务在跑时返回 409 |
| `POST /api/stop` | 给正在运行的任务下发停止指令；当前空闲时返回 404 |
| `POST /api/config` | 修改白名单配置项，返回实际写入的键值 |

可远程修改的配置项：`currencywars_enable`、`weekly_divergent_enable`、`power_enable`、
`daily_enable`、`reward_enable`、`echo_of_war_enable`、`weekly_divergent_level`（1–6）。
写入时按行替换并保留原有注释，写前备份为 `config.yaml.bak-control`；任务运行中拒绝写入。

「立即运行」跳过一次镜像更新检查（避免在一次手动运行里顺带重建容器），其它流程与
当日定时触发一致；当日标记另落在 `logs/schedule-state/`，不影响下一晚的 04:00。

## 桌面端使用

1. 打开小助手，左侧导航「远程控制」。
2. 填 Docker 所在机器的地址、端口、令牌，点「保存并连接」。
3. 状态区会显示版本、下次触发、当前运行、最近一次运行、当日处理结果；页面每 30 秒自动刷新。
4. 「立即运行完整运行」/「停止当前运行」直接下发到容器；「配置开关」区改完点「保存到 Docker」。

## 安全与限制

- 接口是明文 HTTP，只建议在可信局域网内开放；如需跨公网，请走隧道并在前面加认证。
- 令牌保存在本机注册表（QSettings），与小助手的 `config.yaml` 分开；换机器要重新填。
- 白名单之外的配置项（路径、账号、推送密钥等）刻意不开放远程修改。
- 手动触发与定时触发共用同一条完整运行流程，不会并行跑两个任务。
