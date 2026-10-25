"""Build and archive GUI + CLI for the current OS and Python architecture."""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from sync_server.console import configure_console_output

ROOT = Path(__file__).resolve().parents[1]


def check_console_help(executable, name, language, environment):
    result = subprocess.run([str(executable), "--help"], timeout=30,
                            env=environment, capture_output=True)
    if result.returncode:
        # Preserve useful child diagnostics before raising, even if a broken
        # executable produced bytes that were not valid UTF-8.
        print(result.stdout.decode("utf-8", errors="backslashreplace"), end="")
        print(result.stderr.decode("utf-8", errors="backslashreplace"), end="", file=sys.stderr)
        result.check_returncode()
    output = result.stdout.decode("utf-8", errors="strict")
    error = result.stderr.decode("utf-8", errors="strict")
    expected = {"simple-sync-pull": {"en": "Download incremental updates", "zh": "从 Simple Sync"},
                "simple-sync-serve": {"en": "Simple Sync headless", "zh": "Simple Sync 无头"}}[name][language]
    if expected not in output:
        raise RuntimeError(f"Missing {language} help text in {name}")
    print(output, end="")
    if error:
        print(error, end="", file=sys.stderr)


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()
    build_environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    subprocess.run([sys.executable, str(ROOT / "tools/generate_icons.py")], check=True, env=build_environment)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
                    str(ROOT / "packaging/simple-sync.spec")], cwd=ROOT, check=True,
                   env={**build_environment, "PYINSTALLER_CONFIG_DIR": str(ROOT / "build/pyinstaller-cache")})
    suffix = ".exe" if sys.platform == "win32" else ""
    gui = (ROOT / "dist/Simple Sync Server.app/Contents/MacOS/SimpleSyncServer"
           if sys.platform == "darwin" else ROOT / f"dist/SimpleSyncServer/SimpleSyncServer{suffix}")
    for language in ("en", "zh"):
        # Deliberately hostile defaults: a frozen executable must establish its
        # own encoding, independently of the build Python's UTF-8 environment.
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "SIMPLE_SYNC_LANGUAGE": language,
                       "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252:strict"}
        subprocess.run([str(gui), "--smoke-test"], check=True, timeout=30, env=environment)
        for name in ("simple-sync-pull", "simple-sync-serve"):
            check_console_help(ROOT / f"dist/{name}/{name}{suffix}", name, language, environment)
    if args.skip_archive:
        return 0
    stage = ROOT / "build/release"
    # Only recreate the build script's own staging directory.
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    gui_name = "Simple Sync Server.app" if sys.platform == "darwin" else "SimpleSyncServer"
    for name in (gui_name, "simple-sync-pull", "simple-sync-serve"):
        shutil.copytree(ROOT / "dist" / name, stage / name, symlinks=True)
    shutil.copy2(ROOT / "README.md", stage)
    shutil.copy2(ROOT / "README.en.md", stage)
    shutil.copytree(ROOT / "docs", stage / "docs")
    shutil.copytree(ROOT / "packaging/services", stage / "services")
    if sys.platform.startswith("linux"):
        shutil.copy2(ROOT / "packaging/simple-sync-server.desktop", stage)
        shutil.copy2(ROOT / "src/sync_server/assets/app-icon.png", stage / "simple-sync-server.png")
    label = f"simple-sync-{version('simple-sync-server')}-{platform.system().lower()}-{platform.machine().lower()}"
    # tar preserves macOS framework symlinks and Unix executable permissions.
    archive = shutil.make_archive(str(ROOT / "dist" / label), "zip" if sys.platform == "win32" else "gztar", root_dir=stage)
    print(f"Release archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
