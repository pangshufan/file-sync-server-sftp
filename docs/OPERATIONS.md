# 0.4 运维与性能说明

[English](en/OPERATIONS.md)

## 数据库访问和事务

每个 IndexStore 复用一个写连接，由写锁保护；最多四个独立只读连接供状态/明细查询和队列边界读取使用，只读连接设置 query_only 并按查询范围持有快照。PRAGMA 在连接建立时设置，不再在每次操作重复设置。WAL 提供已提交快照；读连接不能看到尚未提交的更新。

扫描、队列刷新、用户规则重算/重置和清理按最多 200 条分批提交。规则变更在全部分页完成后才记录新策略，处理中断后可继续重算；每个 userid 另有锁避免同用户的重算/确认交错。只读状态查询不争抢写锁。大规模规则重算仍需遍历相关记录，分批并不意味着总耗时消失。

变更日志清理每轮最多约 1 秒，成功/offer 清理每轮最多约 5 秒；预算在事务之间检查，单次 SQL 或事务可能超出预算。预算耗尽的剩余记录下轮继续，不影响最新 catalog 恢复。list/ack 仍会同步追赶当前用户的新 catalog；首次大积压请求可能较慢，尚未改为异步排队协议。

`sync_status()` 返回 database 指标：读写调用数、累计等待秒数、最长写事务/写锁等待。统计包括初始化、扫描和维护，不等同于纯 SQL 执行时间；接口统计不包含扫描专用逐文件读连接。停止服务关闭连接池。

0.5.1 修复 Windows 上 stat/lstat 与 fstat 的 ctime 语义差异：路径/句柄交叉比较使用大小、mtime、birthtime 和文件 ID；POSIX 继续使用 ctime。摘要和自动确认还分别保存同一打开句柄的原始 ctime/设备 ID 快照，前后比较以拒绝真实变更，不能简单删除 ctime 检查。Windows birthtime 是创建时间，并不单独证明内容未变；完整性仍依赖摘要、读取区间和句柄检查，保留定期摘要复核。

## 持久运行日志

日志位于应用数据目录 `logs/server.jsonl`，UTF-8 JSONL、UTC 时间。每个文件最多约 5 MiB，保留 5 个轮转文件，合计约 30 MiB。目录/文件采用 POSIX 0700/0600；Windows 应用数据目录的访问范围由系统 ACL 决定。

网络线程向有界队列提交日志，后台线程写盘；队列满时丢弃新增日志，避免阻塞 SFTP。`sync_status()` 的 logging 项提供 dropped_records/write_errors，桌面增量页在非零时显示异常。停止服务等待已入队日志刷出。

包含启动/停止、现有文件打开/拒绝等运行消息，以及结构化登录、断开、限速拒绝和配置重载事件。不记录密码、私钥或完整配置。用户名、路径及来源 IP 会出现在日志中。新运行说明按服务端语言生成，已有日志不重写。它是可轮转的运行日志，不是不可篡改或完整覆盖所有传输结果的审计系统。

## 登录保护

桌面停止服务后可在“登录保护…”调整，或者修改 config.json。默认值：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| auth_max_inflight | 4 | 同时进行的密码摘要校验数；忙时拒绝，不无限排队 |
| auth_ip_attempts | 20 | 单 IP 在窗口内的认证校验次数 |
| auth_user_attempts | 10 | 单账号在窗口内的认证校验次数 |
| auth_window_seconds | 60 | 滑动窗口秒数 |
| auth_max_connections | 128 | 全部 SSH 连接上限，包含已认证和认证中的连接 |

成功和失败的校验均计入预算；公钥探测/签名阶段可能触发多次回调，均计数。未知账号共用账号桶；IPv4 和对应的 IPv4-mapped IPv6 共用 IP 桶。限速状态仅在进程内保存，重启清空；活动桶最多 4096，满时拒绝新桶，不驱逐现有保护。更短窗口或更高预算可适应多设备自动化。

原有单连接、IP/CIDR、单连接三次失败关闭和 30 秒登录超时继续生效。认证协程取消时，仍在运行的摘要计算继续占用预算，防止反复取消绕过上限。

## 公钥和无人值守客户端

在用户“SSH 公钥”页粘贴 OpenSSH 公钥，每行一个、最多 32 个，不含 authorized_keys 选项；可以保留密码，或勾选“仅允许公钥登录”。私钥保留在客户端。公钥账号继续受 userid、目录权限、IP 和单连接约束。

```bash
simple-sync-pull server.example ./downloads --username alice --identity /path/to/key --batch
simple-sync-pull server.example ./downloads --username alice --agent --batch --interval 30
```

identity 使用无口令私钥；加密私钥先载入 SSH Agent，再使用 agent 模式。batch 禁止密码提示，未选择 identity/agent 会报错。首次使用仍需预先核对主机指纹并建立 known_hosts，未关闭主机校验。

## 无头服务

先在桌面完成配置并停止服务，复制完整数据目录（包含主机密钥、同步数据库和 token 密钥）到服务运行账号可访问的位置，或在专用目录准备 config.json。仅复制配置会建立新的索引/密钥，不保留原身份与进度。

```bash
python -m sync_server.headless --data-dir /absolute/path/to/state --check-config
python -m sync_server.headless --data-dir /absolute/path/to/state
# 安装项目后也可使用 simple-sync-serve
```

