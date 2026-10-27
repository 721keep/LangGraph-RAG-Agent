# 独立发布部署

完整原 FastAPI / LangGraph 与最新版 Streamlit Agent Workspace；不使用 Mock/回放入口。
从项目根目录运行，Python 工具依赖：`python -m pip install -r deployment/release/requirements-tools.txt`。

## 首次安装

只在全新机器、同名资源不存在且没有 runtime.json 时运行 prepare。现有部署跳过此步骤。
在 Git 忽略的根 `.env` 安全填写 CHAT_API_KEY、EMBEDDINGS_API_KEY、RERANK_API_KEY、TAVILY_API_KEY。
prepare 只读这四项，生成新的 JWT/数据库密码，绝不覆盖原 `.env` 或旧数据库。
固定模型/数据库配置见 [runtime.example.json](runtime.example.json)，示例不直接参与启动。

```bash
python deployment/release/manage.py prepare
python deployment/release/manage.py build
python deployment/release/manage.py up
```

constraints 是已审核并纳入版本管理的确切版本，构建不需要旧 M4 私有容器。
模型 Key 不进入镜像。当前 prepare 要求四项凭据至少 12 字符，默认模式仍禁止真实模型/搜索调用。

## 私有文件与持久化

项目固定 rag-release-r1；数据库 langgraph_rag_bge_prod；Collection bge_m3_1024_v1；
Volume rag_release_r1_pgdata_v1。Backend/PG 不发布端口，页面仅 http://127.0.0.1:18510。
manage 在新库初始化前验明 Volume 标签、cluster system_identifier，后端执行 DDL 前再次验证
业务/向量/Checkpointer 三条连接及 Collection 的模型 profile，拒绝旧 my_collection。
R2 留下的验收用户/Thread/Checkpointer 仍存在，临时 KB/Document/向量已删；不是空库。

安全备份 `.private/runtime.json`、`db_password`、`database_identity.json` 与数据库卷，
不要在日志中打印它们。更换 JWT 将使旧登录令牌失效，prepare 拒绝重复生成。
Windows 可执行 protect_private.ps1 限制 NTFS ACL。Linux 使用目录 0700 / 文件 0600，
但必须同时验证 Docker Secret 的容器 uid 10001 可读策略；原生 Linux 尚未单独验收，勿直接放宽至全员可读。

```bash
python deployment/release/manage.py stop
python deployment/release/manage.py ps
python deployment/release/manage.py logs
python deployment/release/manage.py build
python deployment/release/manage.py recreate
```

更新代码后 build 再 recreate，stop 保留所有数据。不要用 down -v、不要手改项目名/卷名绕过预检。
R1 的 verify.py 和 artifacts 是本地历史证据，未纳入本次提交，也不作为当前版本安装依赖。
离线校验命令见 [测试指南](../../tests/README.md)。

## 模型调用模式

默认 compose.yaml 指定 off：内部网络、Transport 拦截、Chat/上传入口 503。
up 不开放真实模型。R2 的 compose.r2.yaml 是显式 bounded 覆盖，仅用于授权验收；
私有计划、tokenizer、账本不随仓库分发。数据库/实例/Collection 校验始终存在。
build-r2/up-r2/arm-r2/seal-r2/restart-r2 是验收运维命令，当前已 sealed，不能重新 arm。
不要运行这些命令来绕过日常模式缺口，也不要创建新账本冒充延续旧验收。

日常使用需要后续独立的 daily 模式与新运行账本：每模型请求与 Token 上限、发送前持久计数、
域名/模型白名单、零自动重试、认证/额度错误熔断、正常用户/KB 授权及明确的仅模型网络出口。
这需要少量部署/runtime 开发及离线安全回归，本轮没有永久解锁或重置任何预算。
