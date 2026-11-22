# Docker 控制接口（容器侧）

`control_server.py` 是容器内调度器用的 HTTP 控制服务，只用标准库，不依赖项目其它模块，
因此可以单独挂进官方镜像。桌面端的「远程控制」页面（`app/docker_interface.py`）调用它。

完整说明见仓库内 `docs/DockerControl.md`。

最小部署：

1. 把这个文件放到容器工程目录（与 `scheduler.py` 并列）。
2. `docker-compose.yml` 增加：

```yaml
    ports:
      - "8787:8787"
    volumes:
      - ./control_server.py:/m7a/control_server.py:ro
    environment:
      - M7A_CONTROL_TOKEN=换成一串随机字符串
```

3. 重建容器并验证：

```bash
docker compose up -d --force-recreate
curl -H "X-Control-Token: 令牌" http://127.0.0.1:8787/api/status
```

`scheduler.py` 需要按仓库说明接入 `server_from_env(...)`：提供状态快照、立即运行开关、
停止回调、配置路径与「是否有任务在运行」的判断。
