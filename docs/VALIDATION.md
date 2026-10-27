# 验证记录与边界

以下真实结果来自已完成的历史验收，本轮 R3 未重放或新增供应商请求。
原始报告、截图、账号信息和请求账本保存在本地，不随公开仓库分发。

| 层次 | 已验证范围 | 说明 |
|---|---|---|
| 离线 / Mock | 上传可靠性、配置解耦、DeepSeek 普通/流式 reasoning_content、多工具消息、Citation、权限、模型预算 | 不能替代真实服务 |
| Mock UI | M1/M2/M3 双主题、KB/文档、工具面板、SSE、错误恢复、rerun 防重发 | Streamlit AppTest 和历史 Chrome；不代表真实检索 |
| 隔离真实数据库 | PDF 写入/检索、Checkpointer 恢复、删除与回滚 | 原 Router/Service，独立数据库；删除专项 11 项通过 |
| M4 实时链路 | 真实浏览器 PDF 上传、DeepSeek + BGE-M3 + pgvector + Reranker、引用、多轮、历史、删除 | 独立环境，无旧集合迁移 |
| R1 | 独立新库/Collection、JWT 轮换、镜像、健康与默认关闭保护 | 120 项离线回归；未调用模型 |
| R2 | 新部署真实登录/上传/RAG/Citation/追问/重启历史/真实删除 | DeepSeek 3 / Embedding 2 / Reranker 1，均 HTTP 200；54 项定向回归，7 个 Chrome 阶段 |

R2 Chat 实际输入 9886、输出 217 Token；Embedding 输入 1113、Reranker 输入 1127 Token。
这些数值不等于费用账单；未在本轮查询价格或余额。R2 预算已封存，默认模型模式已恢复 off。

R3 的实际离线回归、暂存和安全检查见根目录 PROJECT_RELEASE_R3_RESULT.md。
不依赖本地历史账本的可复现命令见 tests/README.md。
