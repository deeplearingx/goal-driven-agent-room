# Agent Night Shift

面向 LangGraph 多 Agent 后端的实时像素协作工作台。四个角色通过 POST SSE 事件驱动工作、交接、审查、返工、人工介入和交付动画。

## 启动

```powershell
npm install
Copy-Item .env.example .env.local
npm run dev
```

开发环境默认将 `/tasks`、`/healthz` 和 `/api` 代理到 `http://localhost:8000`。可通过 `VITE_DEV_BACKEND_URL` 修改代理目标；生产环境使用 `VITE_API_BASE_URL`。

## 后端契约默认值

- `POST /tasks/stream` 请求体：`{ "task": string }`
- `POST /tasks/{id}/resume` 请求体：`{ "response": string, "tool_call_id"?: string }`
- SSE 支持通过 `event:` 或 JSON 的 `type/event` 字段声明事件类型。
- 未知 TaskResult、artifact、pending_tool_calls 字段会被保留并宽容归一化。

如真实字段不同，只需调整 `src/lib/api.ts` 与 `src/lib/normalize.ts`。

## 检查

```powershell
npm test
npm run lint
npm run build
```
