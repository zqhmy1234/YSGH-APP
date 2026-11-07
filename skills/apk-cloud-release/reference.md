# 参数速查与故障对照（apk-cloud-release 配套）

## 一、`cli pack` 签名相关参数（`cli pack --help` 摘录）

| 参数 | 含义 |
|---|---|
| `--android.androidpacktype` | **0=自有证书** / 1=公共 / 2=DCloud 老版 / 3=云端证书。**不传会落到云端证书** |
| `--android.certalias` | 自有证书别名 |
| `--android.certfile` | keystore（.jks）绝对路径 |
| `--android.certpassword` | 证书密码 |
| `--android.storepassword` | 证书库密码 |
| `--android.packagename` | 包名（打包 Android 必填） |
| `--config` | 配置文件绝对路径（**口令只能走这里，不能进命令行**） |

其它有用的：`--project`（HBuilderX 里已导入的项目绝对路径）、`--iscustom true`（自定义基座）、
`--safemode true`（安心打包，**仅 uni-app 项目**，我们是 uni-app x，勿依赖）、`--sourceMap`。

## 二、故障对照表

| 现象 | 真因 | 处理 |
|---|---|---|
| 打包报「打包参数无法解析，请关闭杀毒软件…」 | **误导性报错**。真因是 `--config` 的 JSON 必须**嵌套 + 原生类型**：`android.*` 是真嵌套对象、布尔给原生 `true` 而非字符串 | 按结构重写 JSON；扁平键名会触发这条假报错 |
| 日志显示「类型: Android云端证书」 | `androidpacktype` 未设=0，或误以为 json 里的 `keystore` 字段生效 | 加 `androidpacktype:0` + 四个 cert* 字段，重新打包 |
| 已安装用户装不上新包 | 签名与旧包不同（云端证书 vs 自有证书） | 用同一 keystore 重打；**已用错签名分发过则只能卸载重装** |
| 装上后所有页面全空且不报错 | 包里焊的临时隧道地址已失效 | 换活地址进 `PROD_BASE_URL` 后**重出包**；旧包不会自愈 |
| 打开就卡登录 | `ALLOW_DEVICE_LOGIN` 未开 → 501 `AUTH_014` | `backend/.env` 加 `ALLOW_DEVICE_LOGIN=true` |
| 记录能存但 AI 分类/地名/聚合不跑 | RQ worker 没跑（内容卡 `processing`） | `python -m app.workers.worker`（cwd=`backend`） |
| 新内容迟迟不处理、worker 忙 | low 队列积压（`text` 类型按设计进 low 队列） | 开发期可 `q.empty()`；`process_content` 走 high |
| 本机探活隧道 SSL 失败 | **本机网络到 Cloudflare 被阻断**，不代表隧道坏 | 换**云端视角**验证（访问 `https://<域名>/healthz` 看是否 `status:ok`） |
| 编译/打包莫名中断 | HBuilderX 单队列，与其它窗口并发被互杀 | 确认无其它 `cli pack` / `cli launch` 在跑 |

## 三、证书信息位置

| 项 | 位置 |
|---|---|
| keystore | `%USERPROFILE%\.yishu-signing\yishu-guanghua-beta.jks`（alias `yishu`） |
| 口令 | 同目录 `keystore-password-请转移后删除.txt`（`path`/`alias`/`passwd` 键值形式） |
| 导出证书 | 同目录 `yishu-beta-cert.der`（公开信息，可给备案） |
| 登记值（比对基准） | `docs/图标与签名证书_20260921.md` §五 |

> 口令文件在密码转入密码管理器后应删除；口令丢失 = 无法再为已发布包签更新，只能卸载重装。
> keystore 本身要**与口令分开**备份，换机/重装会丢。

## 四、换址（服务器到货后）

1. 客户端改 `client/utils/config.uts` 的 `PROD_BASE_URL`（**唯一必须改的客户端处**）
2. 服务器 `deploy/.env` 改 `BASE_URL`（不影响已打的 APK）
3. 跑 `deploy/scripts/preflight_pack.ps1`（退出码 0 才继续）
4. **重新云打包**（旧包焊死了旧地址，不会自己恢复）

详见 `deploy/ONE_SWAP.md`。

## 五、本次出包的实测数据（2026-10-04，可作下次对照基线）

| 项 | 值 |
|---|---|
| 包名 / versionCode | `com.yishu.guanghua` / 100（0.1.0） |
| 大小 | 26.74 MB |
| minSdk / targetSdk | 24 / 34，单 ABI `arm64-v8a` |
| 签名方案 | v2 + v3（`apksigner verify` 退出码 0） |
| 证书 DN | `CN=YiShu GuangHua Beta, OU=Beta, O=YiShu GuangHua, L=Beijing, ST=Beijing, C=CN` |
| 证书 MD5 | `48:d7:6d:ba:80:ac:66:cb:5e:cd:2b:8c:be:99:25:fc`（备案用） |
| 证书 SHA-1 | `70:ac:5e:f2:71:57:56:79:3b:ef:89:31:32:0f:25:17:fb:95:97:d8` |
| 证书 SHA-256 | `cf:b4:36:29:ff:9f:c8:a4:09:d3:df:34:d3:38:31:1a:f8:44:13:8e:07:b3:e4:31:29:a3:0f:cf:05:c2:22:f2` |
| 云打包耗时 | 约 4~9 分钟（含「原生插件需联网下载」提示，属正常） |
