package org.simplesync.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.sshd.sftp.client.SftpClient;
import org.simplesync.client.model.PullSummary;
import org.simplesync.client.model.SyncAckItem;
import org.simplesync.client.model.SyncFileEntry;
import org.simplesync.client.model.SyncInfo;
import org.simplesync.client.model.SyncListPage;

import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryNotEmptyException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Consumer;

/**
 * High-level incremental pull client for Simple Sync SFTP Server (Protocol v2).
 * <p>
 * Responsibilities:
 * <ul>
 *     <li>Queries server capabilities ({@code info}) and validates {@code userid}.</li>
 *     <li>Pages through all pending/failed incremental items ({@code list}).</li>
 *     <li>Orders operations safely: delete files &rarr; delete directories (deepest first)
 *         &rarr; create directories (shallowest first) &rarr; download files.</li>
 *     <li>Enforces path traversal &amp; symlink checks on local destination paths.</li>
 *     <li>Supports resumable {@code .part} downloads in an isolated state directory,
 *         verifies size and SHA-256 digest, and atomically replaces destination files.</li>
 *     <li>Batches confirmations ({@code ack}) within the 50-item / 48 KiB threshold.</li>
 * </ul>
 */
public class SimpleSyncPullClient {
    private static final int MAX_ACK_BATCH_SIZE = 50;
    private static final int MAX_ACK_JSON_BYTES = 48 * 1024;
    private static final int TRANSFER_BUFFER_SIZE = 256 * 1024;

    private final SimpleSyncProtocolClient protocol;
    private final SftpClient sftp;
    private final Path destination;
    private final Path stateDir;
    private final String expectedUserid;
    private final boolean propagateDelete;
    private final Consumer<String> reporter;
    private final ObjectMapper mapper;

    public SimpleSyncPullClient(
            SftpClient sftp,
            Path destination,
            Path stateDir,
            String expectedUserid,
            boolean propagateDelete,
            Consumer<String> reporter) throws IOException {
        this.sftp = Objects.requireNonNull(sftp, "sftp must not be null");
        this.mapper = new ObjectMapper();
        this.protocol = new SimpleSyncProtocolClient(sftp, mapper);
        this.destination = Objects.requireNonNull(destination, "destination must not be null")
                .toAbsolutePath().normalize();
        this.stateDir = (stateDir != null ? stateDir : defaultStateDir(this.destination))
                .toAbsolutePath().normalize();
        if (this.stateDir.equals(this.destination) || this.stateDir.startsWith(this.destination)) {
            throw new IllegalArgumentException("Client state directory must be outside the sync destination directory");
        }
        Files.createDirectories(this.destination);
        Files.createDirectories(this.stateDir);
        this.expectedUserid = expectedUserid;
        this.propagateDelete = propagateDelete;
        this.reporter = reporter != null ? reporter : System.out::println;
    }

    public static Path defaultStateDir(Path destination) {
        Path norm = destination.toAbsolutePath().normalize();
        String suffix = sha256Hex(norm.toString().getBytes(StandardCharsets.UTF_8)).substring(0, 16);
        Path parent = norm.getParent() != null ? norm.getParent() : norm.getRoot();
        return parent.resolve(".simple-sync-java-" + suffix);
    }

    public SimpleSyncProtocolClient getProtocolClient() {
        return protocol;
    }

    /**
     * Executes one full incremental synchronization round.
     */
    public PullSummary runOnce() throws IOException {
        return runOnce(100);
    }

