# IIS 备份与恢复（第一阶段）

> 当前维护租约和 `active_intake` 寄存器是进程内状态，只适用于当前单 IIS Web 进程部署。
> 启用多个 Gunicorn worker 前，必须先把维护状态迁移到 `/run/iis-maintenance/` 的共享状态与
> 文件锁；这是扩容前置条件，不得仅依赖每个 worker 的内存开关。

当前实现的边界是：不排空分析队列，只短暂停止新的 `/collect`，等待已经进入 intake 的请求
完成，然后对 standalone MongoDB 加数据库级写锁并执行 `mongodump`。查询仍可使用。Mongo
解锁和 intake 恢复后，SQLite 通过 Online Backup API 在线复制；配置文件一并进入备份。

备份协调器从统一的 `/api/runtime/state` 读取状态，并同时校验进程 `instance_id`、维护
`lease_id`、租约 `generation` 和 `active_intake`。四者有任一变化，都会在 Mongo 加锁前取消
本次备份，避免 IIS 重启、旧租约通知或错误进程返回造成竞态。完成时 `release` 最多重试三次，
随后再次读取统一状态，只有当前租约确实消失才继续生成正式 manifest。
若快照期间 `instance_id` 改变，Mongo 仍会先安全解锁，但该次产物保持失败状态，不会成为正式
可恢复备份。

## 准备

生产安装器会在权限为 `0600` 的 `_config/iis.env` 中生成
`IIS_MAINTENANCE_TOKEN`。已有部署需要补入一个随机值并重启 `iis-web.service`，执行备份的
账户也必须通过环境变量取得同一个值。维护 API 只接受 loopback 请求。

本机可用 `GET /api/runtime/state` 无登录读取统一运行寄存器，包括进程实例 ID、启动时间、
uptime、读写 readiness、控制状态版本、维护租约和 active intake；非 loopback 请求返回 403。
`GET /maintenance/ping` 仍供 launcher/systemd 轻量探活，并返回实例 ID、uptime 和 readiness。
反向代理不得把这些只限本机的路径转发到公网：`/maintenance/ping`、
`/api/runtime/state`、`/api/maintenance/backup/*`。标准 Nginx 拒绝片段位于
`deploy/nginx/iis-local-only-locations.conf`，应在代理 IIS 的 `server {}` 中 include；公网
请求统一返回 404。应用层仍检查 `remote_addr`，形成代理边界与应用边界两层限制。

MongoDB Database Tools 的 `mongodump`、`mongorestore` 必须位于 `PATH`。备份目标应位于
独立磁盘；生成目录先使用 `.partial`，完整备份校验后才原子改为正式名称。

备份使用独立 `_config/backup.yaml`，示例为 `_config/backup_example.yaml`。主配置
`iis.yaml` 不包含备份调度；`iis-backup.timer` 是唯一自动触发方，避免 IIS 故障同时使调度失效。

```bash
export IIS_MAINTENANCE_TOKEN='从 iis.env 安全读取的值'
/path/to/iis-python Scripts/iis_backup.py backup --config _config/backup.yaml
```

每个备份包含 `manifest.json`、Mongo gzip dump、SQLite 一致副本和配置文件。可独立校验：

```bash
/path/to/iis-python Scripts/iis_backup.py verify /mnt/iis-backups/<backup-id>
```

维护租约默认 15 分钟，到期后 intake 自动开放。Mongo 写锁另有独立 guard 进程；备份父进程
被强杀时，guard 会在租约到期时尝试解锁 Mongo 并释放租约。`mongodump` 自身也不能运行超过
租约期限。

## 恢复

不带 `--apply` 的恢复命令只校验，不修改任何数据：

```bash
/path/to/iis-python Scripts/iis_backup.py restore /mnt/iis-backups/<backup-id>
```

正式恢复必须先停止 IIS 与 crawler。恢复会调用 `mongorestore`，并原子替换备份中的 SQLite；
`--drop-mongo` 会在恢复前删除 dump 中的同名集合，适合完整灾难恢复，不适合日常导入。

```bash
systemctl stop iis.target
/path/to/iis-python Scripts/iis_backup.py restore /mnt/iis-backups/<backup-id> \
  --apply --drop-mongo --confirm-services-stopped \
  --confirm-backup-id '<backup-id>'
systemctl start iis.target
```

恢复后必须检查 Mongo 集合数量、SQLite `integrity_check`、登录、查询和一条测试采集，再把恢复
结果记入演练记录。当前第一阶段尚未覆盖 VectorDB（可重建）、日志、`_export` 和异地上传。
