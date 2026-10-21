# 知客前端

React + Vite + TDesign 的销售沟通界面，提供登录、历史会话、SSE 执行进度、确认/澄清、模拟聊天导入、准备单与证据抽屉。

```bash
npm ci
npm run dev
```

默认后端为 `http://localhost:8000/api`。需要更改时复制 `.env.example` 为 `.env.development.local`；不要把令牌或服务端密钥放进 `VITE_*` 变量。

```bash
npm test
npm run lint
npm run build
```

`src/pages/Chat.jsx` 管理请求恢复与订阅，`src/chat/` 展示对话，`src/sales/` 展示证据和准备单。服务端仍执行最终参数校验、客户归属和引用校验。

完整部署和功能说明见[项目 README](../README.md)及[本地运行指南](../docs/GETTING_STARTED.md)。