    /**
     * Executes one full incremental synchronization round with the specified {@code pageSize} (1..200).
     */
    public PullSummary runOnce(int pageSize) throws IOException {
        SyncInfo info = protocol.info();
        String userid = (expectedUserid != null && !expectedUserid.isBlank()) ? expectedUserid : info.userid();
        if (!userid.equals(info.userid())) {
            throw new SyncException("USER_MISMATCH",
                    "Configured userid (" + userid + ") does not match authenticated server userid (" + info.userid() + ")");
        }

        Map<String, SyncFileEntry> workByPath = new LinkedHashMap<>();
        String token = null;
        while (true) {
            SyncListPage page = protocol.list(userid, pageSize, token);
            for (SyncFileEntry entry : page.files()) {
                workByPath.put(entry.path(), entry);
            }
            if (!page.hasMore()) {
                break;
            }
            token = page.nextPageToken();
        }

        List<SyncFileEntry> orderedWork = new ArrayList<>(workByPath.values());
        orderedWork.sort(Comparator
                .comparingInt(SimpleSyncPullClient::priorityOf)
                .thenComparingInt(SimpleSyncPullClient::depthOrderOf)
                .thenComparing(SyncFileEntry::path));

        List<SyncAckItem> pendingAcks = new ArrayList<>();
        int succeeded = 0;
        int failed = 0;

        for (SyncFileEntry current : orderedWork) {
            SyncFileEntry entry = current;
            String error = null;
            for (int attempt = 0; attempt < 3; attempt++) {
                try {
                    if (attempt > 0 || "failed".equals(entry.status())) {
                        SyncListPage refreshed = protocol.listByFileIds(userid, List.of(entry.fileId()));
                        if (!refreshed.files().isEmpty()) {
                            entry = refreshed.files().get(0);
                        }
                    }
                    applyEntry(entry);
                    error = null;
                    break;
                } catch (Exception exc) {
                    error = exc.getMessage() != null ? exc.getMessage() : exc.getClass().getSimpleName();
                    if (attempt < 2) {
                        try {
                            Thread.sleep(200L * (attempt + 1));
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            throw new IOException("Interrupted during retry backoff", ie);
                        }
                    }
                }
            }

            SyncAckItem ack = error == null
                    ? SyncAckItem.success(entry.fileId(), entry.version(), entry.sha256())
                    : SyncAckItem.failed(entry.fileId(), entry.version(), entry.sha256(), truncate(error, 500));

            if (!pendingAcks.isEmpty()) {
                List<SyncAckItem> candidate = new ArrayList<>(pendingAcks.size() + 1);
                candidate.addAll(pendingAcks);
                candidate.add(ack);
                if (mapper.writeValueAsBytes(candidate).length > MAX_ACK_JSON_BYTES) {
                    protocol.ack(userid, pendingAcks);
                    pendingAcks.clear();
                }
            }
            pendingAcks.add(ack);
            if (pendingAcks.size() >= MAX_ACK_BATCH_SIZE) {
                protocol.ack(userid, pendingAcks);
                pendingAcks.clear();
            }

            if (error != null) {
                failed++;
                reporter.accept("失败 " + entry.path() + "：" + error);
            } else {
                succeeded++;
                reporter.accept("完成 " + entry.operation() + " " + entry.path());
            }
        }

        if (!pendingAcks.isEmpty()) {
            protocol.ack(userid, pendingAcks);
            pendingAcks.clear();
        }

        SyncListPage finalCheck = protocol.list(userid, 1, null);
        return new PullSummary(userid, finalCheck.pending() == 0, finalCheck.pending(), succeeded, failed);
    }

