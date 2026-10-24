# userid 增量同步协议 v2

[English](en/sync-protocol-v2.md)

适用于应用 0.3.0 及以上版本。单一 server 发布文件，每个 userid 只有一份共享进度。没有设备、任务、全量清单、客户端重置时间或客户端选择范围接口。普通 SFTP 文件传输保持兼容。

## 1. 发现、报文与认证

在 SFTP v3 的 `SSH_FXP_VERSION` 中公告以下扩展，扩展版本为 ASCII `2`：

```text
sync-v2-info@simple-sync.invalid
sync-v2-list@simple-sync.invalid
sync-v2-ack@simple-sync.invalid
```

`.invalid` 是项目的临时私有命名空间，正式公开发布前应选择自有域名并提供兼容别名。v1 的 `open/manifest/changes/ack` 不继续提供；不要混用两代报文。支持协议发现的库可读取握手公告，不暴露公告的库可尝试 info 并处理 `SSH_FX_OP_UNSUPPORTED`。

请求（省略外层 packet length）：

```text
byte    SSH_FXP_EXTENDED = 200
uint32  request_id
string  extension_name
string  UTF-8 JSON object
```

响应：

```text
byte    SSH_FXP_EXTENDED_REPLY = 201
uint32  request_id
string  UTF-8 JSON object
```

`string` 为 4 字节大端长度加内容。请求 JSON 不超过 64 KiB，不允许尾随 SSH 字段。文件名须能通过 UTF-8 SFTP 路径访问。

成功：`{"ok":true,"result":{...}}`。

业务失败：`{"ok":false,"error":{"code":"USER_MISMATCH","message":"..."}}`。

未知扩展、SSH 报文结构错误等仍返回标准 SFTP STATUS。客户端应同时处理标准错误和 JSON 错误。

server 从 SSH 登录身份取得 userid。list 和 ack 必须携带相同 userid；知道其他用户的 userid 不构成授权。目录和文件过滤还会检查该用户的既有访问权限。

## 2. info

请求：`{}`。

```json
{
  "protocol":"2",
  "namespace":"simple-sync.invalid",
  "userid":"32位固定标识",
  "hash":"sha256",
  "confirmation":"manual",
  "sync_enabled":true,
  "incremental_start":0,
  "directories":["/reports"],
  "max_page_size":200,
  "operations":["info","list","ack"],
  "server_time":1792742400.125
}
```

客户端必须核对 protocol 和 hash。userid 可以由服务端发现，也可以由调用者预先提供并与 info 结果核对。所有业务时间为 server 产生的 Unix 秒数；显示时再转换时区。文件版本、处理位置和字节大小采用十进制字符串，避免跨语言大整数精度问题。

## 3. list：分页查询增量待同步清单

```json
{"userid":"...","limit":100,"page_token":null}
```

`limit` 为 1–200。server 依据用户配置的目录、包含/排除规则、权限及起始时间计算待同步清单。客户端不能选择全量模式、扩大目录或修改起始时间。

```json
{
  "userid":"...",
  "server_time":1792742400.125,
  "processed":"1050",
  "pending":12,
  "failed":2,
  "synced":80,
  "has_more":true,
  "next_page_token":"不透明签名令牌",
  "files":[
    {
      "file_id":"64位固定标识",
      "path":"/reports/a.csv",
      "version":"1048",
      "updated_at":1792742390.25,
      "operation":"upsert",
      "kind":"file",
      "size_bytes":"20480",
      "sha256":"十六进制SHA-256",
      "status":"pending",
      "error":null
    }
  ]
}
```

- `pending` 包含待处理和失败项；`failed` 是其中失败项数量。
- `synced` 为当前完成明细与精简归档记录的数量。统计同时包含目录项。
- `processed` 是内部已处理变化位置，不是已下载位置。推进它不会丢弃失败项。
- `operation` 为 `upsert` 或 `delete`；`kind` 为 `file` 或 `directory`。目录和删除项的 sha256 为 null。
- 路径相对于账号共享根目录，以 `/` 开头。目录配置 `/reports` 返回的路径仍为 `/reports/a.csv`，不会去掉前缀。
- 文件只有下载权限时才可进入清单；目录需要浏览或下载权限。过滤不允许绕过 SFTP 自身的目录权限。
- 同一路径具有固定 file_id，内容更新改变版本；改名表现为旧路径删除和新路径新增。
- 多次更新合并到最新待同步版本，不承诺提供历史文件内容。

列表分页按 file_id 排序。令牌绑定 userid、设置修订号、上界和上一页位置，必须原样传回。修改起始时间、范围、规则或权限会使旧令牌失效，需要从第一页重新查询。

分页期间新产生的变化可能不在本次分页内，会继续留在待同步清单中，下次从第一页查询即可取得；翻页不能作为成功确认。不要把首次列表的 pending 数量当成本轮必须成功的固定数量。

查询某几个文件的最新待同步版本可使用：

```json
{"userid":"...","file_ids":["..."],"limit":100}
```

每次 1–200 个 file_id，不能同时使用 page_token。已成功的文件不会返回。这用于下载时内容变化后的重试，不是历史版本读取接口。

## 4. 标准 SFTP 下载

使用正常的 `OPEN/READ/CLOSE` 或组件的 `get()`；无独立下载扩展。建议：

1. 查询待同步列表。
2. 下载到临时文件，必要时按版本断点续传。
3. 验证大小、SHA-256，并将内容持久保存到目标。
4. 通过 ack 主动确认对应版本。

SDK 会先将清单暂存到本地 SQLite，然后先删除文件、从深到浅删除目录、从浅到深创建目录，再下载文件，以处理类型变更和目录依赖。默认保留本地删除项；`--delete` 也仅删除对应文件和空目录，不递归删除额外本地内容。

