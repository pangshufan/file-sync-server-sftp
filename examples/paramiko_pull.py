"""Standalone Paramiko example: one round; retain server-deleted files.

pip install paramiko==4.0.0
python examples/paramiko_pull.py 127.0.0.1 ./downloads --username alice
Use the full SDK for large trees, partial download resume and mirror deletions.
"""
import argparse
import getpass
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

import paramiko
from paramiko.sftp import CMD_EXTENDED, CMD_EXTENDED_REPLY


def request(sftp, operation, args):
    name = f"sync-v2-{operation}@simple-sync.invalid"
    # Isolate Paramiko's private generic extension request API.
    packet_type, message = sftp._request(CMD_EXTENDED, name, json.dumps(args))
    if packet_type != CMD_EXTENDED_REPLY:
        raise RuntimeError("Unexpected SFTP response")
    reply = json.loads(message.get_string())
    if not reply["ok"]:
        raise RuntimeError(f"{reply['error']['code']}: {reply['error']['message']}")
    return reply["result"]


def pull(sftp, destination):
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    info = request(sftp, "info", {})
    if info["protocol"] != "2":
        raise RuntimeError("Requires sync protocol v2")
    userid = info["userid"]
    token = None
    while True:
        page = request(sftp, "list", {"userid": userid, "page_token": token, "limit": 50})
        for entry in page["files"]:
            temporary = None
            error = None
            try:
                parts = PurePosixPath(entry["path"]).parts
                if not entry["path"].startswith("/") or ".." in parts or "\\" in entry["path"]:
                    raise ValueError("Invalid server path")
                local = target
                for part in parts[1:]:
                    local = local / part
                    if local.is_symlink():
                        raise ValueError("Local symlinks are not allowed")
                if local == target or not local.resolve().is_relative_to(target):
                    raise ValueError("Path escapes target")
                if entry["operation"] == "delete":
                    pass  # Explicit retain policy.
                elif entry["kind"] == "directory":
                    local.mkdir(parents=True, exist_ok=True)
                else:
                    local.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(dir=local.parent, prefix=".paramiko-sync-")
                    os.close(fd)
                    sftp.get(entry["path"], temporary)
                    with open(temporary, "rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if digest != entry["sha256"] or os.stat(temporary).st_size != int(entry["size_bytes"]):
                        raise ValueError("Content changed; reopen and retry the task")
                    os.replace(temporary, local)
            except (OSError, ValueError) as exc:
                error = str(exc)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            request(sftp, "ack", {"userid": userid, "files": [
                {"file_id": entry["file_id"], "version": entry["version"], "status": "failed" if error else "success",
                 "sha256": entry["sha256"], "error": error}]})
        if not page["has_more"]:
            current = request(sftp, "list", {"userid": userid, "limit": 1})
            return {"userid": userid, "complete": current["pending"] == 0, "pending": current["pending"]}
        token = page["next_page_token"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("destination")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    with paramiko.SSHClient() as ssh:
        ssh.load_system_host_keys()
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        ssh.connect(args.host, port=args.port, username=args.username,
                    password=getpass.getpass("SFTP password: "), look_for_keys=False, allow_agent=False)
        with ssh.open_sftp() as sftp:
            print(json.dumps(pull(sftp, args.destination)))


if __name__ == "__main__":
    main()
