package org.simplesync.client.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Paginated incremental file list returned by {@code sync-v2-list@simple-sync.invalid}.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SyncListPage(
        @JsonProperty("userid") String userid,
        @JsonProperty("server_time") double serverTime,
        @JsonProperty("processed") String processed,
        @JsonProperty("pending") int pending,
        @JsonProperty("failed") int failed,
        @JsonProperty("synced") int synced,
        @JsonProperty("next_page_token") String nextPageToken,
        @JsonProperty("has_more") boolean hasMore,
        @JsonProperty("files") List<SyncFileEntry> files
) {
}
