# 日常模型运行

此模式使用原 FastAPI / LangGraph / RAG / Checkpointer 和新版 UI，不固定用户、问题或 PDF。
基础 compose.yaml 默认仍为 off；R2 bounded 及其封存账本不变。

## 当前机器的日常操作

启动 Docker Desktop，在 WSL 项目根目录执行：

```bash
python3 deployment/release/manage.py up-daily
```

打开 http://127.0.0.1:18510 ，登录或注册自己的账号。匿名模型调用不开放。
`up` 会回到 off，日常使用请明确用 `up-daily`。

```bash
python3 deployment/release/manage.py stop           # 停止服务，保留数据/请求账本
python3 deployment/release/manage.py daily-status   # 查看今日预留量、累计请求、暂停原因
python3 deployment/release/manage.py build-daily    # 更新源码后的构建
python3 deployment/release/manage.py restart-daily  # 安静时重建/重启，保留两个持久卷
```

restart-daily 之前应等待上传/聊天结束；中断中的 HTTP 可能已计费，重启会暂停而非自动重发。
镜像使用独立 daily 标签，未覆盖 R2 镜像。

## 首次准备

已有环境的 `runtime.json`、JWT、数据库实例证明保持原样，不重新生成。
本机已执行以下步骤，**不要再 prepare-daily 或重建账本**：

```bash
python3 deployment/release/manage.py prepare-daily
python3 deployment/release/manage.py build-daily
python3 deployment/release/manage.py up-daily
```

prepare-daily 创建 Git 忽略的 `.private/daily_policy.json`，复制经校验的 tokenizer 到
`.private/daily-tokenizer`。本机来源是先前已验证的 R2 tokenizer 公共模型文件，未读取其账本。
全新机器按下节准备公共文件并显式传入目录；不需要 R2、M4、Stage3 容器或私有验收计划。
以下步骤只适用于全新安装，不能用于重置现有环境。

### 全新机器的完整初始化

1. 按根 README 安装 Docker / Compose、Python 工具依赖，并在 Git 忽略的根 `.env`
   填写 CHAT_API_KEY、EMBEDDINGS_API_KEY、RERANK_API_KEY、TAVILY_API_KEY。
2. 从项目根目录运行 `python3 deployment/release/manage.py prepare`。
   这只生成独立 runtime/JWT/数据库密码及待验明的实例配置，不连接旧数据库，不需要启动任何历史容器。
3. 下载本次已审核的公共 tokenizer 文件。固定来源为
   [DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)，
   revision 与每个文件的 SHA256/大小固定在本目录 `tokenizer.manifest.json`。
   该 manifest 只有公共元信息，没有私有路径、凭据或模型请求账本；不分发供应商模型文件。

在项目根目录执行（只下载公共静态文件，**不是模型推理 API 请求**；本次 Git 整理未执行下载）：

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib, json, urllib.request
source = Path('deployment/release/tokenizer.manifest.json')
manifest = json.loads(source.read_text())
target = Path('deployment/release/.private/tokenizer-source')
target.mkdir(parents=True, exist_ok=False)  # 已存在时停止，勿覆盖
for name, metadata in manifest['files'].items():
    url = f"https://huggingface.co/{manifest['repo']}/resolve/{manifest['revision']}/{name}"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if len(data) != metadata['bytes'] or hashlib.sha256(data).hexdigest() != metadata['sha256']:
        raise SystemExit('Tokenizer integrity mismatch; stop, do not prepare or start daily')
    path = target / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
