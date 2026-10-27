# 删除集成回归：显式 opt-in

保留原 Router/Service 的 11 项隔离断言：KB/Document/向量删除、权限、旁路数据保留、重复删除及异常回滚。
历史真实数据库验收已通过，本轮不重复执行写入或删除。

没有 DELETE_ACCEPTANCE_MODE=isolated 时跳过。启用前须建立全新临时 PostgreSQL/pgvector，
并严格满足 harness.py 的 host/user/database/collection/run_id 约束。
仅内部测试网络，不挂载原 .env，不连接任何正式/Stage3 资源。
私有 JSON 从 /run/secrets/delete_runtime 读取，需 run_id、host、port、user、password、database、collection、jwt_secret。
这些值由测试运维新生成，不能复制现有账号、密码、资源 ID。

容器需要当前 backend 依赖及 pytest，源码挂载 /workspace/backend 和 /delete-tests，
PYTHONPATH=/workspace/backend:/delete-tests，并提供可写 /delete-artifacts。
执行 /delete-tests/test_delete.py 会在验明目标后初始化、创建/删除临时数据，只能在上述全新实例运行。
历史 prepare/compose/verify 及私有文件留本地，不属于干净安装命令。
