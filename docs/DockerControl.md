# Docker 模式（远程小助手）

桌面端可以把整个界面切换成一个"远程壳"：窗口标题栏标注 Docker，界面里的配置与任务全部以 Docker 上
那台小助手为准，所有操作只影响远程，不影响本机。

## 怎么用

1. 打开小助手，进入 `设置 → 杂项 → Docker 控制`。
2. 填写 Docker 主机地址、端口（默认 8787）、访问令牌，点「连接」。
   - 连接前会先探活，连不上会直接提示，不会切走。
   - 连接成功后当前窗口关闭，另开一个标题栏带 `Docker` 的新窗口。
3. 新窗口里：
   - 配置来自 Docker 上的 `config.yaml`，改完保存会整份写回远程；
   - 任务（完整运行、清体力、货币战争等）交给 Docker 执行，日志页显示远程输出；
   - 「工具箱」「启动游戏」等只对本机有意义的入口被隐藏。
4. 回到本地：在同一张卡片点「切回本地小助手」，或直接正常启动小助手。

## 组成

| 位置 | 文件 | 作用 |
|---|---|---|
| 容器内 | `control_server.py` | 标准库 HTTP 控制接口，由容器里的 `scheduler.py` 启动 |
| 容器内 | `scheduler.py` | 调度器主循环；提供状态、接收任务触发、执行停止、读写配置 |
| 桌面端 | `app/common/docker_mode.py` | Docker 模式状态、配置镜像与同步、远程任务白名单 |
| 桌面端 | `app/common/docker_control.py` | 带令牌的 HTTP 客户端 |
| 桌面端 | `app/card/docker_control_card.py` | 设置里的卡片与连接对话框 |
| 桌面端 | `app/log_interface.py` | Docker 模式下把任务转给远程并轮询日志 |

配置镜像放在程序目录的 `docker_profile/config.yaml`；目标地址与令牌记在
`docker_profile/target.json`，本机 `config.yaml` 不会被改动。

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
4. 验证：`curl -H "X-Control-Token: 令牌" http://<Docker-主机-IP>:8787/api/status`。

环境变量：`M7A_CONTROL_TOKEN`（必填，未设置则不启动）、`M7A_CONTROL_PORT`（默认 8787）、
`M7A_CONTROL_HOST`（默认 0.0.0.0）、`M7A_CONTROL_ENABLE`（置 0 关闭）。

## 接口

除探活外都要带 `X-Control-Token` 请求头（或 `?token=`），未授权返回 401。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/status` | 版本、触发时间、下次触发、当前运行、最近一次运行、当日标记、配置快照 |
| `POST /api/run` | 触发任务，请求体 `{"task": "main"}`；已有任务在跑时返回 409 |
| `POST /api/stop` | 给正在运行的任务下发停止指令 |
| `GET /api/run_log` | 当前/最近一次运行的日志尾部，可带 `?tail=字节数` |
| `GET /api/config_file` | 整份 `config.yaml` 文本（Docker 模式的配置镜像） |
| `POST /api/config_file` | 用整份文本覆盖配置；校验可解析、写前备份、任务运行中拒绝 |
| `GET /api/config` | 白名单配置项当前取值 |
| `POST /api/config` | 按白名单单键修改（保留注释） |

可远程触发的任务：`main/完整运行`、`routine/日常`、`daily/每日实训`、`power/清体力`、
`currencywars`、`divergent`、`fight`、`universe`、`forgottenhall`、`purefiction`、
`apocalyptic`、`redemption`。

## 限制

- 远程任务只支持「停止」，暂不支持暂停/继续；流程编排与外部程序在 Docker 模式里不可用。
- 任务运行中远程会拒绝配置覆盖，等任务结束再保存。
- 接口是明文 HTTP，只建议在可信局域网内开放；跨公网请自行加隧道与额外认证。
- 令牌保存在本机 `docker_profile/target.json`，换机器要重新填。
