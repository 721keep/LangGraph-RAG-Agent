# 日常模型运行模式结果

2026-10-27：**当前本机已达到日常运行条件，18510 正在运行 daily 模式。**
基础部署默认仍为 off；没有解除 R2 封存、修改旧 .env、迁移旧向量或 Commit/Push。

## 实现与使用

原 FastAPI、LangGraph、RAG、引用和 Checkpointer 保持不变；在部署边界增加独立 daily 模型工厂、
HTTP allowlist、发送前持久配额和安全错误提示。不限定用户、提问、文件名或测试资源 ID。
沿用 `langgraph_rag_bge_prod`、`bge_m3_1024_v1`、原 R1 JWT、业务与 Checkpointer 数据。
实际 business / pgvector / checkpointer 三条连接和集群身份均通过启动核验。

打开 Docker Desktop，在 WSL 项目根目录执行：

```bash
python3 deployment/release/manage.py up-daily
```

访问 **http://127.0.0.1:18510**，可注册自己的账号，登录后正常管理知识库、上传与问答。
当前机器已完成首次 prepare-daily/build-daily，无需重新准备或生成 JWT。

```bash
python3 deployment/release/manage.py stop           # 保留全部数据和计数
python3 deployment/release/manage.py daily-status   # 查看使用量/暂停原因
python3 deployment/release/manage.py build-daily    # 更新源码后构建
python3 deployment/release/manage.py restart-daily  # 无请求时重启/重建
```

普通 `up` 仍回到 off；不要使用 up-r2/arm-r2 进行日常聊天。
配置、首次安装及暂停恢复见 [DAILY.md](deployment/release/DAILY.md)。

## 安全机制

- 固定 DeepSeek 官方 `deepseek-flash`、SiliconFlow 非 Pro `BAAI/bge-m3` 与 `BAAI/bge-reranker-v2-m3`。
  地址与模型先经原 release profile 验证，HTTP 层再核对 HTTPS 域名、端口、路径和模型；不自动换模型。
- 默认每 UTC 日期 HTTP 上限为 Chat 300、Embedding 1000、Reranker 500；各模型输入预留 200 万，
  Chat 输出预留 50 万；单次 Chat 输入 16384、输出 2048；并发 3。私有策略可调整且有硬安全上限。
- 每次用户操作还有模型调用次数和批量/文本上限；SDK 与 Transport 重试均为 0，避免工具循环无界调用。
- 独立 SQLite 账本位于新卷 `rag_release_daily_usage_v1`，事务预留后才发 HTTP，FULL synchronous；
  失败也计数。按日期追加记录，不删除旧日期、不因容器重启清零，不复用 R2 账本。
- 正常启动拒绝缺失/身份不符账本；中断遗留请求会暂停。恢复需停止服务后显式
  `resume-daily --acknowledge`，不返还计数、不补发请求。
- 额度/并发/暂停/上游错误通过固定错误码显示；不展示 provider 原文或伪装成模型回答。
  匿名 Chat 被部署层拒绝，正常用户/KB 权限仍由原接口执行。
- 密钥/JWT/密码仍在 Git 忽略的私有配置中；账本仅保存计数、数值 usage、错误类别和必要元信息，
  不保存 prompt、回答或完整 reasoning。当前 daily 明确关闭 Thinking；原兼容代码保留。
- Backend、PostgreSQL 不发布宿主机端口；Frontend 仅绑定 127.0.0.1。daily 出口网络与 R2 独立，
  普通 HTTP Transport 的封锁保留；真实搜索/外部 MCP 没有随 daily 自动放开。

## 实际验证

| 检查 | 结果 |
|---|---|
| 离线回归 | **191 passed：新增 daily 45 + 原相关回归 146**，锁定依赖、禁网且无 .env/.private 挂载 |
| Streamlit AppTest | **3 项通过**：额度、暂停、并发提示，不显示未受信任错误载荷 |
| 构建与健康 | 独立 `20261027-daily` Backend / Frontend / checks 构建成功；三服务 healthy |
| 真实 Chrome | 登录、创建/选择 KB、真实上传、Tool Calling、流式回答、引用、追问、刷新重登、重启恢复及深色主题通过 |
| 正例 PDF | 新单页中文 PDF，离线解析 123 字符、1 Chunk；网页 ready；真实 BGE-M3 写入 1024 维 |
| RAG / Citation | 原 retrieve_user_documents + 实际 PGVector + 实时 Reranker；来源为 daily_runtime_concise.pdf、page 1、chunk 0 |
| Evidence Gate | 正例 Vector **0.5740**、Reranker **0.9721**，Gate PASS；回答含 BGE-M3 / 1024 |
| 连续追问 | 同 Thread 恢复前文，回答 PostgreSQL / pgvector，并保留真实来源引用 |
| 重启持久化 | 三容器重建/重启后实例身份、文档、向量、ToolMessage 配对、答案摘要完全一致；Chrome 重新登录恢复成功 |
| 使用记录 | 重启/恢复/主题切换前后 **11 条账本记录完全一致**，没有额外模型请求 |

实际供应商 HTTP 请求（包含最初保留的拒答案例）：

