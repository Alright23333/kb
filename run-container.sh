#!/bin/sh
# 在 ui-lazyvim 分支上起一个独立测试容器（端口 8099）。
# 复用生产镜像 kb-docker-kb:latest（Python 依赖没变，没必要重建），
# 只把源码以只读方式挂进去，数据用本分支的 data/ 副本 ——
# 绝不指向生产的 ~/services/kb-docker/data。
set -e
SRC=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-8099}
NAME=${NAME:-kb-ui-test}

mkdir -p "$SRC/data"
# 首次运行：从生产库做一份快照（.backup 不会带走 -wal/-shm 残留）
if [ ! -f "$SRC/data/kb.db" ]; then
  echo "初始化 data/kb.db（从生产库快照）…"
  sudo sqlite3 /home/user/services/kb-docker/data/kb.db ".backup '$SRC/data/kb.db'"
fi
sudo chown -R 10000:10000 "$SRC/data" 2>/dev/null || true

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network kb-docker_default \
  -p "127.0.0.1:$PORT:8080" \
  -v "$SRC/data:/app/data" \
  -v "$SRC/main.py:/app/main.py:ro" \
  -v "$SRC/routers:/app/routers:ro" \
  -v "$SRC/static:/app/static:ro" \
  -v "$SRC/templates:/app/templates:ro" \
  -e KB_DB_PATH=/app/data/kb.db -e TZ=Asia/Shanghai \
  -e KB_ALLOWED_CIDRS='100.64.0.0/10,127.0.0.0/8,172.17.0.0/16,172.22.0.0/16,::1/128,fd7a:115c:a1e0::/48' \
  -e KB_TRUSTED_PROXIES='127.0.0.0/8,::1/128,172.16.0.0/12' \
  --restart unless-stopped \
  kb-docker-kb:latest >/dev/null

echo "起好了：http://127.0.0.1:$PORT"
echo "预览（tailnet 内）：https://fajita.tail117f80.ts.net:8447/"
