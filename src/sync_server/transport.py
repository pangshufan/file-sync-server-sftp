"""AsyncSSH 2.24.1 compatibility boundary; no global monkey patches."""

import asyncio
import json

import asyncssh
from asyncssh.constants import FXP_CLOSE, FXP_READ, FXP_EXTENDED_REPLY
from asyncssh.packet import String
from asyncssh.sftp import SFTPServerHandler
from asyncssh.stream import SSHServerStreamSession

from .i18n import tr
from .sync_api import NAMESPACE, OPERATIONS, SyncError

MAX_JSON = 64 * 1024


def extension_name(operation):
    return f"sync-v2-{operation}@{NAMESPACE}".encode("ascii")


class JSONReply:
    def __init__(self, data):
        self.data = data

    def encode(self, _version):
        return String(json.dumps(self.data, ensure_ascii=True, separators=(",", ":")).encode())


def handler_for(operation):
    async def handle(handler, packet):
        try:
            raw = packet.get_string()
            packet.check_end()
            if len(raw) > MAX_JSON:
                raise SyncError("BAD_REQUEST", tr('请求 JSON 超过 64 KiB'))
            try:
                args = json.loads(raw)
            except (ValueError, UnicodeError):
                raise SyncError("BAD_REQUEST", tr('无效 JSON')) from None
            peer = handler._server.connection.get_extra_info("peername")
            result = await asyncio.to_thread(handler._server.sync_api.dispatch, handler._server.user, operation, args, str(peer[0]))
            return JSONReply({"ok": True, "result": result})
        except SyncError as exc:
            return JSONReply({"ok": False, "error": {"code": exc.code, "message": str(exc)}})
    return handle


async def close_request(handler, packet):
    """Only a received FXP_CLOSE can auto-confirm; disconnect cleanup cannot."""
    handle = packet.get_string()
    packet.check_end()
    file_obj = handler._file_handles.pop(handle, None)
    if file_obj is not None:
        await handler._server.close_explicit(file_obj)
    elif handler._dir_handles.pop(handle, None) is None:
        raise asyncssh.SFTPInvalidHandle("Invalid file handle")


async def read_request(handler, packet):
    """Track bytes only for FXP_READ replies, never server-side copy-data reads."""
    handle = packet.get_string()
    offset = packet.get_uint64()
    length = packet.get_uint32()
    packet.check_end()
    file_obj = handler._file_handles.get(handle)
    if file_obj is None:
        raise asyncssh.SFTPInvalidHandle("Invalid file handle")
    data = handler._server.read_download(file_obj, offset, length)
    if not data:
        raise asyncssh.SFTPEOFError()
    return data, False


class SyncSFTPHandler(SFTPServerHandler):
    _extensions = [*SFTPServerHandler._extensions, *((extension_name(op), b"2") for op in OPERATIONS)]
    _packet_handlers = {**SFTPServerHandler._packet_handlers,
                        **{extension_name(op): handler_for(op) for op in OPERATIONS},
                        FXP_CLOSE: close_request, FXP_READ: read_request}
    _return_types = {**SFTPServerHandler._return_types,
                     **{extension_name(op): FXP_EXTENDED_REPLY for op in OPERATIONS}}


class SyncSession(SSHServerStreamSession):
    def __init__(self, sftp_factory):
        super().__init__(None, sftp_factory=sftp_factory, sftp_version=3, allow_scp=False)

    def session_started(self):
        # This session accepts only subsystem 'sftp'; shell/exec remain refused.
        reader = asyncssh.SSHReader(self, self._chan)
        writer = asyncssh.SSHWriter(self, self._chan)
        self._conn.create_task(SyncSFTPHandler(self._init_sftp_server(), reader, writer, 3).run(), reader.logger)


async def request(sftp, operation, args):
    """Keep all uses of AsyncSSH private client APIs in this module."""
    if operation not in OPERATIONS:
        raise ValueError(tr('未知同步指令'))
    name = extension_name(operation)
    handler = sftp._handler
    handler._return_types = {**handler._return_types, name: FXP_EXTENDED_REPLY}
    packet = await handler._make_request(name, String(json.dumps(args, ensure_ascii=True).encode()))
    result = json.loads(packet.get_string())
    packet.check_end()
    if not result["ok"]:
        raise SyncError(result["error"]["code"], result["error"]["message"])
    return result["result"]


def server_identity(sftp):
    connection = sftp._handler._reader.channel.get_connection()
    return [connection.get_server_host_key().get_fingerprint(), connection.get_extra_info("username")]
