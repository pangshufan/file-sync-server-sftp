package org.simplesync.client.example;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.apache.sshd.client.SshClient;
import org.apache.sshd.client.keyverifier.AcceptAllServerKeyVerifier;
import org.apache.sshd.client.keyverifier.KnownHostsServerKeyVerifier;
import org.apache.sshd.client.keyverifier.RejectAllServerKeyVerifier;
import org.apache.sshd.client.keyverifier.ServerKeyVerifier;
import org.apache.sshd.client.session.ClientSession;
import org.apache.sshd.common.config.keys.FilePasswordProvider;
import org.apache.sshd.common.util.security.SecurityUtils;
import org.apache.sshd.sftp.client.SftpClient;
import org.apache.sshd.sftp.client.SftpClientFactory;
import org.simplesync.client.SimpleSyncProtocolClient;
import org.simplesync.client.SimpleSyncPullClient;
import org.simplesync.client.SyncException;
import org.simplesync.client.model.PullSummary;
import org.simplesync.client.model.SyncInfo;
import org.simplesync.client.model.SyncListPage;

import java.io.Console;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyPair;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Runnable Java example &amp; CLI for Simple Sync SFTP Server (Protocol v2).
 * <p>
 * Supports:
 * <ul>
 *     <li>Inspecting incremental status &amp; pending manifest only ({@code --list-only})</li>
 *     <li>Running a single incremental pull round or continuous polling ({@code --interval})</li>
 *     <li>Password authentication or SSH private key authentication ({@code --identity})</li>
 * </ul>
 */
public class SimpleSyncPullExample {

    public static void main(String[] args) {
        try {
            int exitCode = runCli(args);
            System.exit(exitCode);
        } catch (IllegalArgumentException e) {
            System.err.println("参数错误: " + e.getMessage());
            printUsage();
            System.exit(2);
        } catch (Exception e) {
            System.err.println("同步失败: " + e.getMessage());
            System.exit(1);
        }
    }

    public static int runCli(String[] args) throws Exception {
        CliOptions options = CliOptions.parse(args);
        if (options.help) {
            printUsage();
            return 0;
        }

        String password = options.password;
        if (options.identity == null && password == null) {
            Console console = System.console();
            if (console != null) {
                char[] chars = console.readPassword("SFTP 密码: ");
                password = chars != null ? new String(chars) : "";
            } else {
                throw new IllegalArgumentException("非交互终端请使用 --identity 指定私钥或 --password 指定密码");
            }
        }

        ObjectMapper prettyMapper = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);

