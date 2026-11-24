package org.simplesync.client.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Server capabilities and user incremental settings returned by {@code sync-v2-info@simple-sync.invalid}.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SyncInfo(
        @JsonProperty("protocol") String protocol,
        @JsonProperty("namespace") String namespace,
        @JsonProperty("userid") String userid,
        @JsonProperty("hash") String hash,
        @JsonProperty("confirmation") String confirmation,
        @JsonProperty("sync_enabled") boolean syncEnabled,
        @JsonProperty("incremental_start") double incrementalStart,
        @JsonProperty("directories") List<String> directories,
        @JsonProperty("max_page_size") int maxPageSize,
        @JsonProperty("operations") List<String> operations,
        @JsonProperty("server_time") double serverTime
) {
}
