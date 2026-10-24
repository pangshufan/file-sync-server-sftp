> 历史文档：仅适用于 0.2.x。0.3.0 起使用 [协议 v2](sync-protocol-v2.md)，旧设备/任务进度不自动合并。

# Simple Sync SFTP 扩展协议 v1

[English](en/sync-protocol-v1.md)

状态：项目内实验协议。单一服务端发布文件，多个用户/设备单向下载最新内容；不提供双向合并或历史文件内容。

## 1. 传输与发现

复用已认证的 SSH `sftp` subsystem，协议版本保持 SFTP v3。普通客户端继续使用标准文件指令。

服务端在 `SSH_FXP_VERSION` 中公告以下扩展，每个扩展的版本字符串为 ASCII `1`：

```text
sync-info@simple-sync.invalid
sync-open@simple-sync.invalid
sync-manifest@simple-sync.invalid
sync-changes@simple-sync.invalid
sync-ack@simple-sync.invalid
```

`simple-sync.invalid` 是项目的临时私有命名空间，没有声称拥有任何第三方域名。正式公开发布前应选定自有域名并通过兼容别名迁移；第三方开发应集中配置此常量。

请求包（省略外层 packet length）：

```text
byte    SSH_FXP_EXTENDED = 200
uint32  request_id
string  extension_name
string  utf8_json_request
```

响应包：

```text
byte    SSH_FXP_EXTENDED_REPLY = 201
uint32  request_id
string  utf8_json_response
```

这里的 `string` 使用 SSH 标准编码：4 字节大端字节数＋对应字节。JSON 对象不允许跟随额外字段。请求 JSON 最多 64 KiB。

成功：`{"ok":true,"result":{...}}`。

业务失败：`{"ok":false,"error":{"code":"INDEX_NOT_READY","message":"..."}}`。

无法识别的扩展、损坏的 SSH 数据包等使用普通 `SSH_FXP_STATUS`。客户端不能假定所有错误都采用 JSON。

序号、文件版本、字节大小和纳秒修改时间在 JSON 中使用十进制字符串。任务/轮次 ID 是不透明字符串，客户端不要解析。业务路径是用户共享根目录内以 `/` 开头的 UTF-8 虚拟路径，不暴露服务器本机路径。

## 2. info

请求：`{}`。

返回示例：

```json
{
  "protocol": "1",
  "namespace": "simple-sync.invalid",
  "hash": "sha256",
  "max_page_size": 200,
  "mode": "server-to-client-latest",
  "operations": ["info", "open", "manifest", "changes", "ack"]
}
```

客户端应检查版本及摘要算法；未知版本不要直接继续同步。

## 3. open

```json
{"device_id":"persistent-device-id","name":"default","scope":"/reports","reset":false}
```

- `device_id` 必填，`name` 默认 `default`，均为 1–128 字符。
- `scope` 默认 `/`；目录及其后代纳入范围，禁止 `..`。
- `(已认证账号, device_id, name)` 唯一标识任务。不同本地目标必须使用独立设备状态或独立任务名。
- `reset:true` 强制重新建立完整清单。
- 请求不接受用户名作为授权依据。账号来自 SSH 会话。

返回：

```json
{
  "task_id":"opaque-task-id",
  "cursor":"1050",
  "mode":"changes",
  "created_at":1792630000.0,
  "last_sync_at":1792631000.0
}
```

时间字段是 Unix 秒数；`last_sync_at` 初始为 `null`。首次任务返回 `mode:"manifest"`。

目录/权限/同步范围发生变化，或游标早于日志保留边界时，保留任务身份并自动重建清单。旧分页令牌和轮次不再有效。客户端重新 open 后应使用返回的 mode。

服务启动后后台建立索引，首次完整扫描完成前返回 `INDEX_NOT_READY`。客户端稍后重试。

## 4. manifest / changes

```json
{"task_id":"...","limit":100,"page_token":null}
```

`limit` 范围 1–200。初次请求创建并持久化轮次，固定 `upper_cursor`；后续请求和断线重连复用该轮次。manifest 复制当前索引，changes 将上次完成游标到本轮上界之间的变化按路径合并为最新状态。

```json
{
  "task_id":"...",
  "round_id":"...",
  "upper_cursor":"1080",
  "has_more":true,
  "next_page_token":"opaque-signed-token",
  "changes":[
    {
      "path":"/reports/a.csv",
      "version":"1051",
      "operation":"upsert",
      "kind":"file",
      "size_bytes":"20480",
      "mtime_ns":"1792631000000000000",
      "sha256":"hex-digest",
      "status":"pending",
      "error":null
    }
  ]
}
```

- `operation` 为 `upsert` 或 `delete`；目录移动表现为旧路径删除与新路径创建。
- `kind` 为 `file` 或 `directory`。目录和删除项的摘要为 `null`。
- `status` 为 `pending`、`failed`、`done`。恢复任务时可跳过 done 项。
- 分页顺序按路径排列，客户端执行操作时还应处理依赖关系：先删文件，再从深到浅删目录，再从浅到深创建目录，最后下载文件。
- 文件需要下载权限；目录需要浏览或下载权限。不返回无权访问文件的路径、摘要或删除记录。
- 分页令牌由服务端签名并绑定任务和轮次，只可原样传回，不能自行使用全局序号构造下一页。
- 翻页只更新清单读取进度，不推进已完成游标。
- 若同一文件在一轮内变化多次，只需最终同步最新状态，不要求回放每个历史版本。