        try (SshClient client = SshClient.setUpDefaultClient()) {
            client.setServerKeyVerifier( createServerKeyVerifier(options));
            client.start();

            while (true) {
                try (ClientSession session = client.connect(options.username, options.host, options.port)
                        .verify(Duration.ofSeconds(20))
                        .getSession()) {

                    if (options.identity != null) {
                        Iterable<KeyPair> keys = loadKeyPairs(options.identity, options.passphrase);
                        for (KeyPair kp : keys) {
                            session.addPublicKeyIdentity(kp);
                        }
                    } else {
                        session.addPasswordIdentity(password);
                    }
                    session.auth().verify(Duration.ofSeconds(20));

                    // Negotiate SFTP v3 as required by Simple Sync Server
                    try (SftpClient sftp = SftpClientFactory.instance().createSftpClient(session, 3)) {
                        if (options.listOnly) {
                            SimpleSyncProtocolClient protocol = new SimpleSyncProtocolClient(sftp);
                            SyncInfo info = protocol.info();
                            String userid = options.userid != null ? options.userid : info.userid();
                            SyncListPage page = protocol.list(userid, 200, null);
                            Map<String, Object> output = new LinkedHashMap<>();
                            output.put("info", info);
                            output.put("list", page);
                            System.out.println(prettyMapper.writeValueAsString(output));
                            return 0;
                        }

                        SimpleSyncPullClient pullClient = new SimpleSyncPullClient(
                                sftp,
                                options.destination,
                                options.stateDir,
                                options.userid,
                                options.delete,
                                System.out::println);

                        while (true) {
                            try {
                                PullSummary summary = pullClient.runOnce();
                                System.out.println(new ObjectMapper().writeValueAsString(summary));
                                if (options.intervalSeconds == 0) {
                                    return summary.complete() ? 0 : 1;
                                }
                            } catch (SyncException exc) {
                                if (options.intervalSeconds == 0 || !"INDEX_NOT_READY".equals(exc.getCode())) {
                                    throw exc;
                                }
                                System.out.println(exc.getMessage());
                            }
                            Thread.sleep(options.intervalSeconds * 1000L);
                        }
                    }
                } catch (IOException exc) {
                    if (options.intervalSeconds == 0 || exc instanceof SyncException) {
                        throw exc;
                    }
                    System.err.println("连接中断，" + options.intervalSeconds + " 秒后重连：" + exc.getMessage());
                    Thread.sleep(options.intervalSeconds * 1000L);
                }
            }
        }
    }

    private static ServerKeyVerifier createServerKeyVerifier(CliOptions options) {
        if (options.insecureTrustAllHostKeys) {
            return AcceptAllServerKeyVerifier.INSTANCE;
        }
        return new KnownHostsServerKeyVerifier(RejectAllServerKeyVerifier.INSTANCE, options.knownHosts);
    }

    private static Iterable<KeyPair> loadKeyPairs(Path identityPath, String passphrase)
            throws IOException, GeneralSecurityException {
        try (InputStream in = Files.newInputStream(identityPath)) {
            FilePasswordProvider provider = passphrase != null ? FilePasswordProvider.of(passphrase) : FilePasswordProvider.EMPTY;
            return SecurityUtils.loadKeyPairIdentities(null, () -> identityPath.toString(), in, provider);
        }
    }

    private static void printUsage() {
        System.out.println("""
                用法:
                  java -jar simple-sync-client-0.4.0-all.jar <host> [destination] --username <user> [选项]

                选项:
                  --username <name>       必填，SFTP 登录用户名
                  --port <port>           SFTP 端口（默认 2222）
                  --password <pwd>        登录密码（留空且未指定私钥时交互输入）
                  --identity <path>       SSH 私钥文件路径（支持 Ed25519 / RSA / ECDSA）
                  --passphrase <secret>   加密私钥口令（可选）
                  --known-hosts <path>    known_hosts 文件路径（默认 ~/.ssh/known_hosts）
                  --trust-all-hosts       测试环境跳过主机指纹校验（生产环境勿用）
                  --state <path>          目标目录外的本地状态目录
                  --userid <id>           可选；校验服务端账号 userid
                  --delete                传播服务端删除事件（仅删除对应文件或空目录）
                  --interval <seconds>    持续轮询间隔秒数（默认 0 执行一轮后退出）
                  --list-only             仅查询并打印服务端增量状态 (info + list)，不下载文件
                  --help                  显示此帮助信息
                """);
    }

    public static final class CliOptions {
        public String host;
        public Path destination;
        public int port = 2222;
        public String username;
        public String password;
        public Path identity;
        public String passphrase;
        public Path knownHosts = Path.of(System.getProperty("user.home"), ".ssh", "known_hosts");
        public boolean insecureTrustAllHostKeys;
        public Path stateDir;
        public String userid;
        public boolean delete;
        public int intervalSeconds;
        public boolean listOnly;
        public boolean help;

        public static CliOptions parse(String[] args) {
            CliOptions opts = new CliOptions();
            int positional = 0;
            for (int i = 0; i < args.length; i++) {
                String arg = args[i];
                switch (arg) {
                    case "--help", "-h" -> {
                        opts.help = true;
                        return opts;
                    }
                    case "--username" -> opts.username = requireNext(args, ++i, "--username");
                    case "--port" -> opts.port = Integer.parseInt(requireNext(args, ++i, "--port"));
                    case "--password" -> opts.password = requireNext(args, ++i, "--password");
                    case "--identity" -> opts.identity = Path.of(requireNext(args, ++i, "--identity"));
                    case "--passphrase" -> opts.passphrase = requireNext(args, ++i, "--passphrase");
                    case "--known-hosts" -> opts.knownHosts = Path.of(requireNext(args, ++i, "--known-hosts"));
                    case "--trust-all-hosts" -> opts.insecureTrustAllHostKeys = true;
                    case "--state" -> opts.stateDir = Path.of(requireNext(args, ++i, "--state"));
                    case "--userid" -> opts.userid = requireNext(args, ++i, "--userid");
                    case "--delete" -> opts.delete = true;
                    case "--interval" -> opts.intervalSeconds = Integer.parseInt(requireNext(args, ++i, "--interval"));
                    case "--list-only" -> opts.listOnly = true;
                    default -> {
                        if (arg.startsWith("-")) {
                            throw new IllegalArgumentException("未知参数: " + arg);
                        }
                        if (positional == 0) {
                            opts.host = arg;
                        } else if (positional == 1) {
                            opts.destination = Path.of(arg);
                        } else {
                            throw new IllegalArgumentException("多余的位置参数: " + arg);
                        }
                        positional++;
                    }
                }
            }
            if (opts.host == null || opts.username == null) {
                throw new IllegalArgumentException("必须提供 <host> 和 --username");
            }
            if (!opts.listOnly && opts.destination == null) {
                throw new IllegalArgumentException("拉取模式必须提供 <destination> 目标目录（或使用 --list-only 仅查询）");
            }
            if (opts.intervalSeconds < 0) {
                throw new IllegalArgumentException("--interval 不能小于 0");
            }
            return opts;
        }

        private static String requireNext(String[] args, int index, String flag) {
            if (index >= args.length) {
                throw new IllegalArgumentException(flag + " 缺少参数值");
            }
            return args[index];
        }
    }
}