(target / 'manifest.json').write_bytes(source.read_bytes())
print('Pinned public tokenizer verified; no credentials or model calls involved.')
PY
```

下载失败或校验失败时停止，检查网络和供应商文件，不更改 manifest 来跳过校验。
也可从可信离线介质取得相同五个文件及 manifest，校验后使用；不要传入未经审核的 encoding.py。

4. 显式使用刚准备的目录并从当前源码构建：

```bash
python3 deployment/release/manage.py prepare-daily deployment/release/.private/tokenizer-source
python3 deployment/release/manage.py build-daily
python3 deployment/release/manage.py up-daily
```

首次 up-daily 创建独立计数卷，核验新 PostgreSQL Volume/实例并初始化业务与 Checkpointer。
不需要先 build/up R2；`runtime.json` 中的密钥不会进入镜像。

## 配额与计数

配置示例为 daily.example.json；实际编辑私有 daily_policy.json 后，在无请求时 restart-daily。
必须保留 `ledger_id`，不能删除、替换或初始化旧账本来获取额外额度。

| 默认保护 | 数值 |
|---|---|
| 每 UTC 日期 Chat / Embedding / Reranker HTTP 请求 | 300 / 1000 / 500 |
| 每日期各模型输入预留上限 | 各 2,000,000 |
| 每日期 Chat 输出预留上限 | 500,000 |
| 单次 Chat 输入 / 输出上限 | 16,384 / 2,048 |
| 每个用户 HTTP 操作中 Chat / Embedding / Reranker 请求 | 12 / 64 / 8 |
| 并发模型请求 / 批量文本数 | 3 / 32 |
| 单文本 / 整批字符上限 | 6,000 / 48,000 |

Chat 使用已校验 tokenizer 加安全余量预留；BGE 使用 UTF-8 字节数加特殊 token 余量，
这是保守限制单位，**不是精确 BGE tokenizer 用量**。实际返回 usage 另存账本，不能将预留量作为账单。
发送前事务预留，不因短回答或失败退回额度；所有失败 HTTP 也计数。SDK / HTTP Transport 均无自动重试。
当前 daily 显式关闭 Thinking，保留原 reasoning_content 兼容实现；不把此验收表述为 daily Thinking 开启验收。

新账本在 `rag_release_daily_usage_v1` 持久卷的 SQLite 中，以事务和 FULL synchronous 保存，
记录 HTTP 状态、数值 usage、错误分类、维度/排序、工具名；不记录密钥、prompt、回答或完整 reasoning。
每日期新增记录，旧日期记录不会删除或归零；容器重建/Docker 重启不重置。
缺少账本/身份不符时拒绝启动。未确认请求跨重启保留并触发暂停。

## 错误与恢复

额度/并发限制有明确 HTTP/流式安全错误码，页面显示固定提示，不显示上游原始错误。
认证、账单、限流、异常响应和网络/流中断会暂停调用，检查 daily-status 及供应商状态后再处理。
不要因为页面未收到完整响应而重传已成功文件或重发旧问题。

只有在核验失败/未确认请求、停止服务后，才显式恢复：

```bash
python3 deployment/release/manage.py stop
python3 deployment/release/manage.py resume-daily --acknowledge
python3 deployment/release/manage.py up-daily
```

恢复仅解除暂停，保留失败/未确认记录和计数，不自动补发。每日额度用尽无需恢复，可等下一 UTC 日期
或经核验调整私有配额再重启。单次输入过大应缩短会话/文件，不能靠恢复绕过硬上限。

## 隔离与限制

三连接继续使用 langgraph_rag_bge_prod / bge_m3_1024_v1 / 原 R1 持久卷及 JWT，启动前核验真实实例。
Backend 与 PostgreSQL 不发布端口，Frontend 仅 127.0.0.1:18510。新 daily 逻辑出口网络与 R2 分开。
只放行固定 DeepSeek / SiliconFlow 的三类 HTTPS 模型接口，不自动切换 Pro；其他普通 HTTP Transport 仍禁用。
本地 MCP 工具结构保留，真实外部搜索/MCP 不随 daily 模式自动开放。

当前是单实例本地使用、全实例共享配额，不提供公网生产、多租户计费或多 worker 并行启动保证。
原生 Linux Secret 的非 root 权限策略仍需独立验收。账本长期增长需要未来明确的备份/归档策略，本次不清理。
