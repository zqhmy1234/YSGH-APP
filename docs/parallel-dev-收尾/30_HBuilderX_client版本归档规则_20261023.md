# HBuilderX client 版本归档与 CLI 唯一性规则

- 制定时间：2026-10-23
- 适用范围：所有使用 HBuilderX CLI 编译、运行到真机或云打包的本仓库工作

## 一、强制规则

1. 当前仓库 `D:\projects\YSGH-APP\client` 是唯一允许命名为 `client` 的 HBuilderX 项目。
2. 旧 client 副本按最近一次已生成 APK 的 versionCode 加后缀归档，例如：
   - `client-102`
   - `client-103`
3. 每次 APK 重新打包是归档周期边界：
   - 打包前确认旧副本已带版本后缀；
   - 当前待修改、待复验源码继续保留 `client`；
   - 打包后再出现新副本时，旧副本按刚生成的 APK versionCode 归档。
4. 不允许两个 HBuilderX 项目同时叫 `client`。
5. 不允许只凭“编译成功”判断选中了当前源码；必须确认项目路径和包内 versionCode。

## 二、2026-10-23 已执行归档

| 原路径 | 新路径 | 依据 |
|---|---|---|
| `D:\projects\YSGH-PR3\client` | `D:\projects\YSGH-PR3\client-102` | 该副本 manifest versionCode = 102 |
| `D:\projects\YSGH-APP\client` | 保持不变 | 当前最新源码，versionCode = 104，尚未生成 104 APK |

已确认 HBuilderX 注册表中只有当前仓库项目名称仍为 `client`。

## 三、自动守卫

新增：

```text
scripts/hbuilder_project_guard.py
```

检查命令：

```powershell
D:\projects\ysgh_venv\Scripts\python.exe scripts\hbuilder_project_guard.py
```

通过条件：

- HBuilderX 注册表存在；
- 只有一个项目目录名为 `client`；
- 该路径解析后等于 `D:\projects\YSGH-APP\client`。

`scripts/client_compile_gate.py` 已在编译前调用同一守卫。出现同名项目时直接报失败，不再依赖 HBuilderX 选择顺序。

2026-10-23 22:53–22:54 验证：

```text
[OK] # 1 client -> D:\projects\YSGH-APP\client
项目 client 编译成功
16 个页面
ready in 45002ms
```

随后完整静态门再次通过：

```text
452 files compiled OK
client_compile: 客户端编译成功（18398ms）
审核通过，可以提交
```

## 四、每次出包前

```text
1. 运行 hbuilder_project_guard.py
2. 确认 CLI `--project client` 只可能指向当前仓库
3. 编译后核对 package/versionCode，不只看“编译成功”
4. 成功生成 APK 后，把下一次需要淘汰的副本按该 versionCode 归档
```

## 五、事故记录

2026-10-23 真机联调时，HBuilderX 同时登记：

- `D:\projects\YSGH-APP\client`，versionCode 104；
- `D:\projects\YSGH-PR3\client`，versionCode 102。

CLI 使用 `--project client` 时选中了旧 102 副本。日志虽然有“编译成功”，实际仍运行旧配置，造成真机继续请求旧地址、textarea 修复未生效的假象。该事故说明：项目名不唯一时，编译日志不足以证明当前源码被执行。
