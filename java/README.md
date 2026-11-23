# Simple Sync Java Client SDK (`simple-sync-client`)

适用于 **Simple Sync SFTP Server (`v0.3.0+` / `v0.4.0`)** 的 Java 17+ 客户端 SDK 与命令行示例。
底层基于 **Apache MINA SSHD (`sshd-sftp`)** 与 **Jackson**，完整实现了 [增量同步协议 v2](../docs/sync-protocol-v2.md) 的扩展报文收发、分页拉取、按版本断点续传、SHA-256 校验、原子文件替换与批量确认（`ack`）。

---

## 1. 环境准备（Conda 隔离环境）

本项目将 `openjdk=17` 与 `maven` 统一托管在 Conda 环境 `/Volumes/T9/sync/env/sync-env` 中，不污染宿主机全局环境：

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate /Volumes/T9/sync/env/sync-env
cd /Volumes/T9/sync/file-sync-server/java
```

构建并运行端到端集成测试（自动拉起 Python 无头服务端验证 Java 客户端）：

```bash
mvn clean package
```

构建完成后会在 `java/target/` 下生成两个构件：
- `simple-sync-client-0.4.0.jar`：轻量 SDK 库 JAR（供其他 Maven/Gradle 项目依赖引入）。
- `simple-sync-client-0.4.0-all.jar`：包含全部依赖的可执行 Fat JAR（可直接命令行运行示例）。

安装到本地 Maven 仓库供其他 Java 工程引用：

```bash
mvn clean install -DskipTests
```

---

## 2. 在其他 Java 项目中引入依赖

在目标项目的 `pom.xml` 中添加：

```xml
<dependency>
    <groupId>org.simplesync</groupId>
    <artifactId>simple-sync-client</artifactId>
    <version>0.4.0</version>
</dependency>
```

---

## 3. Java 代码集成示例

### 场景 A：使用高级 SDK (`SimpleSyncPullClient`) 自动完成一轮增量同步

[`SimpleSyncPullClient`](src/main/java/org/simplesync/client/SimpleSyncPullClient.java) 自动处理 `info` 协商、全量分页拉取、先删后建优先级排序、`.part` 断点续传、SHA-256 校验、同盘原子替换以及分批 `ack`：

```java
import org.apache.sshd.client.SshClient;
import org.apache.sshd.client.keyverifier.KnownHostsServerKeyVerifier;
import org.apache.sshd.client.keyverifier.RejectAllServerKeyVerifier;
import org.apache.sshd.client.session.ClientSession;
import org.apache.sshd.sftp.client.SftpClient;
import org.apache.sshd.sftp.client.SftpClientFactory;
import org.simplesync.client.SimpleSyncPullClient;
import org.simplesync.client.model.PullSummary;

import java.nio.file.Path;
import java.time.Duration;

public class Demo {
    public static void main(String[] args) throws Exception {
        Path knownHosts = Path.of(System.getProperty("user.home"), ".ssh", "known_hosts");
        Path destination = Path.of("./downloads");
        Path stateDir = Path.of("./.simple-sync-state");

        try (SshClient client = SshClient.setUpDefaultClient()) {
            client.setServerKeyVerifier(
                new KnownHostsServerKeyVerifier(RejectAllServerKeyVerifier.INSTANCE, knownHosts)
            );
            client.start();

            try (ClientSession session = client.connect("alice", "127.0.0.1", 2222)
                    .verify(Duration.ofSeconds(20))
                    .getSession()) {
                session.addPasswordIdentity("your-password");
                // 或使用 SSH 公钥认证: session.addPublicKeyIdentity(keyPair);
                session.auth().verify(Duration.ofSeconds(20));

                // Simple Sync Server 固定使用 SFTP v3
                try (SftpClient sftp = SftpClientFactory.instance().createSftpClient(session, 3)) {
                    SimpleSyncPullClient pullClient = new SimpleSyncPullClient(
                            sftp,
                            destination,
                            stateDir,
                            null,             // userid 传 null 时自动从服务端 info 发现
                            false,            // propagateDelete: 是否删除服务端已删文件
                            System.out::println
                    );

                    PullSummary summary = pullClient.runOnce();
                    System.out.printf("同步完成=%s, 剩余待同步=%d, 本轮成功=%d, 失败=%d%n",
                            summary.complete(), summary.pending(), summary.succeeded(), summary.failed());
                }
            }
        }
    }
}
```

### 场景 B：使用底层协议客户端 (`SimpleSyncProtocolClient`) 仅查询增量清单或自定义确认

如果业务系统只需要查询待同步文件清单（`info` / `list`）或自行控制下载与 `ack`，可直接使用 [`SimpleSyncProtocolClient`](src/main/java/org/simplesync/client/SimpleSyncProtocolClient.java)：

```java
import org.simplesync.client.SimpleSyncProtocolClient;
import org.simplesync.client.model.SyncAckItem;
import org.simplesync.client.model.SyncAckResult;
import org.simplesync.client.model.SyncFileEntry;
import org.simplesync.client.model.SyncInfo;
import org.simplesync.client.model.SyncListPage;

import java.util.List;

// 在已建立的 SftpClient (v3) 上构造协议客户端：
SimpleSyncProtocolClient protocol = new SimpleSyncProtocolClient(sftp);

// 1. 查询服务端能力与当前账号 userid
SyncInfo info = protocol.info();
String userid = info.userid();

// 2. 分页查询待同步增量列表 (limit: 1..200)
SyncListPage page = protocol.list(userid, 100, null);
for (SyncFileEntry file : page.files()) {
    System.out.printf("[%s] %s (version=%s, size=%s, sha256=%s)%n",
            file.operation(), file.path(), file.version(), file.sizeBytes(), file.sha256());
}

// 3. 下载并校验后主动确认 (ack)
SyncAckResult ackResult = protocol.ack(userid, List.of(
        SyncAckItem.success(file.fileId(), file.version(), file.sha256())
));
```

---

## 4. 直接运行命令行示例 (`SimpleSyncPullExample`)

**仅查看增量信息（不下载文件）：**

```bash
java -jar target/simple-sync-client-0.4.0-all.jar 127.0.0.1 \
  --port 2222 \
  --username alice \
  --list-only
```

**拉取增量文件到本地 `./downloads` 目录：**

```bash
java -jar target/simple-sync-client-0.4.0-all.jar 127.0.0.1 ./downloads \
  --port 2222 \
  --username alice \
  --identity ~/.ssh/id_ed25519 \
  --delete \
  --interval 30
```