scope 保留完整虚拟路径。例如 scope `/reports` 的 `/reports/a.csv` 落到 SDK 本地目标的 `reports/a.csv`。

## 5. 下载与确认

文件内容通过标准 `OPEN/READ/CLOSE` 下载。建议写入本地临时文件，检查大小和 SHA-256 后原子替换目标。

确认成功：

```json
{
  "task_id":"...",
  "round_id":"...",
  "items":[{"path":"/reports/a.csv","version":"1051","status":"done","sha256":"hex-digest"}]
}
```

确认失败：

```json
{
  "task_id":"...",
  "round_id":"...",
  "items":[{"path":"/reports/a.csv","version":"1051","status":"failed","error":"disk full"}]
}
```

每批最多 200 项，同时受 64 KiB 请求大小约束。SDK 默认合并约 50 项并按字节数拆批。账号归属、已提供的路径及版本、文件摘要均会校验。确认是客户端对本地成功的声明，服务器不能独立证明客户端已持久写盘。

删除处理由客户端选择：镜像模式删除对应文件/空目录；保留模式保留本地副本并确认该删除事件已处理。改变删除策略不会自动重放已确认的删除；需要重新建立清单。

读取完所有分页、确认全部条目后：

```json
{"task_id":"...","round_id":"...","finish":true}
```

返回：

```json
{"complete":true,"pending":0,"cursor":"1080","refreshed":[]}
```

只有完整枚举且所有条目完成时才推进任务游标。某个文件失败，之后成功的文件可以保留完成记录，但游标不会越过失败项。最后一次 finish 的重复请求可安全重试。

## 6. 查询后源文件再次变化

文件版本不是历史文件存储。普通 SFTP 读取可能拿到较新的内容，所以必须核对清单摘要。

摘要不一致或文件消失时不要确认旧版成功。发送：

```json
{
  "task_id":"...",
  "round_id":"...",
  "items":[{"path":"/reports/a.csv","version":"1051","status":"refresh"}]
}
```

服务端查询当前索引，将该条目更新到较新文件版本或删除版本，并通过 `refreshed` 数组返回条目。重新下载并验证。若索引暂未追上文件事件，可能返回相同版本，客户端应延后重试。

允许确认高于本轮上界的新版本，但本轮游标仍只推进到原上界。下一轮通过逐文件完成版本跳过已经覆盖的旧事件。这样无需保留所有历史内容，也不会丢失后续其他文件变化。

## 7. 恢复与生命周期

- 任务、清单轮次、逐文件完成状态都保存在 SQLite，服务重启不会重置它们。
- 客户端必须持久保存设备标识。删除客户端状态目录会产生新设备身份。
- 日志默认保留 30 天，过旧客户端重新核对完整清单。
- 权限撤销后不再提供相关条目；不会远程擦除已下载的文件。
- 初次清单和增量上界是索引快照，不是磁盘内容快照；源文件稳定后可最终收敛到最新版本。
- 本协议的增量是文件级，不是大文件的数据块差分。
- 同一任务不支持两个进程同时消费；每个设备任务应只有一个同步执行者。

## 8. 业务错误码

| code | 客户端处理 |
| --- | --- |
| INDEX_NOT_READY | 等待初次索引完成 |
| BAD_REQUEST / BAD_TOKEN | 修正请求；不要反复重试错误参数 |
| TASK_NOT_FOUND | 核对当前账号和任务身份 |
| POLICY_CHANGED / CURSOR_EXPIRED | 重新 open，按返回 mode 建立清单 |
| WRONG_MODE | 使用 open 返回的 mode，先完成已有轮次 |
| VERSION_MISMATCH | 重新查询条目，不能跳过版本检查 |
| CHECKSUM_MISMATCH | 检查下载内容及摘要 |
| ROUND_NOT_FOUND | 重新 open 并查询当前轮次 |
| SERVER_STOPPING | 重连后恢复任务 |

## 9. 组件适配

服务端和 SDK 的 AsyncSSH 内部接口统一封装在 `transport.py`，依赖固定为 `2.24.1`，避免全局 monkey patch。升级依赖必须运行互通测试。

`examples/paramiko_pull.py` 是不依赖本项目 SDK 的独立示例，验证过 Paramiko 4.0.0。它封装 Paramiko 的 `_request`，其余文件下载使用普通 `get()`。该小示例保留删除文件、按页顺序执行；大目录、目录类型替换和可靠重试应使用完整 SDK 或按本文实现。

协议机制参考：[SFTP v3 扩展说明](https://www.openssh.org/txt/draft-ietf-secsh-filexfer-02.txt)。

当前 examples/paramiko_pull.py 已改为 v2 示例，不再是 v1 实现。
