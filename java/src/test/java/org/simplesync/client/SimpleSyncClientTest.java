package org.simplesync.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.sshd.client.SshClient;
import org.apache.sshd.client.keyverifier.AcceptAllServerKeyVerifier;
import org.apache.sshd.client.session.ClientSession;
import org.apache.sshd.sftp.client.SftpClient;
import org.apache.sshd.sftp.client.SftpClientFactory;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.simplesync.client.model.PullSummary;
import org.simplesync.client.model.SyncInfo;
import org.simplesync.client.model.SyncListPage;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SimpleSyncClientTest {

    @Test
    void endToEndIncrementalPullAndAckAgainstPythonServer(@TempDir Path tempDir) throws Exception {
        Path pythonBin = resolvePythonExecutable();
        Path sharedRoot = tempDir.resolve("shared");
        Path stateDir = tempDir.resolve("server-state");
        Path destDir = tempDir.resolve("downloads");
        Path clientStateDir = tempDir.resolve("client-state");
        Files.createDirectories(sharedRoot.resolve("reports"));
        Files.createDirectories(stateDir);

        Files.writeString(sharedRoot.resolve("hello.txt"), "hello from server", StandardCharsets.UTF_8);
        Files.writeString(sharedRoot.resolve("reports/q1.csv"), "id,value\n1,100\n", StandardCharsets.UTF_8);

        int port = findFreePort();
        String userid = "0123456789abcdef0123456789abcdef";
        prepareServerConfig(pythonBin, stateDir, sharedRoot, port, userid, "test-java-pass");

        ProcessBuilder pb = new ProcessBuilder(
                pythonBin.toString(), "-m", "sync_server.headless", "--data-dir", stateDir.toString());
        pb.directory(resolveRepoRoot().toFile());
        pb.environment().put("SIMPLE_SYNC_LANGUAGE", "zh");
        pb.redirectErrorStream(true);
        Process server = pb.start();

        try {
            waitForServerStartup(server, port);

            try (SshClient ssh = SshClient.setUpDefaultClient()) {
                ssh.setServerKeyVerifier(AcceptAllServerKeyVerifier.INSTANCE);
                ssh.start();

                try (ClientSession session = ssh.connect("alice", "127.0.0.1", port)
                        .verify(Duration.ofSeconds(10))
                        .getSession()) {
                    session.addPasswordIdentity("test-java-pass");
                    session.auth().verify(Duration.ofSeconds(10));

                    try (SftpClient sftp = SftpClientFactory.instance().createSftpClient(session, 3)) {
                        SimpleSyncProtocolClient protocol = new SimpleSyncProtocolClient(sftp);
                        SyncInfo info = protocol.info();
                        assertEquals("2", info.protocol());
                        assertEquals("sha256", info.hash());
                        assertEquals(userid, info.userid());
                        assertTrue(info.syncEnabled());

                        // Wait until initial index scan is ready
                        waitForIndexReady(protocol, userid);

                        SyncListPage initialPage = protocol.list(userid, 100, null);
                        assertTrue(initialPage.pending() >= 2);
                        assertFalse(initialPage.files().isEmpty());

                        List<String> logs = new ArrayList<>();
                        SimpleSyncPullClient pullClient = new SimpleSyncPullClient(
                                sftp, destDir, clientStateDir, userid, true, logs::add);

                        // Path traversal checks
                        assertThrows(IOException.class, () -> pullClient.resolveSafeLocalPath("/../outside.txt"));
                        assertThrows(IOException.class, () -> pullClient.resolveSafeLocalPath("relative/path.txt"));
                        assertThrows(IOException.class, () -> pullClient.resolveSafeLocalPath("/bad\\slash.txt"));

                        // Round 1: pull all initial files
                        PullSummary firstRound = pullClient.runOnce(1);
                        assertTrue(firstRound.complete());
                        assertEquals(0, firstRound.pending());
                        assertEquals(0, firstRound.failed());
                        assertTrue(firstRound.succeeded() >= 2);
                        assertEquals("hello from server", Files.readString(destDir.resolve("hello.txt")));
                        assertEquals("id,value\n1,100\n", Files.readString(destDir.resolve("reports/q1.csv")));

                        // Round 2: idempotency when no changes occurred
                        PullSummary secondRound = pullClient.runOnce();
                        assertTrue(secondRound.complete());
                        assertEquals(0, secondRound.succeeded());

                        // Modify one file and delete another on the server, then trigger a scan via SFTP upload/remove
                        try (SftpClient.CloseableHandle h = sftp.open("/hello.txt",
                                List.of(SftpClient.OpenMode.Write, SftpClient.OpenMode.Truncate))) {
                            byte[] updated = "updated content v2".getBytes(StandardCharsets.UTF_8);
                            sftp.write(h, 0, updated, 0, updated.length);
                        }
                        sftp.remove("/reports/q1.csv");

                        // Wait for watchdog debounce (~2.5s) to publish new versions
                        waitForPendingCountAtLeast(protocol, userid, 2);

                        PullSummary thirdRound = pullClient.runOnce();
                        assertTrue(thirdRound.complete());
                        assertEquals("updated content v2", Files.readString(destDir.resolve("hello.txt")));
                        assertFalse(Files.exists(destDir.resolve("reports/q1.csv")));
                    }
                }
            }
        } finally {
            server.destroy();
            if (!server.waitFor(5, TimeUnit.SECONDS)) {
                server.destroyForcibly();
            }
        }
    }

    private static void waitForIndexReady(SimpleSyncProtocolClient protocol, String userid) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        while (System.nanoTime() < deadline) {
            try {
                protocol.list(userid, 10, null);
                return;
            } catch (SyncException e) {
                if (!"INDEX_NOT_READY".equals(e.getCode())) {
                    throw e;
                }
                Thread.sleep(100);
            }
        }
        throw new AssertionError("Timed out waiting for server index to become ready");
    }

    private static void waitForPendingCountAtLeast(
            SimpleSyncProtocolClient protocol, String userid, int expected) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        while (System.nanoTime() < deadline) {
            SyncListPage page = protocol.list(userid, 10, null);
            if (page.pending() >= expected) {
                return;
            }
            Thread.sleep(200);
        }
        throw new AssertionError("Timed out waiting for pending count >= " + expected);
    }

    private static void waitForServerStartup(Process server, int port) throws Exception {
        BufferedReader reader = new BufferedReader(new InputStreamReader(server.getInputStream(), StandardCharsets.UTF_8));
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        while (System.nanoTime() < deadline) {
            if (!server.isAlive()) {
                throw new AssertionError("Server exited prematurely with code " + server.exitValue());
            }
            String line = reader.readLine();
            assertNotNull(line, "Server stdout closed before startup message");
            if (line.contains("服务已启动") || line.contains("127.0.0.1:" + port) || line.contains("SHA256:")) {
                // Drain remaining output in background so buffer never blocks
                Thread drain = new Thread(() -> {
                    try {
                        while (reader.readLine() != null) {
                            // discard
                        }
                    } catch (IOException ignored) {
                    }
                }, "server-stdout-drain");
                drain.setDaemon(true);
                drain.start();
                return;
            }
        }
        throw new AssertionError("Timed out waiting for headless server startup");
    }

    private static void prepareServerConfig(
            Path pythonBin, Path stateDir, Path sharedRoot, int port, String userid, String password) throws Exception {
        String script = """
                import sys
                from pathlib import Path
                from sync_server.config import Config, ConfigStore, User, hash_password
                state_dir = Path(sys.argv[1])
                shared_root = Path(sys.argv[2])
                port = int(sys.argv[3])
                userid = sys.argv[4]
                password = sys.argv[5]
                user = User(username="alice", password_hash=hash_password(password), root=str(shared_root), userid=userid)
                config = Config(host="127.0.0.1", port=port, users=[user])
                ConfigStore(state_dir).save(config)
                """;
        ProcessBuilder pb = new ProcessBuilder(
                pythonBin.toString(), "-c", script,
                stateDir.toString(), sharedRoot.toString(), String.valueOf(port), userid, password);
        pb.directory(resolveRepoRoot().toFile());
        pb.redirectErrorStream(true);
        Process p = pb.start();
        String output = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        assertEquals(0, p.waitFor(), "Failed to create server config: " + output);
    }

    private static Path resolveRepoRoot() {
        Path cwd = Path.of("").toAbsolutePath().normalize();
        if (Files.exists(cwd.resolve("pyproject.toml"))) {
            return cwd;
        }
        return cwd.resolve("..").toAbsolutePath().normalize();
    }

    private static Path resolvePythonExecutable() {
        String env = System.getenv("SIMPLE_SYNC_PYTHON");
        if (env != null && !env.isBlank() && Files.isExecutable(Path.of(env))) {
            return Path.of(env);
        }
        Path condaEnvPython = resolveRepoRoot().getParent().resolve("env/sync-env/bin/python");
        if (Files.isExecutable(condaEnvPython)) {
            return condaEnvPython;
        }
        return Path.of("python3");
    }

    private static int findFreePort() throws IOException {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        }
    }
}
