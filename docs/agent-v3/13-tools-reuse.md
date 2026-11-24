# 13 个写/读工具复用（2026-11-23）

## 为什么不能直接 import 旧 `tools/memory_tools.py`

旧文件依赖 `langchain.tools` 装饰器、`storage.database.supabase_client`、`yishu` 请求上下文。它是**传输层 + 旧基础设施绑定**，不是能力本身。V3 不 import 它，而是把 13 个能力语义拆到服务：

`agent/src/agentv3/services/tools.py` → `MemoryToolService`

| 旧工具 | V3 实现 | 写入层 |
|---|---|---|
| `save_memory` | Ingestor → Sculptor，NARRATIVE | 账本 |
| `update_memory_summary` | `summaries[person]` 缓存（摘要不是账本条目） | 不写账本 |
| `delete_memory` | `PurgeScope(person_id, entry_ids)` | 账本删除 |
| `save_knowledge_collection` | Ingestor(origin=knowledge) → Sculptor，ASSET | 账本资产层 |
| `delete_knowledge_collection` | 按 content_id 或 ASSET 层 purge | 账本删除 |
| `export_knowledge_collections` | ASSET 条目导出 JSON/Markdown | 只读 |
| `link_memory_to_collection` | `MemoryStore.add_link()`，随 state 持久化 | 账本 state |
| `fetch_url_content` | 注入 `fetch` callable；只允许 http/https，拒绝内网 IP | 不落库 |
| `search_memories` | 个人层关键词检索（排除 knowledge） | 只读 |
| `search_knowledge_collections` | ASSET 层关键词检索 | 只读 |
| `get_recent_memories` | 按 valid_from/recorded_at 倒序 | 只读 |
| `get_memory_stats` | 按 layer/library/category 统计 | 只读 |
| `get_knowledge_summary` | 复用 digest projection | 只读 |

## 关键约束

- Muse 只暴露 **5 个读工具**（`READ_TOOLS`）；写工具只在应用/后台编排里调用，模型没有写句柄。
- `fetch_url_content` 的 SSRF 检查在服务层：拒绝 `file://`、`localhost`、`127.*`、`10.*`、`192.168.*`、`169.254.*`、`172.16–31.*`。
- 删除按 `person_id` 限定；`test_delete_does_not_touch_other_person` 防跨人误删。
- 知识收藏强制 `TagSet.library=knowledge` + `MemoryLayer.ASSET`；Sculptor 校验不匹配即 `layer_disallowed`。
- 关联记录写入 `MemoryStore` 的 `links`，随 SQLite/PG 的 state 文档一起持久化；PG 适配器同时 upsert `agent_v3_links` 投影表。
- 摘要缓存目前是进程内 `dict`，重启丢失；这是**已知边界**，不伪称已持久化。

## 迁移

`backend/migrations/versions/b8c4d2f0a1e5_add_agent_v3_tags_and_links.py`

- `agent_v3_episode_tags` / `agent_v3_memory_tags`：可重建的标签投影。
- `agent_v3_links`：`(person_id, memory_id, collection_id)` 唯一。
- 不 ALTER `agent_v3_memory_entries`；账本正文/证据列保持 append-only。
