# COS 开通与验证（B4 · Wave3 AgentG · audit #10 缺口 · 2026-08-28 B3 收尾更新）

> 生成：2026-08-26 · 维护：并行开发 Agent G（B4 后端域）/ Agent B3（收尾 Wave1，2026-08-28）
> 目的：生产对象存储（腾讯云 COS）+ 数据万象（CI 图片能力）的开通步骤与验证命令。
> 现状（2026-08-28）：**COS key 已配齐**（Infisical dev 环境 + `backend/.env` 别名
> `TENCENT_CI_SECRET_ID` / `TENCENT_GUANHAIFENG_CI_SECRET_KEY` / `TENCENT_COS_BUCKET` /
> `TENCENT_COS_REGION`，config.py 别名读取已对齐）；本地开发用 `STORAGE_BACKEND=fs`
> （跨进程共享），**上生产切 `cos` 零代码变更**——切换步骤见 §2.5。

---

## 1. 前置

- 腾讯云账号（未开通可先免费开通）
- 已申请/待申请：`TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`（feature_list OPS-SECRETS 待办④缺失项申请清单）

## 2. 开通步骤

### 步骤 1：创建 COS 存储桶

1. 控制台 → 对象存储 COS → 存储桶列表 → 创建存储桶
2. 名称建议 `yishu-<env>-<appid>`（如 `yishu-prod-1300000000`），地域选最近（如 `ap-guangzhou` / `ap-shanghai`）
3. 访问权限：**私有读写**（对象一律经后端/STS 访问，不公开）
4. 创建后记下：`COS_BUCKET`（桶名，含 appid 后缀）、`COS_REGION`（地域，如 `ap-guangzhou`）

### 步骤 2：创建子账号（最小权限）

1. 访问管理 CAM → 用户 → 用户列表 → 新建用户 → 自定义创建 → 编程访问
2. 权限策略：
   - `QcloudCOSFullAccess`（MVP 最小可用；后续可收敛为按桶 `cos:PutObject/GetObject/DeleteObject`）
   - `QcloudCIReadOnlyAccess`（数据万象打标/审核只读）
3. 记下子账号的 `SecretId` / `SecretKey` → 写入 Infisical（`TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`）

### 步骤 3：数据万象（CI）开通（图片打标/内容审核）

1. 控制台 → 数据万象 CI → 开通（绑定上述存储桶）
2. 计费：打标 ≈0.0015 元/次；内容审核按次/按量（见 `忆述光华_外部API清单与成本.md`）
3. CI 复用 COS 凭证（`tencent_ci.py` 已对齐别名），无需单独密钥

### 步骤 4（可选）：STS 角色（客户端直传）

- 客户端直传需 STS 临时凭证。当前 `storage.py:get_sts_credentials` 用 root ARN
  （优化已搁置），若 AssumeRole 失败自动降级为后端中转（见 api/upload.py UPLOAD_006）。
- 要启用客户端直传：CAM → 角色 → 创建（信任 qcloud 服务，绑定 COS 上传策略），
  记下 `TENCENT_STS_ROLE_ARN`；`TENCENT_APPID` 为腾讯云账号 appid（公开参数）。

### 步骤 5：配置 backend/.env

```ini
STORAGE_BACKEND=cos
COS_BUCKET=yishu-prod-<appid>
COS_REGION=ap-guangzhou
TENCENT_SECRET_ID=<infisical: TENCENT_SECRET_ID>
TENCENT_SECRET_KEY=<infisical: TENCENT_SECRET_KEY>
TENCENT_APPID=<可选，STS 用>
TENCENT_STS_ROLE_ARN=<可选，客户端直传用>
```

> 密钥纪律：**永不硬编码**；生产从 Infisical 注入（`skills/infisical-secrets/SKILL.md`）。
> 密钥未到位前生产保持 `STORAGE_BACKEND=fake`/`fs`（开发用 fs 跨进程共享），
> 到位后一键切换 `cos` 零代码变更。

### 步骤 6（2026-08-28）：本地 fs → 生产 cos 切换

