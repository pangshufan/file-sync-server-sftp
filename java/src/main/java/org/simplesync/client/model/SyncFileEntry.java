package org.simplesync.client.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Single incremental file or directory item returned by {@code sync-v2-list@simple-sync.invalid}.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SyncFileEntry(
        @JsonProperty("file_id") String fileId,
        @JsonProperty("path") String path,
        @JsonProperty("version") String version,
        @JsonProperty("updated_at") double updatedAt,
        @JsonProperty("operation") String operation,
        @JsonProperty("kind") String kind,
        @JsonProperty("size_bytes") String sizeBytes,
        @JsonProperty("sha256") String sha256,
        @JsonProperty("status") String status,
        @JsonProperty("error") String error
) {
    public long sizeAsLong() {
        return sizeBytes == null || sizeBytes.isEmpty() ? 0L : Long.parseLong(sizeBytes);
    }

    public boolean isDelete() {
        return "delete".equals(operation);
    }

    public boolean isDirectory() {
        return "directory".equals(kind);
    }

    public boolean isFile() {
        return "file".equals(kind);
    }
}
