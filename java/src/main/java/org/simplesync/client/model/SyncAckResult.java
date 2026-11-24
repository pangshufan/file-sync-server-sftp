package org.simplesync.client.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Confirmation response returned by {@code sync-v2-ack@simple-sync.invalid}.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SyncAckResult(
        @JsonProperty("userid") String userid,
        @JsonProperty("complete") boolean complete,
        @JsonProperty("pending") int pending,
        @JsonProperty("failed") int failed,
        @JsonProperty("synced") int synced,
        @JsonProperty("server_time") double serverTime
) {
}
