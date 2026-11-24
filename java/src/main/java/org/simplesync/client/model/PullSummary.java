package org.simplesync.client.model;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Result of executing one full incremental synchronization round via {@code SimpleSyncPullClient}.
 */
public record PullSummary(
        @JsonProperty("userid") String userid,
        @JsonProperty("complete") boolean complete,
        @JsonProperty("pending") int pending,
        @JsonProperty("succeeded") int succeeded,
        @JsonProperty("failed") int failed
) {
}
