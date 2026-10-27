# 可复现的回归测试

保留产品长期回归；历史 Stage3/F1/F1.5/M4/R1/R2 私有计划、账本、截图、生成配置与一次性验收脚本留本地。
不要直接执行 `pytest tests`，避免误收集本地历史集成测试。

## 禁网离线回归

先构建锁定 Python 3.12.11 依赖的 checks 镜像，不启动数据库或应用：

```bash
docker compose -p rag-release-r1 -f deployment/release/compose.yaml --profile checks build checks
```

测试必须运行在没有 `.env` 的已审核副本。提交前可导出 index 到全新目录：

```bash
mkdir -p /tmp/rag-reviewed-tree
git checkout-index --all --prefix=/tmp/rag-reviewed-tree/
mkdir -p /tmp/rag-reviewed-tree/logs
docker run --network none --read-only --tmpfs /tmp --tmpfs /app/logs --tmpfs /workspace/logs \
  --mount type=bind,src=/tmp/rag-reviewed-tree,dst=/workspace,readonly \
  --workdir /workspace --entrypoint python \
  langgraph-rag-release-checks:20261027-r2 -B tests/offline/core.py
```

`checkout-index` 只导出 index，不重置工作区。Windows/WSL 使用 Docker Desktop 可访问的绝对路径。
不挂载私有配置、不接入网络；fixture 的 Key/JWT/URI/用户均为虚构。
预算测试仅在临时目录创建虚构账本，不读取或重置历史账本。

| 测试组 | 范围 |
|---|---|
| test_upload_reliability | 上传错误/超时、并发、状态写回/回滚、结果不确定时保留向量 |
| test_model_configuration | 三模型配置隔离、legacy 回退边界、HTTP 契约 |
| release/test_release_safety / test_manage | 三连接/实例/集合校验、默认关闭、私有配置不覆盖、依赖锁定 |
| release/test_empty_thread | R2 空 Thread 404、权限错误、历史保护与会话切换 |
| release/test_r2_guard | HTTP 发送前计数、Token/模型/域名限制、封存与熔断 |
| stage3/test_guard | 原 BGE 隔离和预算边界，仅虚构请求/内存响应 |
| stage3/agent/test_contract / test_regressions | 原 Agent Thinking、工具消息、流式、Citation、多工具、历史权限与缓存 |

Agent offline 使用 fixture.json 虚构数据，不声称真实检索。
本地 PostgreSQL 验收仍读取原私有运行快照，未改写资源 ID。

## Streamlit Mock UI

```bash
docker compose -p rag-release-r1 -f deployment/release/compose.yaml build frontend
docker compose -f tests/offline/ui.yaml up -d --wait m3-api
docker compose -f tests/offline/ui.yaml run --no-deps -T checks
```

仅内部网络、无端口发布、无密钥或数据库挂载，使用最新发布 frontend 镜像；不依赖旧私有预览容器。
运行 M3 消息/工具/引用/恢复测试及适用于最新布局的 M1/M2 回归。
`demo@example.org` / `workspace-demo` 只是内存 Mock 固定输入，不能用于真实部署。
这是 AppTest 和 HTTP Mock，不是 Chrome 或真实后端验收。不含自动资源清理步骤。

## 真实数据库/模型

delete_acceptance 保留真实 Router/Service 的 11 项隔离删除断言与 harness，默认跳过。
需另外搭建全新临时数据库和私有配置，见该目录 README；历史生成器依赖本地镜像，未列入公开启动步骤。
本轮离线测试不访问 DeepSeek、BGE、搜索或外部 MCP。
历史真实结果见 [验证摘要](../docs/VALIDATION.md)，本轮见根目录 PROJECT_RELEASE_R3_RESULT.md。
