package org.simplesync.client;

import java.io.IOException;

/**
 * Exception representing a structured business error returned by the Simple Sync SFTP v2 protocol
 * (for example {@code USER_MISMATCH}, {@code SYNC_DISABLED}, {@code INDEX_NOT_READY},
 * {@code BAD_TOKEN}, {@code VERSION_MISMATCH}, {@code CHECKSUM_MISMATCH}).
 */
public class SyncException extends IOException {
    private final String code;

    public SyncException(String code, String message) {
        super(code + ": " + message);
        this.code = code;
    }

    public SyncException(String code, String message, Throwable cause) {
        super(code + ": " + message, cause);
        this.code = code;
    }

    public String getCode() {
        return code;
    }
}
