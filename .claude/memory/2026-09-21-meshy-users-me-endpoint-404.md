# Meshy 账户接口排错：users/me 不在 v2 命名空间且 404

- **日期**：2026-09-21
- **现象**：用 `GET /openapi/v2/users/me` 和 `GET /openapi/v1/users/me` 验证 API key，均返回 404 Not Found（非 401），一度以为 key 有问题。
- **根因**：key 校验不必走账户端点；v2 任务列表端点 `GET /openapi/v2/text-to-3d?page_size=1` 即可完成鉴权验证（200 + 空集合）。另注意 `GET /openapi/v2/text-to-3d/pagination` 并不存在——`pagination` 会被当作 `{task_id}` 匹配，返回 400 "Invalid ID"，极具迷惑性。
- **修复**：`MeshyClient.list_tasks(page_size=1)` 作为连通性与鉴权检查的标准入口。
- **预防**：对接第三方 API 时，先探一个"确定存在"的最便宜只读端点验鉴权；对文档里没逐字确认过的路径不要猜。判断鉴权问题看 401/403，路由问题看 404，语义要看响应体（本项目 400 带的是 "Invalid ID"）。
