# 周期数据从 v1 迁移至 v2

现有 v2 部署不需要重复迁移。仅持有旧版数据的部署需要运行 [`migrate_cycle_v2.py`](../migrate_cycle_v2.py)。脚本保留 v1 表，只将已确认的正式记录与适用设置导入 v2；旧的详细健康信息分享权限不会自动扩大。

1. 停止旧应用写入，按[部署文档](deployment.md#升级与回退)备份数据库与数据目录。
2. 在备份副本上预演：`python3 migrate_cycle_v2.py --db /path/to/copy.db --dry-run`。
3. 核对预演结果后对目标库执行 `python3 migrate_cycle_v2.py --db /path/to/sched.db`。正式迁移先归档 v1 数据，可用 `--archive-dir` 指定备份目录。归档含健康数据，存放在私有位置。
4. 启动 v2 应用，分别验证自己的历史和对方可见范围。需要回退时先恢复应用配置；如迁移失败，保留现场并从一致性备份恢复，不用旧库覆盖已新增的业务数据。