| 模型 | HTTP 次数 | 状态 | 服务返回 Token |
|---|---:|---|---|
| DeepSeek Chat | 5 | 全部 200 / complete | 输入 **13,428**，输出 **378** |
| BGE-M3 Embedding | 4 | 全部 200 / complete | 输入 **1,197** |
| BGE Reranker | 2 | 全部 200 / complete | 输入 **414** |

没有余额探测、批量评测、自动重试或 Pro 切换。daily 账本保持可持续累计，不像验收账本那样封存。
私有账本元信息与 Chrome/数据库证据保存在本机 `.private/daily-checks*` 和 `.release-artifacts/daily`，不提交凭据。

## 发现的问题与处理

1. 新初始化 helper 首次缺少 Docker `-i`，未收到私有配额 stdin；补齐并增加回归。
   账本使用独占创建，初始化失败也不能覆盖已有计数。
2. Compose 复用 R2 的逻辑网络键引发重绑定错误；daily 改用独有 `daily-egress` / `rag_release_daily_models`。
   R2 及首次创建的网络均保留，没有执行资源清理。
3. 新 Embedding 适配器与原工厂重复传递 chunk_size/check_embedding_ctx_length，导致启动失败；
   改为合并后一次传参，并验证强制零重试。以上失败均发生在模型请求前。
4. 最初复用的两 Chunk PDF 含重复干扰文本。首问所有接口成功、Gate 放行，但只召回缺少事实的段落，
   模型据实拒答，未达到正例答案标准。该结果完整保留，没有重发原会话问题或改阈值。
   随后另建 KB，上传新单段 PDF 完成正例及追问；不是 Mock 或检索快照替代。

最终保留本轮账号的 2 个 KB、2 个 ready PDF、3 个 1024 维 Chunk、2 个有 Checkpointer 的 Thread。
其他用户业务摘要与最初基线一致。旧正式数据库、my_collection 和 271 条旧向量未连接或修改。
1221 个原有私有/账本等保护文件、原有 50 个行尾差异文件均通过逐文件摘要核验，未改变。
本轮 23 个公开改动经当前已知凭据精确匹配及限定模式扫描，无命中；16 个 Python 文件语法通过，
运行镜像内模块摘要与当前源码一致。这不是整个仓库绝对无秘密的承诺。
HEAD 仍为 864a4f4，未暂存、Commit 或 Push；未清理旧测试容器、网络或任何 Volume。

## 文件清单

修改：README.md；deployment/release/{README.md,backend.Dockerfile,manage.py,runtime/bootstrap.py}；
frontend/gui/{api_utils.py,conversation_events.py,conversation_view.py}。

新增：deployment/release/{DAILY.md,compose.daily.yaml,daily.example.json,daily_manage.py,
runtime/daily_guard.py,runtime/daily_runtime.py,runtime/daily_init.py}；frontend/gui/runtime_errors.py；
tests/daily/{test_runtime.py,ui_errors.py,browser_live.py,acceptance.py,db_probe.py,pdf_fixture.py}；本报告。
仅前端安全错误展示有小范围兼容调整，布局、Logo、图标、双主题、Agent/RAG 业务逻辑和 API 契约不变。

## 已知限制

- 当前结论适用于本机单实例本地日常使用，配额是全实例共享，不是公网多租户计费方案。
- Chat Token 是官方 tokenizer + 安全预留；BGE 预留采用 UTF-8 字节估计，实际 usage 另记，不能当精确账单。
- Gate PASS 不保证片段可回答；本次干扰文档已说明检索质量边界，没有为验收降低阈值。
- 刷新后的历史 API 仍主要提供正文；结构化工具面板不补造，完整工具消息仍在 Checkpointer。
- daily 当前 Thinking 关闭；未额外验收 daily Thinking 开启、外部搜索/MCP、原生 Linux Secret 权限。
- 新机器需要经审核的 tokenizer/manifest；需要安全备份原私有配置、数据库卷和 daily 账本卷。
  管理/测试产生的临时容器本轮按要求保留；没有自动归档或清理历史请求。

结论：**当前 18510 已具备受控、可持续的本地日常模型运行能力**。保留运行状态与测试数据，在此停止。

## Daily 发布整理补充

运行验收结束后的本次 Git 发布整理没有新增真实模型调用、数据库操作或容器重启。
补齐 tokenizer.manifest.json（固定官方仓库 revision 与五个公共文件的大小/SHA256）及完整新机器准备命令；
新增 tests/daily/test_release_packaging.py，验证显式公共文件目录准备不需要历史 R2/Stage3 容器，
校验失败不发布私有配置，已有配置不覆盖。没有修改已验收的模型运行逻辑或旧 .env。

公开保留 test_runtime.py、test_release_packaging.py、ui_errors.py 和该目录排除规则。
acceptance.py、browser_live.py、db_probe.py、pdf_fixture.py 是依赖本机历史资源的验收工具，
作为本地实验材料保留，不随本次提交；其凭据、PDF、截图、账本和 .release-artifacts 同样不提交。
所有原有 50 个纯行尾差异保持工作区原样，不纳入本次提交。
