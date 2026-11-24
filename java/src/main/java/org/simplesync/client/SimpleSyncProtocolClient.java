package org.simplesync.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.sshd.common.util.buffer.Buffer;
import org.apache.sshd.common.util.buffer.ByteArrayBuffer;
import org.apache.sshd.sftp.client.RawSftpClient;
import org.apache.sshd.sftp.client.SftpClient;
import org.apache.sshd.sftp.common.SftpConstants;
import org.apache.sshd.sftp.common.SftpException;
import org.simplesync.client.model.SyncAckItem;
import org.simplesync.client.model.SyncAckResult;
import org.simplesync.client.model.SyncInfo;
import org.simplesync.client.model.SyncListPage;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Low-level Protocol v2 client over an active Apache MINA SSHD {@link SftpClient}.
 * <p>
 * Sends {@code SSH_FXP_EXTENDED (200)} requests for:
 * <ul>
 *     <li>{@code sync-v2-info@simple-sync.invalid}</li>
 *     <li>{@code sync-v2-list@simple-sync.invalid}</li>
 *     <li>{@code sync-v2-ack@simple-sync.invalid}</li>
 * </ul>
 */
public class SimpleSyncProtocolClient {
    public static final String NAMESPACE = "simple-sync.invalid";
    public static final int MAX_JSON_BYTES = 64 * 1024;

    private final SftpClient sftp;
    private final RawSftpClient rawSftp;
    private final ObjectMapper mapper;

    public SimpleSyncProtocolClient(SftpClient sftp) {
        this(sftp, new ObjectMapper());
    }

    public SimpleSyncProtocolClient(SftpClient sftp, ObjectMapper mapper) {
        this.sftp = Objects.requireNonNull(sftp, "sftp must not be null");
        if (!(sftp instanceof RawSftpClient raw)) {
            throw new IllegalArgumentException(
                    "Provided SftpClient does not implement RawSftpClient: " + sftp.getClass().getName());
        }
        this.rawSftp = raw;
        this.mapper = Objects.requireNonNull(mapper, "mapper must not be null");
    }

    public SftpClient getSftpClient() {
        return sftp;
    }

    public static String extensionName(String operation) {
        return "sync-v2-" + operation + "@" + NAMESPACE;
    }

    /**
     * Queries {@code sync-v2-info@simple-sync.invalid} and verifies protocol v2 + sha256 compatibility.
     */
    public SyncInfo info() throws IOException {
        SyncInfo result = request("info", Map.of(), SyncInfo.class);
        if (!"2".equals(result.protocol()) || !"sha256".equals(result.hash())) {
            throw new SyncException("UNSUPPORTED_PROTOCOL",
                    "Server incremental protocol is not v2/sha256 (got protocol="
                            + result.protocol() + ", hash=" + result.hash() + ")");
        }
        return result;
    }

    /**
     * Queries a page of pending/failed incremental items via {@code sync-v2-list@simple-sync.invalid}.
     */
    public SyncListPage list(String userid, int limit, String pageToken) throws IOException {
        Objects.requireNonNull(userid, "userid must not be null");
        if (limit < 1 || limit > 200) {
            throw new IllegalArgumentException("limit must be between 1 and 200");
        }
        Map<String, Object> args = new LinkedHashMap<>();
        args.put("userid", userid);
        args.put("limit", limit);
        args.put("page_token", pageToken);
        return request("list", args, SyncListPage.class);
    }

    /**
     * Queries the latest pending state of specific {@code file_ids} (1..200) for retry after content changes.
     */
    public SyncListPage listByFileIds(String userid, List<String> fileIds) throws IOException {
        Objects.requireNonNull(userid, "userid must not be null");
        Objects.requireNonNull(fileIds, "fileIds must not be null");
        if (fileIds.isEmpty() || fileIds.size() > 200) {
            throw new IllegalArgumentException("fileIds must contain 1 to 200 identifiers");
        }
        Map<String, Object> args = new LinkedHashMap<>();
        args.put("userid", userid);
        args.put("file_ids", fileIds);
        args.put("limit", Math.min(200, Math.max(1, fileIds.size())));
        return request("list", args, SyncListPage.class);
    }

    /**
     * Acknowledges 1..200 downloaded or failed items via {@code sync-v2-ack@simple-sync.invalid}.
     */
    public SyncAckResult ack(String userid, List<SyncAckItem> files) throws IOException {
        Objects.requireNonNull(userid, "userid must not be null");
        Objects.requireNonNull(files, "files must not be null");
        if (files.isEmpty() || files.size() > 200) {
            throw new IllegalArgumentException("Each ack batch must contain 1 to 200 items");
        }
        Map<String, Object> args = new LinkedHashMap<>();
        args.put("userid", userid);
        args.put("files", files);
        return request("ack", args, SyncAckResult.class);
    }

    /**
     * Sends a custom SFTP v2 sync extension request and parses the JSON response into {@code responseType}.
     */
    public <T> T request(String operation, Object payload, Class<T> responseType) throws IOException {
        byte[] jsonBytes = mapper.writeValueAsString(payload).getBytes(StandardCharsets.UTF_8);
        if (jsonBytes.length > MAX_JSON_BYTES) {
            throw new SyncException("BAD_REQUEST", "Request JSON exceeds 64 KiB limit");
        }

        Buffer requestBuffer = new ByteArrayBuffer(extensionName(operation).length() + jsonBytes.length + 16, false);
        requestBuffer.putString(extensionName(operation), StandardCharsets.US_ASCII);
        requestBuffer.putBytes(jsonBytes);

        int requestId = rawSftp.send(SftpConstants.SSH_FXP_EXTENDED, requestBuffer);
        Buffer reply = rawSftp.receive(requestId);
        return decodeReply(reply, responseType);
    }

    private <T> T decodeReply(Buffer reply, Class<T> responseType) throws IOException {
        int length = reply.getInt();
        int type = reply.getUByte();
        int rxId = reply.getInt();
        if (type == SftpConstants.SSH_FXP_STATUS) {
            int substatus = reply.getInt();
            String msg = reply.available() > 0 ? reply.getString() : "SFTP status " + substatus;
            throw new SftpException(substatus, msg);
        }
        if (type != SftpConstants.SSH_FXP_EXTENDED_REPLY) {
            throw new IOException("Unexpected SFTP packet type: " + type + " (len=" + length + ", id=" + rxId + ")");
        }
        String rawJson = reply.getString(StandardCharsets.UTF_8);
        JsonNode root = mapper.readTree(rawJson);
        boolean ok = root.path("ok").asBoolean(false);
        if (!ok) {
            JsonNode error = root.path("error");
            String code = error.path("code").asText("UNKNOWN_ERROR");
            String message = error.path("message").asText("Unknown server sync error");
            throw new SyncException(code, message);
        }
        return mapper.treeToValue(root.get("result"), responseType);
    }
}
