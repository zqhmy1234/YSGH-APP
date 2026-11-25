# 本地 L0（Colima + Docker Compose）安装与运行手册

> 2026-11-24 实测通过。本机为 Apple silicon（M2 / macOS 26.7），**没有 Docker Desktop、没有 Homebrew、sudo 需要密码**。
> 因此这里采用「用户目录安装、零 sudo」的方案。

## 1. 安装（一次性）

```bash
# 1) 静态 Docker CLI（TUNA 镜像）
mkdir -p ~/.local/opt/docker ~/.local/bin ~/.local/share/docker-install
cd ~/.local/share/docker-install
curl -L -o docker-static.tgz \
  https://mirrors.tuna.tsinghua.edu.cn/docker-ce/mac/static/stable/aarch64/docker-29.10.0.tgz
tar -xzf docker-static.tgz -C ~/.local/opt/docker
ln -sf ~/.local/opt/docker/docker/docker ~/.local/bin/docker

# 2) compose 插件（GitHub 直连不通时走 gh-proxy）
mkdir -p ~/.docker/cli-plugins
curl -L -o ~/.docker/cli-plugins/docker-compose \
  https://gh-proxy.com/https://github.com/docker/compose/releases/download/v5.8.0/docker-compose-darwin-aarch64
chmod +x ~/.docker/cli-plugins/docker-compose

# 3) buildx（必须；否则 compose build 会退回 legacy builder，导出镜像时会 I/O 失败）
curl -L -o ~/.docker/cli-plugins/docker-buildx \
  https://gh-proxy.com/https://github.com/docker/buildx/releases/download/v0.30.0/buildx-v0.30.0.darwin-arm64
chmod +x ~/.docker/cli-plugins/docker-buildx

# 4) Colima（VM 运行时）
curl -L -o ~/.local/bin/colima \
  https://gh-proxy.com/https://github.com/abiosoft/colima/releases/download/v0.10.3/colima-Darwin-arm64
chmod +x ~/.local/bin/colima
```

`~/.zshrc` 已有 `$HOME/.local/bin` 在 PATH 中，无需再改。

## 2. Colima 配置要点

`~/.colima/default/colima.yaml` 关键项：

```yaml
cpu: 4
memory: 8
disk: 40
arch: aarch64
runtime: docker
vmType: vz            # Apple 原生虚拟化，不依赖 qemu
network:
  dnsHosts: {}        # ⚠️ 必须是 map，写 [] 会整份配置加载失败并回退默认 2C/2G
docker:
  registry-mirrors:   # Docker Hub 直连不通
    - https://docker.1panel.live
    - https://docker.m.daocloud.io
```

首次启动会下载 Ubuntu 24.04 cloud image（约 317MB）。若 GitHub 下载被重置：
先把镜像下到 `~/Library/Caches/colima/caches/<ur.<sha256>.downloading>` 对应的
文件名（无 `.downloading` 后缀），Colima 会直接复用。

### VM 内 DNS（重要）

Cloud image 的 `/etc/resolv.conf` 指向不存在的 systemd-resolved → `docker pull` 会报
`lookup ...: connection refused`。修复：

```bash
colima ssh -- sudo sh -c 'rm -f /etc/resolv.conf && printf "nameserver 192.168.5.1\nnameserver 223.5.5.5\n" > /etc/resolv.conf'
colima ssh -- sudo systemctl restart docker
```

> 这是 VM 内改动，**重建 VM 会丢失**，重建后需重跑。

## 3. 起 L0 栈

```bash
# MinIO 官方镜像经所有可用镜像站都 403，本地用 Bitnami 等价镜像（已 gitignore）
cat > deploy/.env <<'EOT'
MINIO_IMAGE=docker.1panel.live/bitnami/minio:latest
POSTGRES_PASSWORD=local-v3-only
AGENT_SERVICE_TOKEN=local-agent-service-token
AGENT_V3_TOKEN=local-agent-token
EOT

docker compose -f deploy/compose.agent-v3.yml up -d postgres redis qdrant minio
docker compose -f deploy/compose.agent-v3.yml --profile app up -d backend worker   # 可选
```

## 4. 迁移与冒烟

```bash
cd backend
DATABASE_URL='postgresql+psycopg://postgres:local-v3-only@127.0.0.1:55432/yishu' \
  PYTHONPATH=. .venv312/bin/python -m alembic upgrade head

bash scripts/dev/agent_v3_compose_smoke.sh
```

冒烟脚本要求 `docker` 与 daemon 可达；`LINGQU_LIVE=1` 时才发真实模型请求。

## 5. 已知坑

| 症状 | 原因 | 处理 |
|---|---|---|
| `colima start` 卡在 downloading disk image | GitHub 直连被重置 | 用 `gh-proxy.com` 预下载到 caches 目录 |
| `config load failed: cannot unmarshal !!seq into map[string]string` | `dnsHosts: []` 应为 `{}` | 改成 `{}` |
| `docker pull` 报 `connection refused` on `[::1]:53` | VM 内 resolv.conf 坏了 | 见 §2 DNS 修复 |
| `exporting layers ... input/output error` | 宿主磁盘写满（APFS 只剩 110MB） | 清出空间后重启 Colima |
| `docker compose` 不存在 | 插件缺失或为 HTML 假文件 | 重新下载 `~/.docker/cli-plugins/docker-compose` |
| 镜像拉取 403 | 镜像站限流 | 换 `docker.1panel.live` / `docker.m.daocloud.io` |