    private void applyEntry(SyncFileEntry entry) throws IOException {
        Path local = resolveSafeLocalPath(entry.path());
        if (local.equals(destination)) {
            throw new IOException("Server entry cannot replace the sync destination root");
        }

        if (entry.isDelete()) {
            if (propagateDelete && Files.exists(local, LinkOption.NOFOLLOW_LINKS)) {
                if (Files.isDirectory(local, LinkOption.NOFOLLOW_LINKS)) {
                    try {
                        Files.delete(local);
                    } catch (DirectoryNotEmptyException ignored) {
                        // Never recursively delete untracked local files inside directories.
                    }
                } else {
                    Files.deleteIfExists(local);
                }
            }
            return;
        }

        if (entry.isDirectory()) {
            if (Files.isRegularFile(local, LinkOption.NOFOLLOW_LINKS)) {
                Files.delete(local);
            }
            Files.createDirectories(local);
            return;
        }

        long expectedSize = entry.sizeAsLong();
        if (Files.isRegularFile(local, LinkOption.NOFOLLOW_LINKS)) {
            if (Files.size(local) == expectedSize && sha256File(local).equalsIgnoreCase(entry.sha256())) {
                return;
            }
        }

        if (local.getParent() != null) {
            Files.createDirectories(local.getParent());
        }

        String partKey = sha256Hex((entry.path() + ":" + entry.version()).getBytes(StandardCharsets.UTF_8));
        Path partFile = stateDir.resolve(partKey + ".part");
        if (Files.isSymbolicLink(partFile)) {
            throw new IOException("Temporary part file must not be a symbolic link");
        }

        long offset = Files.exists(partFile, LinkOption.NOFOLLOW_LINKS) ? Files.size(partFile) : 0L;
        if (offset > expectedSize) {
            Files.deleteIfExists(partFile);
            offset = 0L;
        }

        if (offset < expectedSize || !Files.exists(partFile, LinkOption.NOFOLLOW_LINKS)) {
            downloadResumable(entry.path(), partFile, offset);
        }

        if (Files.size(partFile) != expectedSize || !sha256File(partFile).equalsIgnoreCase(entry.sha256())) {
            Files.deleteIfExists(partFile);
            throw new SyncException("CONTENT_CHANGED", "Downloaded file size or SHA-256 does not match manifest version");
        }

        Path stagingTmp = Files.createTempFile(local.getParent(), ".simple-sync-", ".tmp");
        try {
            Files.copy(partFile, stagingTmp, StandardCopyOption.REPLACE_EXISTING);
            try (FileChannel channel = FileChannel.open(stagingTmp, StandardOpenOption.WRITE)) {
                channel.force(true);
            }
            resolveSafeLocalPath(entry.path());
            if (Files.isDirectory(local, LinkOption.NOFOLLOW_LINKS)) {
                Files.delete(local);
            }
            Files.move(stagingTmp, local, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            Files.deleteIfExists(partFile);
        } finally {
            Files.deleteIfExists(stagingTmp);
        }
    }

    private void downloadResumable(String remotePath, Path partFile, long offset) throws IOException {
        try (SftpClient.CloseableHandle handle = sftp.open(remotePath, EnumSet.of(SftpClient.OpenMode.Read));
             FileChannel output = FileChannel.open(partFile,
                     StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.APPEND)) {
            byte[] buffer = new byte[TRANSFER_BUFFER_SIZE];
            long fileOffset = offset;
            while (true) {
                int read = sftp.read(handle, fileOffset, buffer, 0, buffer.length);
                if (read <= 0) {
                    break;
                }
                ByteBuffer byteBuffer = ByteBuffer.wrap(buffer, 0, read);
                while (byteBuffer.hasRemaining()) {
                    output.write(byteBuffer);
                }
                fileOffset += read;
            }
            output.force(true);
        }
    }

    public Path resolveSafeLocalPath(String virtualPath) throws IOException {
        if (virtualPath == null || !virtualPath.startsWith("/")
                || virtualPath.contains("\\") || virtualPath.indexOf('\0') >= 0) {
            throw new IOException("Server returned an illegal path: " + virtualPath);
        }
        String[] parts = virtualPath.split("/");
        Path current = destination;
        for (int i = 1; i < parts.length; i++) {
            String part = parts[i];
            if (part.isEmpty()) {
                continue;
            }
            if (".".equals(part) || "..".equals(part)) {
                throw new IOException("Server returned a traversal path: " + virtualPath);
            }
            current = current.resolve(part);
            if (Files.isSymbolicLink(current)) {
                throw new IOException("Local target path contains a symbolic link: " + current);
            }
        }
        Path normalized = current.toAbsolutePath().normalize();
        if (!normalized.startsWith(destination)) {
            throw new IOException("Resolved local path escapes destination root: " + virtualPath);
        }
        return normalized;
    }

    private static int priorityOf(SyncFileEntry entry) {
        if (entry.isDelete()) {
            return entry.isDirectory() ? 1 : 0;
        }
        return entry.isDirectory() ? 2 : 3;
    }

    private static int depthOrderOf(SyncFileEntry entry) {
        int slashes = 0;
        String path = entry.path();
        for (int i = 0; i < path.length(); i++) {
            if (path.charAt(i) == '/') {
                slashes++;
            }
        }
        return entry.isDelete() ? -slashes : slashes;
    }

    private static String truncate(String text, int maxChars) {
        return text == null || text.length() <= maxChars ? text : text.substring(0, maxChars);
    }

    public static String sha256File(Path file) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[64 * 1024];
            try (InputStream in = Files.newInputStream(file, LinkOption.NOFOLLOW_LINKS)) {
                int n;
                while ((n = in.read(buffer)) > 0) {
                    digest.update(buffer, 0, n);
                }
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }

    private static String sha256Hex(byte[] data) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(data));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }
}