入口不导入 Qt，前台运行供进程管理器托管。SIGINT/SIGTERM 停止监听、断开连接、等待扫描退出并关闭数据库与日志。数据目录有进程锁，不能让 GUI 和无头进程同时使用。解锁依赖操作系统句柄关闭，崩溃后不需要删除 server.lock 文件。

系统服务模板在 packaging/services，分发包中为 services：

- Linux：修改 simple-sync.service 中 User/Group、ExecStart 和 data-dir，创建可读写数据与共享目录的运行账号；管理员安装到 /etc/systemd/system 后执行 daemon-reload、enable --now。ExecReload 使用 SIGHUP。
- macOS：修改 org.simplesync.sftpserver.plist 的可执行文件和数据路径；按部署方式作为当前用户 LaunchAgent 或配置运行身份的 LaunchDaemon 安装。RunAtLoad 启动，异常退出后重启；需在真实登录/重启场景验收目录授权。
- Windows：simple-sync-winsw.xml 是 WinSW 包装配置，需自行提供 WinSW 可执行文件，并按 WinSW 的同名 XML 规则放置；修改 data-dir，管理员执行包装器 install/start。运行账号和 ACL 由部署者配置。程序不是可由 sc.exe 直接注册的原生 SCM 服务。

模板尚未进行对应系统的服务安装/开机验收，本次只验证 macOS 无头子进程的启动和 SIGTERM 正常退出。不会自动安装服务或修改本机开机设置。

## Windows 构建诊断

如果日志已显示 Build complete，但中文 --help 阶段出现 cp1252 UnicodeEncodeError，则是冻结程序标准输出编码问题。0.5.2 通过入口输出初始化和 PyInstaller UTF-8 运行选项修复，不关闭中文测试。可用 `python -m pytest tests/test_console.py -q` 在任意平台强制 CP1252 环境验证；构建脚本还会验证实际打包程序的中英文 UTF-8 输出。

如果 Actions 在 pytest 阶段失败，应先检查测试结果，尚不能据此判断 PyInstaller 打包失败。CI 现在在测试前运行 `python tools/diagnose_filesystem.py`，报告临时文件的路径/句柄时间字段与规范化签名；无论测试成功与否，都会尝试上传 `tests-<OS>-<arch>` JUnit 报告。扫描失败断言包含具体错误和根目录就绪状态。

将修复代码（包括新增模块/脚本）提交并推送后，对该新提交手动运行构建，或创建与 pyproject.toml 版本对应的新标签，例如 v0.5.1。直接重跑旧失败 run 仍使用原提交，不会自动获取后来的修复。当前提供的 Windows 日志为 17 failed、51 passed、1 skipped；这些是修复前结果，不代表修复后 Windows 已验收。

## 分类重载

编辑数据目录 config.json 后，桌面点击“热加载配置文件”；Unix 无头进程发送 SIGHUP，systemd 使用 systemctl reload simple-sync。

- 巡检/保留周期和登录预算更新后使用新值，不主动断开未变更的账号。
- 用户密码、公钥、权限、启用状态、同步规则、时间或名称改变：只断开该 userid 的全部连接，新登录使用新配置。旧快照的增量请求会被拒绝，规则重算在后续队列刷新时执行。
- 新增账号可使用已监控的根目录；停用/删除账号不会强制停止其他根目录连接。已移除的根目录监控保留至重启，避免重建所有监听器。
- 监听 IP/端口、新增监控根目录或修改已有账号根目录：拒绝热加载，需要停止后重启。

校验失败时运行配置保持不变；外部已经编辑的磁盘文件需要操作人修复。桌面编辑器仍在停止服务后编辑，热加载用于已准备好的磁盘配置。修改期间其他进程不要同时写 config.json。

## 内容复用和持续写入文件

SDK 按本地已同步记录的 SHA-256/大小寻找最多 16 个候选，重新校验实际内容并复制到状态目录临时文件，目标落盘后仍主动确认。先暂存再处理删除，支持重命名时 delete=true；不使用硬链接，避免本地修改相互影响。缓存损坏、候选不存在或符号链接会回退网络下载。节约网络流量的代价是本地摘要、复制和暂存磁盘空间。

持续追加的大文件仍使用文件级同步。推荐生产者写入排除规则中的临时文件，flush/fsync 并关闭后，在同一文件系统原子替换到发布路径；下次更新使用新的临时文件，避免继续修改已发布文件。日志采用轮转后的封闭文件发布，正在写入的日志目录排除。跨文件系统移动不能保证原子性。仅增加巡检间隔无法保证得到一致的大文件版本。

当前没有历史内容快照、跨版本块复用和跨句柄自动确认；普通客户端续传后需主动确认，SDK 已支持。块级差分和数据库替换保持为实测驱动的后续选项。

## 并发压测

```bash
python tools/benchmark_concurrency.py --files 2000 --users 4 --seconds 10
```

临时创建真实小文件，预先建立用户队列，再持续改写/扫描；多个真实 SFTP 连接发送 list/ack，另一路状态轮询。报告 P50/P95/P99/最大值和数据库指标。ack 是元数据负载，未实际下载文件，不能当作下载吞吐或完整同步结果。

文件数、文件大小、用户数、持续时间可调。脚本只监听 127.0.0.1，结束删除临时目录；所需空间随文件数/大小及用户队列增加，应先从小规模运行。百万条元数据基准仍保留在 benchmark_index.py，和此测试用途不同。
