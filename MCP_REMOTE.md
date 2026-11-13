# KB MCP 远程接入指南

其他设备上的 Hermes Agent 如何通过 MCP 连接 fajita 上的 KB 知识库。

---

## 前置条件

- 目标设备已加入 Tailscale 网段（能 ping 通 `100.88.251.64` 或 `fajita.tail117f80.ts.net`）
- 目标设备已配置 SSH key 到 fajita（即 `ssh user@100.88.251.64 echo OK` 能直接通过）

> 如果目标设备还没有 SSH key：
> ```bash
> # 在目标设备上生成 key（如果还没有）
> ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
>
> # 把公钥追加到 fajita
> ssh-copy-id user@100.88.251.64
> ```

---

## 配置方式

在目标设备的 Hermes 配置文件 `~/.hermes/profiles/<profile>/config.yaml` 的 `mcp_servers` 段添加：

```yaml
mcp_servers:
  kb:
    command: ssh
    args:
      - -o
      - StrictHostKeyChecking=accept-new
      - -o
      - ConnectTimeout=10
      - user@100.88.251.64
      - docker
      - exec
      - -i
      - kb
      - python3
      - /app/mcp_server.py
    enabled: true
```

或者用命令行添加：

```bash
hermes mcp add kb \
  --command ssh \
  --args -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 user@100.88.251.64 docker exec -i kb python3 /app/mcp_server.py
```

然后测试连接：

```bash
hermes mcp test kb
```

---

## 工作原理

```
目标设备 Hermes Agent
  │
  │  stdio (JSON-RPC over SSH pipe)
  ▼
ssh user@100.88.251.64  ──────────────────────────────
  │                                                      │
  │  在 fajita 上执行                                    │
  ▼                                                      │
docker exec -i kb python3 /app/mcp_server.py             │
  │                                                      │
  │  直连容器内 SQLite                                    │
  ▼                                                      │
kb-docker/data/kb.db                                      │
```

数据不离开 fajita，不需要把数据库文件复制到别的设备。SSH 传输本身在 Tailscale 加密隧道内，没有额外暴露面。

---

## 本机（fajita）的配置对比

本机不需要 SSH 跳板，直接用 docker exec：

```yaml
mcp_servers:
  kb:
    command: docker
    args:
      - exec
      - -i
      - kb
      - python3
      - /app/mcp_server.py
    enabled: true
```

---

## MCP 工具清单

| 工具 | 功能 |
|---|---|
| `search(query)` | FTS 全文搜索 |
| `get_page(name)` | 获取页面全文 + tags + 链接 + 反链 |
| `create_page(name, content)` | 创建页面 |
| `update_page(name, content)` | 更新页面 |
| `delete_page(name)` | 删除页面（可通过 versions 恢复） |
| `rename_page(old, new)` | 重命名 + 原子重写入链 |
| `list_pages()` | 列出所有页面名 |
| `get_recent_pages(limit)` | 最近更新的页面 |
| `get_tags()` | 所有 tag 及计数 |
| `get_pages_by_tag(tag)` | 按 tag 筛选页面 |
| `get_backlinks(name)` | 获取反链 |
| `get_page_versions(name)` | 版本历史（含已删除页面） |
| `restore_page_version(name, version_id)` | 恢复旧版本 |

---

## 故障排查

| 症状 | 原因 | 解决 |
|---|---|---|
| `Permission denied (publickey)` | SSH key 未授权 | `ssh-copy-id user@100.88.251.64` |
| `docker: command not found` | SSH 后没进 docker 路径 | 确认 fajita 上用户有 docker 权限 |
| `connect: connection refused` | kb 容器未运行 | 在 fajita 上 `docker ps` 检查 |
| 超时 | 网络不通 | `ping 100.88.251.64` 检查 Tailscale |
| `Host key verification failed` | SSH 已知主机 | 已配置 `StrictHostKeyChecking=accept-new` 自动处理 |

---

## 安全说明

- 数据库始终在 fajita 容器内，远程设备只通过 SSH + docker exec 读写
- SSH 传输走 Tailscale 加密隧道
- nftables INPUT 链默认 drop，SSH 端口仅放行 Tailscale 接口（`iifname "tailscale0*"` accept）
- KB 容器有 IP 白名单（`KB_IP_WHITELIST=1`，仅放行 Tailscale 网段和 localhost），但 MCP 通过 `docker exec` 在容器内部执行，不受此限制
