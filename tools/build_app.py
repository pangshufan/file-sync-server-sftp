"""Build and archive GUI + CLI for the current OS and Python architecture."""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "tools/generate_icons.py")], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
                    str(ROOT / "packaging/simple-sync.spec")], cwd=ROOT, check=True,
                   env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(ROOT / "build/pyinstaller-cache")})
    suffix = ".exe" if sys.platform == "win32" else ""
    gui = (ROOT / "dist/Simple Sync Server.app/Contents/MacOS/SimpleSyncServer"
           if sys.platform == "darwin" else ROOT / f"dist/SimpleSyncServer/SimpleSyncServer{suffix}")
    for language in ("en", "zh"):
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "SIMPLE_SYNC_LANGUAGE": language}
        subprocess.run([str(gui), "--smoke-test"], check=True, timeout=30, env=environment)
        for name in ("simple-sync-pull", "simple-sync-serve"):
            subprocess.run([str(ROOT / f"dist/{name}/{name}{suffix}"), "--help"],
                           check=True, timeout=30, env=environment)
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
