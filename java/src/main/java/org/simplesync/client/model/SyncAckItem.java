package org.simplesync.client.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Single file confirmation payload for {@code sync-v2-ack@simple-sync.invalid}.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record SyncAckItem(
        @JsonProperty("file_id") String fileId,
        @JsonProperty("version") String version,
        @JsonProperty("status") String status,
        @JsonProperty("sha256") String sha256,
        @JsonProperty("error") String error
) {
    public static SyncAckItem success(String fileId, String version, String sha256) {
        return new SyncAckItem(fileId, version, "success", sha256, null);
    }

    public static SyncAckItem failed(String fileId, String version, String sha256, String error) {
        return new SyncAckItem(fileId, version, "failed", sha256, error);
    }
}