切换 = 改 1 个配置项 + 4 个 COS 变量已配即可，无代码改动。具体：

| 配置项 | 本地 dev（现状） | 生产 |
|---|---|---|
| `STORAGE_BACKEND` | `fs`（跨进程共享，根目录 `FS_STORAGE_ROOT=data/storage`） | `cos` |
| `COS_BUCKET` / `COS_REGION` | 已配（`TENCENT_COS_BUCKET` / `TENCENT_COS_REGION` 别名） | 已配 |
| `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` | 已配（别名 `TENCENT_CI_SECRET_ID` / `TENCENT_GUANHAIFENG_CI_SECRET_KEY`） | 已配 |

切换前用存储闭环冒烟验证（`--storage-only`，零 CI 费用）：

```powershell
Remove-Item Env:TENCENT_SECRET_ID -ErrorAction SilentlyContinue
Remove-Item Env:TENCENT_SECRET_KEY -ErrorAction SilentlyContinue
infisical run --env=dev --silent -- python scripts/smoke_cos.py --storage-only
# 预期输出：{"upload": {"ok": true}, "download": {"ok": true, "sha256_match": true},
#           "list_objects": {"ok": true}, "cleaned": {"ok": true}}，退出码 0
```

单元层（key 缺失自动 skip 标"环境依赖"）：

```powershell
cd backend
python -m pytest tests/test_storage_backends.py -k "round_trip or list_objects" -v
```

> 已核验（2026-08-28，Agent B3）：本机 `.env` 别名 COS 凭据下，真实 COS
> 上传/读回 SHA256 一致/前缀列举/删除闭环全绿；生产部署仍建议在部署机再跑一次
> `--storage-only` 冒烟确认网络可达（COS 走公网或内网 endpoint 见部署检查清单）。

## 3. 验证命令（拿 key 后执行）

```bash
# 0. 存储闭环快速冒烟（fs→cos 切换验证，零 CI 费用；2026-08-28 新增）
#    上传/读回 SHA256 校验/list_objects 前缀列举/删除闭环
Remove-Item Env:TENCENT_SECRET_ID -ErrorAction SilentlyContinue
Remove-Item Env:TENCENT_SECRET_KEY -ErrorAction SilentlyContinue
infisical run --env=dev --silent -- python scripts/smoke_cos.py --storage-only

# 1. 全链路冒烟（上传/读回/缩略图/CI 打标/审核/STS/list_objects/清理）
#    屏蔽 .env 旧变量避免别名抢占，用 Infisical 注入
Remove-Item Env:TENCENT_SECRET_ID -ErrorAction SilentlyContinue
Remove-Item Env:TENCENT_SECRET_KEY -ErrorAction SilentlyContinue
infisical run --env=dev --silent -- python scripts/smoke_cos.py

# 2. 分片断电续传实测（真实 COS）
infisical run --env=dev --silent -- python scripts/smoke_cos_upload.py

# 3. 缩略图回填（历史照片补缩略图，audit #1）
cd backend
python scripts/backfill_thumbnails.py --limit 200
```

预期：`smoke_cos.py --storage-only` 输出 `{upload, download, list_objects, cleaned}` 全 ok
（退出码 0）；`smoke_cos.py` 输出 `{upload, download, list_objects, thumbnail, ci_tags,
ci_audit, sts, cleaned}` 各步 ok（`sts` 可降级提示不阻断）；`smoke_cos_upload.py`
输出 `RESULT: PASS`。

## 4. 相关代码位置

| 能力 | 文件 |
|---|---|
| 存储抽象（fake/fs/minio/cos） | `backend/app/services/external/storage.py` |
| COS 分片状态机 | `backend/app/services/upload.py` |
| 缩略图管线 | `backend/app/services/thumbnails.py` + `backend/scripts/backfill_thumbnails.py` |
| CI 打标/审核 | `backend/app/services/external/tencent_ci.py` |
| 客户端直传 STS | `api/upload.py` GET /api/v1/upload/sts |
