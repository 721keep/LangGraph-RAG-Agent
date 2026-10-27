# R3 Git 整理与 GitHub 发布准备结果

日期：2026-10-27。已形成精确暂存变更集，等待人工确认；**未 Commit、未 Push**。
本轮未调用模型、未连接或写入数据库、未迁移向量、未清理 Docker，未修改真实 .env / JWT / API Key。

## 1. 提交白名单与文件分类

精确白名单共 **127 文件：A 67 / B 31 / C 29**，包含 103 个新增、24 个已有文件修改。
完整逐文件清单、分类及原始 49 个行尾差异列表见
[STAGING_MANIFEST.json](docs/release/STAGING_MANIFEST.json)。按该清单逐个写入 index，未使用 git add . / -A。

| 分类 | 提交内容 |
|---|---|
| A 正式产品 67 | PDF 上传/状态可靠性；Chat/Embedding/Reranker 配置解耦；DeepSeek reasoning_content、并行工具、Checkpointer 与历史权限修复；M1～M3 全部 UI、主题、原创 Logo、29 个本地 Lucide 图标及许可 |
| B 部署/文档 31 | 新独立 release Compose、Dockerfile、锁定依赖、默认关闭/有界 runtime、实例校验、私有配置示例与管理脚本；legacy Compose/env 示例；README、验证摘要、清单与本报告；构建/凭据忽略规则 |
| C 回归 29 | 上传/模型配置、R1/R2 安全与空 Thread、Agent Thinking/工具/流式/引用/权限、Mock UI、显式 opt-in 删除测试；新可复现禁网/Mock 测试入口 |
| D 本地实验 221 | 历史阶段报告、预览 Compose、一次性浏览器/准备/核验工具等，不暂存，保留原文件 |
| E 敏感文件与原始证据 160 | 清单中逐项排除日志、截图、运行结果等；另统一排除已被忽略的 .env、所有 .private、真实账本、数据库文件、浏览器配置与账号秘密 |

特别确认 `frontend/gui/conversation_session.py`、`frontend/gui/resource_api.py` 已暂存，
保留 R2 空 Thread 历史精确 404 分类及切换修复；untracked 状态未导致产品文件遗漏。
M4 本身没有新增业务修复，已验收行为仍由上述产品源码提供。

保留删除测试 harness/test_delete 的原因：它们验证原 Router/Service 权限、级联、向量清理与事务回滚，
有长期价值；生成器依赖旧本机镜像且涉及临时凭据，留本地，不作为公开安装依赖。
保留 M1/M2 的相关用例与 M3 内存 HTTP/场景/preview，供最新版 UI 回归；旧预览环境配置不提交。
Stage3 的 guard 与 Agent 离线测试保留，其他 live/e1/e2/f1/f15 控制与原始证据不提交。
R1 的 verify.py 是历史摘要核验器，与 R2 后代码不再匹配，留本地，不再推荐为当前安装验收入口。

## 2. 49 个纯换行差异

初始 71 个 tracked 修改中，49 个经 HEAD/工作区字节比较及 CRLF→LF 归一比较确认只差行尾，22 个有真实改动。

- 其中 **48 个未暂存，也未改写工作区**，包括 LICENSE、原 auth/MCP/evaluation/requirements 等。
- `.gitignore` 原本也只差行尾；本轮增加必要安全规则，成为实际变更。其工作区仍为 CRLF，index 保持 HEAD 的 LF。
- `backend/app/rag/reranker.py` 同时有真实功能改动和整文件 LF→CRLF：保留真实改动，index 使用 HEAD LF，工作区原 CRLF 不变。
- 其他原有文件不批量转换。新增文件以其既有格式暂存，新文件模式统一 100644，已有文件保留 Git mode。
- 仅移除 sidebar、budget_core、r2_live、stage3 guard 共 4 处多余 EOF 空行，消除 diff --check 错误；没有业务语义调整。

因此状态里仍会出现未暂存的行尾差异，以及 .gitignore/reranker 的 MM，这是有意保留，不能直接 add 全部。
最终忽略行尾的 unstaged diff 应为空；报告和测试文档完成后已再次核对。

## 3. README / 文档更新