源文件在下载期间再次变化时，下载摘要可能不匹配。不要将最新版本的摘要用于确认旧版本；重新按 file_id 查询最新版本并重试。server 没有历史内容快照。

## 5. ack：主动确认或报告失败

```json
{
  "userid":"...",
  "files":[
    {"file_id":"...","version":"1048","status":"success","sha256":"..."},
    {"file_id":"...","version":"1049","status":"failed","error":"disk full"}
  ]
}
```

每批 1–200 项，同时受 64 KiB 请求限制。SDK 默认约 50 项并按字节数拆批。文件 upsert 成功必须携带与提供版本匹配的 SHA-256；目录、删除事件不要求摘要。

```json
{
  "userid":"...",
  "complete":false,
  "pending":1,
  "failed":1,
  "synced":81,
  "server_time":1792742500.5
}
```

- 正常确认只接受该用户被提供过的版本；重复成功确认是幂等的。
- 若确认 1048 时文件已更新到 1055，server 记录 1048 成功，但保留 1055 待同步，不能用确认时间覆盖文件版本判断。
- 旧版成功不会清除新版失败信息。
- 确认时间由 server 写入，忽略客户端自行附加的时间字段。
- 没有 finish/轮次提交接口，每个文件独立持久保存结果。
- 手动模式中，普通 SFTP 完整下载不会改变同步状态。
- 自动模式也允许主动 ack，作为断点续传、目录、删除及校验落盘的补充。
- 删除确认表示客户端已经按其策略处理事件；保留模式可保留本地副本并确认已处理。

## 6. 自动确认边界

server 仅对用户开启自动确认且仍待同步的普通文件进行跟踪。必须满足：

- 单个只读文件句柄的实际读取区间完整覆盖 `[0, size)`；重复区间只计算一次。
- 文件内容对应索引版本，打开和关闭时的大小、mtime、ctime、inode 都一致，路径未被替换。
- 没有读取错误，且客户端发送了正常的 `SSH_FXP_CLOSE`。

空文件可在正常关闭只读句柄时确认。读取区间过度碎片化时放弃自动确认；上限为 4096 个区间。

以下情况不会自动确认：部分读取、仅 EOF 读取、跨句柄分段拼接、从中间开始续传、断线后的句柄清理、源文件变化、目录浏览、server 内部 copy-data 复制。只统计实际 FXP_READ 响应的读取区间。目录项和删除事件仍需主动 ack；只使用普通 SFTP 客户端时，即使全部实际文件都传完，目录/删除项仍可能显示待处理。完整 SDK 会明确确认这些项。

自动确认的保证仅为“server 判断内容完整传出”，不是客户端本地持久写盘或摘要验证证明。需要确认客户端落盘时选择 manual。

## 7. 增量时间、进度与归档

候选条件：目录与规则匹配、有权限、server 更新时间 `>=` 配置起始时间，且未成功确认当前版本。初次观察到文件时，以观察时间作为 server 更新时间，不能从文件系统 mtime 恢复尚未记录的历史。

server 使用三层状态：

1. 共享 catalog：每个路径的最新内容或删除版本及 server 更新时间。
2. 每用户 processed：新变化已经落入该用户清单的位置。
3. 每用户文件状态：目标版本、已成功版本、失败信息及确认来源。

普通查询只计算 processed 后的新变化，再读取索引化的待同步集合，不反复比较全部成功文件。即使滚动变更日志过期，最新 catalog 和持久待同步状态仍可恢复离线用户。

server 调整起始时间时：

- 默认保留未完成项，重新筛选时间范围；已有成功版本继续跳过。
- “从新时间重新开始”清除该用户待同步、成功和归档状态，较早遗漏可能不再补发。
- 起始时间向过去调整会重新计算相关范围，但不会导出一个单独的全量模式。

成功明细达到保留期后分批精简为 `(userid, file_id, version, completed_at)`，不会清除必要的成功版本。新变化可重新激活该文件。用户计数采用事务内维护的缓存，不依赖每次 COUNT 全部历史记录。

已提供的历史版本确认凭证可过期，最新待同步版本不会因凭证保留期丢失。过旧确认收到 VERSION_MISMATCH 后应重新查询最新版本。失败项和未完成项不随成功明细归档而删除。

## 8. 错误码与适配

| code | 处理 |
| --- | --- |
| USER_MISMATCH | 核对当前 SSH 账号及 userid |
| USER_SETTINGS_CHANGED | 服务端已更新账号配置，重新登录后查询 |
| SYNC_DISABLED | 由 server 管理员启用用户增量设置 |
| INDEX_NOT_READY | 等待首次扫描完成 |
| BAD_REQUEST | 修正 JSON、字段或分页大小 |
| BAD_TOKEN | 从第一页重新查询，不能自行修改令牌 |
| VERSION_MISMATCH | 重新查询最新待同步版本 |
| CHECKSUM_MISMATCH | 校验实际下载内容，不要推进状态 |
| SERVER_STOPPING | 重连后重新查询并继续 |

单连接/IP 限制在 SSH 登录阶段拒绝请求，不返回上述 JSON。网络断开和标准 SFTP 错误由组件的原生异常表示。

AsyncSSH 2.24.1 的内部适配统一在 `transport.py`，没有全局 monkey patch；升级版本须回归测试。独立示例 `examples/paramiko_pull.py` 已验证 Paramiko 4.0.0 的扩展请求和标准 get，不依赖本项目 SDK；小示例保留删除文件，不代替 SDK 的复杂目录调度和断点续传。

0.5 起，错误 message 可按服务端语言显示中文或英文；扩展名、字段、操作/状态和错误码保持不变。客户端应按 code 判断，不匹配 message 文案。没有按单个 SFTP 客户端协商语言的接口。
