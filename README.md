# Simple Sync · SFTP 文件服务

简体中文 | [English](README.en.md)

Python + PySide6 桌面服务端，当前版本 0.5.1。提供多用户 SFTP、目录权限、登录限制，以及 **server 配置、按 userid 共享进度的文件级增量同步**。

## 启动与环境

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate /Volumes/T9/sync/env/sync-env
cd /Volumes/T9/sync/file-sync-server
python -m sync_server
```

也可双击 [start.command](start.command) 或运行 `simple-sync-server`。启动脚本优先使用 `SIMPLE_SYNC_PYTHON` 指定的解释器，其次使用项目同级 `env/sync-env/bin/python`，最后尝试 PATH 中的 python3。

新环境：

```bash
conda env update --prefix /Volumes/T9/sync/env/sync-env --file environment.yml
conda activate /Volumes/T9/sync/env/sync-env
```

锁定依赖安装：

```bash
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
```

已验证 Python 3.12.15、AsyncSSH 2.24.1、PySide6 Essentials 6.12.0、watchdog 6.0.0、Paramiko 4.0.0，运行平台为当前 macOS arm64；Windows/Linux 尚未进行运行验收。

## 第一次使用

1. 设置监听 IP 和端口。默认 `127.0.0.1:2222`，局域网访问选择本机 IP，或 `0.0.0.0` 监听全部 IPv4 网卡。客户端连接实际主机 IP。
2. 添加用户，选择该用户的共享根目录并授予所需目录权限。账号独立于操作系统用户。
3. 在用户的“增量同步设置”页配置目录、起始时间、文件规则和确认方式。默认起始时间为 0；新收录文件使用 server 首次观察时间。
4. 保存并启动服务。索引在后台建立，首次扫描完成后增量接口可用。
5. 用 SFTP 客户端连接，核对桌面显示的 SHA256 主机指纹。防火墙需允许监听端口。

“当前连接”显示用户、来源 IP、来源端口、连接时间、在线时长和共享目录，可以主动断开。关闭主窗口会停止服务并断开连接。桌面编辑用户设置前先停止服务；运行时可编辑数据目录内的 config.json，再点击“热加载配置文件”。重载只断开设置变化的账号，监听地址/端口和新增监控根目录需要重启，详见 [运维说明](docs/OPERATIONS.md)。

界面跟随系统浅色/深色配色，运行中切换系统主题会自动更新，无需重启。主题覆盖主窗口、用户/权限与同步设置弹窗、输入框、表格、选中项和禁用状态；字体颜色随背景一起调整。系统未提供主题信息时使用启动时的系统调色板判断。Linux 的系统主题通知取决于桌面环境和 Qt 平台插件。

界面启动时按系统 UI 语言偏好选择简体中文或英文；未匹配时回退英文。更改系统语言后重启应用，可用 `SIMPLE_SYNC_LANGUAGE=en` 或 `zh` 临时覆盖。配置、userid 和协议字段不变，详见 [多语言说明](docs/LOCALIZATION.md)。全部文档均提供 [英文版本](README.en.md#english-documentation)。

## 用户、登录和目录权限

userid 为固定标识，编辑用户名不会改变它。每个 userid 只有一份增量进度；同账号在两台机器上使用时，共享同步结果，不分别维护设备任务。

| 设置 | 行为 |
| --- | --- |
| 单连接 | 默认开启；同 userid 已登录时拒绝新连接，不踢掉原连接。不同用户可并行登录 |
| 登录 IP / CIDR | 可填写多个 IP 或网段，界面用分号分隔；空表示不限制，支持 IPv4 和 IPv6 |
| 账号启用 | 停用后拒绝登录 |
| 根目录 | 客户端 `/` 对应所选本机目录 |
| 子目录规则 | 最长路径匹配，完整覆盖上级权限；全不勾选即拒绝访问 |

根目录权限分别控制浏览、下载、上传/覆盖、创建目录、重命名/移动、删除。新账号默认只读。移动的目标还需上传权限；覆盖式重命名的目标还需删除权限。移动不能扩大文件可见性或绕过子目录规则。

macOS/Windows 权限匹配忽略大小写并规范化 Unicode，Linux 区分大小写。不要依赖仅大小写不同的目录配置不同权限。每个用户只有一个物理根目录，但可在根目录下配置多个同步目录。

## 增量同步设置

| 字段 | 含义 |
| --- | --- |
| 同步开关 | 关闭只禁用增量接口，普通 SFTP 不受影响 |
| 同步目录 | 每行一个虚拟路径，如 `/reports`，受已有权限约束 |
| 增量起始时间 | server 的 Unix 时间下限；界面按 server 本地时区显示，包含边界值 |
| 包含规则 | 每行一个通配规则，默认 `*`；空列表表示不包含文件 |
| 排除规则 | 优先于包含规则，如 `*.tmp`、`private/**` |
| 确认方式 | 手动或自动，默认手动 |
| 从新时间重新开始 | 有确认提示；清除该用户旧待同步、成功及归档状态后重新筛选 |

规则按去掉开头 `/` 的完整路径或文件名匹配，使用 Python fnmatch 语义，`*` 可匹配 `/`。目录项不按文件包含规则过滤，但受目录范围、排除规则和权限约束。

增量接口没有全量模式。完整目录由用户自行通过普通 SFTP 下载，例如：

```bash
sftp -P 2222 alice@127.0.0.1
# 在交互界面中按需要使用 get / get -r
```

server 使用大小、纳秒 mtime、ctime、inode 快速筛选，SHA-256 确认内容变化；只改时间而内容相同不产生新业务版本。业务更新时间为 server 确认变化的时间，不能用历史文件 mtime 代替。server 在索引前不知道的历史，不会被虚构为历史更新时间。

## 手动与自动成功确认

**手动**：客户端完整下载、校验并保存后，通过 JSON 主动确认对应 file_id、版本和摘要。普通 SFTP 下载本身不改变同步状态。

**自动**：server 跟踪单个只读句柄的实际读取区间，要求覆盖整个文件、文件签名未变化、无错误并收到正常 CLOSE。部分读取、重复读取但有缺口、仅 EOF、断线清理、跨句柄分段、从中间续传以及 server 内部 copy-data 复制不触发成功。空文件正常关闭可完成确认。

自动确认只表示内容完整传出，不能证明客户端校验或落盘。目录项、删除事件仍需主动确认；因此普通客户端完成实际文件下载后，目录/删除记录可能仍待处理。SDK 会明确确认这些项，断点续传后也主动确认。自动模式允许主动确认作为补充。

下载期间出现新版本时，只能确认实际下载版本，新版本仍待同步。失败文件不会被其他文件的成功时间越过。

## 时间重设与长期运行

“已处理变化位置”和“已同步文件版本”独立保存。server 只处理新的变化，再查询持久待同步集合；同文件持续更新合并为最新待同步版本，不每次遍历全部成功记录。

默认调整起始时间会保留未完成项。要放弃旧积压，显式勾选“从新时间重新开始”。向过去调整时补算该范围，已有成功版本仍可跳过。最近成功时间只供展示，不能直接作为过滤下限。

成功明细到期后分批精简为 userid/file_id/成功版本/完成时间；必要版本保留，统计用事务计数维护。最新 catalog 包括删除标记，不依赖完整保留全部历史变化来恢复离线用户。

全量元数据巡检默认按规模自动选择：≤1 万项 10 分钟、≤10 万项 1 小时、更大目录 4 小时，可指定秒数。watchdog 原生事件约 2 秒去抖，持续事件最多合并约 10 秒。摘要复核默认 7 天分批轮转，变更日志和成功明细保留默认 30 天。实际摘要覆盖速度受磁盘和文件体积影响。扫描失败或磁盘掉线不会直接发布批量删除。

## Python SDK 和命令行

先通过普通 SFTP 核对指纹并建立 `~/.ssh/known_hosts` 信任记录，然后执行：

```bash
conda activate /Volumes/T9/sync/env/sync-env
simple-sync-pull 127.0.0.1 ./downloads --username alice --interval 30
```

默认交互输入密码。无人值守可使用 `--identity /path/to/private-key --batch` 或 `--agent --batch`；公钥在用户的“SSH 公钥”页配置，可选择仅公钥登录。省略 interval 执行一轮后退出；持续模式断线重连。默认保留服务端删除的本地副本；`--delete` 传播删除，但只删除对应文件或空目录，不递归清除额外本地内容。

其他参数：`--port`、`--known-hosts`、`--state`、`--userid`。userid 可从 info 自动发现；显式传入必须匹配账号。没有 `--name`、`--scope`、`--reset`，这些设置由 server 管理。

```python
from pathlib import Path
import asyncssh
from sync_server.client import PullClient

async def pull(password):
    async with asyncssh.connect('127.0.0.1', 2222,
                                username='alice', password=password) as connection:
        async with connection.start_sftp_client() as sftp:
            client = PullClient(sftp, Path('downloads'), Path('client-state'))
            try:
                return await client.run_once()
            finally:
                client.close()
```

客户端状态目录在同步目标之外，默认放在目标同级隐藏目录，保存摘要缓存、清单暂存和按版本断点临时文件；它不再代表独立 server 进度。不要让多个进程同时操作同一个本地状态目录。不同下载目标可用独立本地缓存，但同 userid 的 server 完成状态仍共享。

下载经临时文件、大小和摘要校验后替换目标，再批量确认。重命名/新路径可从本地已同步文件按摘要和大小复用内容；删除旧路径前先校验并暂存副本，不创建硬链接。缓存候选不存在或校验失败时回退普通下载。每项每轮最多 3 次尝试，未完成项下轮继续。scope 路径保留完整虚拟前缀，例如 `/reports/a.csv` 下载到 `downloads/reports/a.csv`。请使用专用下载目录；被纳入同步的本地目标会被更新。

客户端不监听本地修改。server 没有新变化时，本地删除或篡改不自动修复；需要完整副本时使用普通 SFTP，不通过重置 userid 进度伪装成全量任务。

## 桌面同步状态

“增量同步”页显示索引、userid、起始时间、待同步、失败、已同步（含归档）、已处理位置和最近成功时间。待同步数包含失败。可查看待同步、失败及成功明细，按 200 项翻页，显示目标版本、成功版本、server 时间、确认方式、IP 和错误。归档明细仅保留必要版本，故不在详细列表中展示。

## 数据、迁移和边界

应用数据目录显示在窗口底部。配置为 `config.json`，数据库为 `sync.sqlite3`，主机密钥为 `ssh_host_ed25519_key`。密码采用带随机盐的 scrypt 哈希；支持 POSIX 权限的平台上，数据目录 0700，配置、数据库和密钥 0600；Windows 访问范围由 ACL 决定，chmod 不提供同等的权限位语义。WAL 文件位于受保护目录中。数据库不要置于网络共享或同步目标内。

[升级/回滚说明](docs/MIGRATION.md) 解释自动备份、旧进度不合并、userid 生成和客户端状态迁移。升级前停止旧程序；首次升级会备份配置及 SQLite 的一致性快照。

[协议 v2](docs/sync-protocol-v2.md) 是当前对外约定；[协议 v1](docs/sync-protocol-v1.md) 仅作历史参考。[独立 Paramiko 示例](examples/paramiko_pull.py) 不依赖 SDK，展示 v2 查询、标准 get 和主动确认。

服务仅提供 SFTP v3，禁用 Shell、命令、SCP 和端口转发；拒绝符号链接、多硬链接及设备/FIFO，禁止修改系统所有者/权限。客户端应关闭“保留原文件权限”。路径隔离是应用级机制，本地目录应由可信进程管理，不是针对本机恶意并发替换的操作系统沙箱。

提供 macOS、Windows、Linux 便携应用打包配置，详见下节。提供无 Qt 的无头服务入口与 systemd、launchd、Windows WinSW 服务模板，需按部署路径配置并手动安装。当前不提供历史内容快照、块级差分或多人双向冲突合并。运行日志持久保存为轮转 JSONL，窗口显示最近 2000 条；日志不记录密码、私钥和完整配置。详见 [运维说明](docs/OPERATIONS.md)。

## 应用图标与打包

应用已接入文件夹与同步箭头图标，提供 SVG、PNG、Windows ICO 和 macOS ICNS。源文件位于 `src/sync_server/assets/app-icon.svg`。

```bash
python -m pip install ".[build]"
python tools/build_app.py
```

在目标平台本机构建，`dist/` 输出桌面服务端、无头服务端、独立命令行客户端及压缩分发包；目标机器无需 Conda。仓库附带 GitHub Actions 三平台构建流程。[打包说明](docs/PACKAGING.md) 包含产物路径、图标再生成、部署、签名与验收边界。

## 验证与开发

```bash
python -m pytest -q
python tools/check_translations.py
python tools/benchmark_concurrency.py --files 2000 --users 4 --seconds 10
python tools/benchmark_index.py --files 100000
python tools/benchmark_index.py --files 1000000
```

集成测试使用临时目录、本机回环端口和 Qt offscreen，不覆盖正式用户数据。覆盖迁移、权限、单连接/IP、增量时间、确认、恢复、原生监听、SDK、Paramiko 及桌面流程。

并发基准创建临时真实文件，通过本机 SFTP 测量扫描期间 list/ack 和状态查询延迟，不测试下载吞吐；详见运维文档。合成基准只创建 SQLite 元数据，不创建相应源文件，测量索引、初次待同步生成和后续分页。不能据此推算真实磁盘扫描、摘要或网络传输耗时；多用户队列会额外占用磁盘。具体结果见 [验证记录](docs/VALIDATION.md)。

代码入口：`config.py` 配置，`migration.py` 备份，`index.py` 共享目录，`sync_api.py` 用户队列/计数，`server.py` SFTP/登录/自动确认，`transport.py` 协议适配，`monitor.py` 监听巡检，`ui.py` 界面，`client.py` SDK/CLI，`headless.py` 无头服务，`auth_limits.py` 登录预算，`audit.py` 持久运行日志。