[README.md](README.md) 已按实际 Agent Workspace 重写，保留 721keep 原仓库地址和技术定位，
包括架构图、功能、四项私有 Key、首次 prepare/build/up、停止/重建、数据库/Collection 隔离、测试分层和已知限制。
不引用未经审核截图，不要求旧 M4/Stage3 私有容器。

[部署说明](deployment/release/README.md) 移除历史 verify 作为当前安装入口的误导，明确 R2 后库并非空库、
安全备份、JWT 影响与平台权限限制。新增工具依赖 requirements-tools.txt，现有 prepare 直接使用已提交 constraints，
不捕获旧 M4 镜像依赖。env.example 加 legacy 说明，未擅自切换原配置。

[验证摘要](docs/VALIDATION.md) 脱敏记录历史真实结果；[测试指南](tests/README.md) 给出可复现禁网/Mock 命令。
历史阶段报告不随提交分发，避免将临时测试密码和本地资源当成公开文档依赖。

## 4. 安全检查

| 检查 | 结果 |
|---|---|
| Git 可提交范围当前文件 | 199 文件；当前已知真实秘密精确匹配及高置信 Key/JWT/private-key 模式未命中 |
| 白名单 | 127 文件；未命中 |
| 完整 index（含未修改的 tracked 文件） | 199 文件；未命中；无 .env/.private/账本/原始证据误暂存 |
| 本地被排除原文件 | 381 文件另行扫描；发现 4 处包含已知临时测试秘密的文件，均不在 index 且已忽略 |
| 可达 Git 历史 | 37 commits / 239 blobs 精确匹配当前已知秘密；仅发现 1 个旧 JWT blob，未发现其他当前真实凭据匹配；未重写历史 |
| 私有配置/原始证据保护 | 基线覆盖的文件摘要未变，含根 .env、21 个原有私有文件以及历史 artifacts；未重置账本 |
| Docker 构建上下文 | release 两个 Dockerfile-specific allowlist；补充 frontend Streamlit secrets 排除；legacy backend/frontend 新增 .dockerignore 防止 .env/.private/缓存/日志进入上下文 |

4 个被排除的秘密命中文件：FRONTEND_REDESIGN_M4_RESULT.md、tests/frontend_m4/README.md、
tests/frontend_m4/prepare.py、tests/stage3/prepare.py。只记录路径，不披露值；原文件保留本地。
所有正式 .env / .env.*（公开示例除外）、.private、artifacts、数据库导出和浏览器配置被忽略。

旧根 .env JWT **仍与历史值一致**；新 release JWT **不同且长度至少 64 字符**，新版没有沿用已泄露签名值。
本轮不改任何密钥。若未来重新启用 legacy 环境，必须另行处理旧 JWT；不要将旧 .env 作为新版 runtime。

扫描是当前已知凭据精确值与明确模式检测，不覆盖所有未知历史密码、不可达对象、二进制 OCR 或全部敏感数据类型；
不宣称整个仓库绝对无秘密。原始私有文件本就应含凭据，不能因未进入 index 就视为可公开。

## 5. 测试与暂存统计

| 本轮执行 | 结果 |
|---|---|
| 上传43 + 配置45 + 发布安全28 + manage4 + 空 Thread11 + R2 HTTP guard15 | **146 passed** |
| Stage3 BGE 隔离/预算 | **12 passed** |
| 原 Agent Thinking/Tool Calling/流式/Citation/权限/缓存 | **32 passed** |
| M3 Mock UI | **25 passed** |
| 适配最新布局的 M1 / M2 Mock UI | **7 + 23 passed** |
| 合计 | **245 passed** |
| 完整 index Python 语法 | **117 文件 AST 通过**；实际导入由相应回归覆盖，不启动 app lifespan |
| git diff --cached --check | **通过，无输出** |

使用现有 R2 构建的 Python 3.12.11、锁定依赖 checks/frontend 镜像；核心/Agent 从完整 index 导出的无 .env 副本运行，
network none、只读源码、临时日志挂载。UI 使用 internal 网络的独立内存 Mock，无数据库、无密钥、无端口发布。
保留这些新测试容器和网络，不自动清理；未覆盖旧预览。

