"""Unicode console output must survive legacy redirected Windows encodings."""
import io
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sync_server.console import configure_console_output


def legacy_environment(language):
    return {**os.environ, "SIMPLE_SYNC_LANGUAGE": language,
            "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252:strict"}


@pytest.mark.parametrize("module", ["sync_server.client", "sync_server.headless"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_help_is_utf8_under_cp1252(module, language):
    result = subprocess.run([sys.executable, "-m", module, "--help"],
                            env=legacy_environment(language), capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    output = result.stdout.decode("utf-8", errors="strict")
    assert "--help" in output
    if module == "sync_server.client":
        assert ("从 Simple Sync" if language == "zh" else "Download incremental updates") in output
    else:
        assert ("Simple Sync 无头" if language == "zh" else "Simple Sync headless") in output


@pytest.mark.parametrize("module", ["sync_server.client", "sync_server.headless"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_localized_errors_are_utf8_under_cp1252(module, language, tmp_path):
    if module == "sync_server.client":
        args = ["localhost", str(tmp_path / "downloads"), "--username", "alice", "--batch"]
        code = 2
        expected = "batch 模式必须指定" if language == "zh" else "Batch mode requires"
    else:
        args = ["--data-dir", str(tmp_path / "missing"), "--check-config"]
        code = 1
        expected = "服务失败:" if language == "zh" else "Server failed:"
    result = subprocess.run([sys.executable, "-m", module, *args],
                            env=legacy_environment(language), capture_output=True)
    assert result.returncode == code
    error = result.stderr.decode("utf-8", errors="strict")
    assert expected in error
    assert "UnicodeEncodeError" not in error


@pytest.mark.parametrize("newline", [None, "\n", "\r\n"], ids=["native", "lf", "crlf"])
def test_reconfigures_both_real_streams_without_losing_unicode(monkeypatch, newline):
    stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict", newline=newline)
    stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252", errors="strict", newline=newline)
    # Encoding configuration must preserve the stream's newline policy.
    expected_newline = os.linesep if newline is None else newline
    with monkeypatch.context() as context:
        context.setattr(sys, "stdout", stdout)
        context.setattr(sys, "stderr", stderr)
        configure_console_output()
        stdout.write("中文输出 😀\n")
        stderr.write("中文错误\n")
        stdout.flush()
        stderr.flush()
        assert stdout_bytes.getvalue().decode("utf-8") == "中文输出 😀" + expected_newline
        assert stderr_bytes.getvalue().decode("utf-8") == "中文错误" + expected_newline
    stdout.detach()
    stderr.detach()


def test_missing_and_custom_capture_streams_are_supported(monkeypatch):
    capture = io.StringIO()
    with monkeypatch.context() as context:
        context.setattr(sys, "stdout", capture)
        context.setattr(sys, "stderr", None)
        configure_console_output()
        assert sys.stdout is capture
        assert sys.stderr is None


def load_builder():
    path = Path(__file__).resolve().parents[1] / "tools/build_app.py"
    spec = importlib.util.spec_from_file_location("build_app", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def test_build_smoke_retains_child_failure_details(monkeypatch, capsys):
    builder = load_builder()
    failed = subprocess.CompletedProcess(["app", "--help"], 1, stdout=b"", stderr=b"child failure: \xe9\n")
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: failed)
    with pytest.raises(subprocess.CalledProcessError):
        builder.check_console_help("app", "simple-sync-pull", "zh", {})
    assert "child failure: \\xe9" in capsys.readouterr().err


def test_build_smoke_rejects_non_utf8_help(monkeypatch):
    builder = load_builder()
    wrong_encoding = subprocess.CompletedProcess(["app", "--help"], 0,
                            stdout="从 Simple Sync".encode("gbk"), stderr=b"")
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: wrong_encoding)
    with pytest.raises(UnicodeDecodeError):
        builder.check_console_help("app", "simple-sync-pull", "zh", {})