Agent 首次因导出副本缺少可写日志目录而未完成收集；补建挂载点并增加 tmpfs 后仅重跑该组通过。
已将修正写入公开测试命令，未修改产品配置初始化逻辑。
1 条 LangGraph serializer 后续弃用提示保留，没有为消除警告升级依赖。
本轮没有新增 Chrome/真实数据库/真实模型测试，不把 Mock 结果当成真实端到端验收。

Agent offline harness 原依赖本地 run_live.json，现只在 offline 模式读取明确虚构的 fixture.json；
PostgreSQL 模式继续读原快照，未破坏本机历史集成配置。

最终 `git diff --cached --stat`：**127 files changed, 10822 insertions(+), 1757 deletions(-)**。
详细统计已执行并保存本地 .release-artifacts/r3/staged.diffstat.txt；逐文件提交范围见提交 manifest。

## 6. Git 分支与安全同步

当前 `fix/rag-upload-reliability`，HEAD `3978dc83630c2b0d9d8e37eae93b25570a089f76`。
本轮成功 `git fetch --no-tags origin main`，origin/main 为 `5253ee63b860f9ce14ee8a7e224319f41963bbe5`。
**领先 origin/main 1，落后 0**；已有本地提交为 frontend MODEL_NAMES 透传。
origin 指向 721keep/LangGraph-RAG-Agent；upstream 是原参考仓库，两者不能混用。
当前分支未设置 upstream；本地 main 跟踪 upstream/main（ahead 22），不能据此判断自有 GitHub main 状态。

待你确认白名单后，建议提交信息：

```text
feat: deliver Agent Workspace and isolated BGE deployment
```

后续经明确授权再执行：

```bash
git diff --cached --stat
git diff --cached --check
git commit -m "feat: deliver Agent Workspace and isolated BGE deployment"
git fetch --no-tags origin main
git log --left-right --oneline origin/main...HEAD
# 确认 main 未前进，且同名远程 feature 分支没有其他人的提交后：
git push -u origin fix/rag-upload-reliability
```

优先向 origin/main 提 PR，经审核再合并；不直接覆盖 main、不 force push。
若 main 或同名远程分支有新提交，先停止并检查祖先关系/冲突，再决定 merge 或 rebase；本轮未执行这些操作。
需要注意：还有本地纯行尾差异，未来任何追加暂存都应继续使用白名单。

## 7. 18510 模型默认关闭与日常使用缺口

只读检查 R1 三个服务均 healthy；backend 实际为 model_mode=off，仅 application/database 两个 internal 网络。
bootstrap、safety 和 compose 共同在部署边界禁止模型外发，Chat/上传受控 503；
因此 18510 当前可登录、管理与查看历史，但不能直接进行日常真实 DeepSeek/RAG 聊天或真实向量上传。

R2 bounded 模式还限制验收用户、两问、一份 PDF、HTTP/Token 和账本生命周期，已 sealed，
不是日常模式。不能通过删除代码、改旧计数、重新 arm 或单独开放网络来解除保护。

最小后续方案（**本轮未实现/启用**）：

1. 新增明确 daily 模式，默认 off，保留三条数据库连接、实例、Collection/profile 和私有密钥保护。
2. 新建独立日常账本，与封存验收账本分开；每模型/每日请求和 Token 上限、HTTP 发送前持久计数、零自动重试、错误熔断。
3. 保留 DeepSeek/SiliconFlow 固定域名与非 Pro 模型白名单；以正常用户/KB 权限替换 R2 固定资源范围，只开放必要模型网络。
4. 增加离线安全回归，再进行少量经授权真实验收；Web Search/MCP 另行决定，不能顺带全开放。

这需要少量 runtime/部署调整，不能只设置一个现有开关就安全完成。目前可提交同步的版本应明确定位为
“新版工作台 + 默认安全部署 + 已完成受控真实验收”，不应宣称“已开放日常不限期使用”。

## 8. 结论与停止点

已满足本次 **提交准备** 的白名单、空白、安全与离线回归条件，可在人工确认暂存清单后 Commit 并推送 feature 分支/PR。
日常模型模式尚未实现，原生 Linux 私有 Secret 读取策略尚未独立验收，这些限制已公开写入 README。
旧数据库、271 条历史向量、my_collection 和旧 Checkpointer 未访问/修改，旧与测试 Docker 资源未清理。
未迁移数据，未改 JWT/API Key，未提交/推送；在此停止，等待最终 Git Commit/Push 指令。
