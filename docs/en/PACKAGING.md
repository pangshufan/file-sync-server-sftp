# Application icon and multi-platform packaging

[简体中文](../PACKAGING.md) | [English home](../../README.en.md)

## Icon

The icon combines a blue rounded background, folder and blue/teal sync arrows. Editable source is src/sync_server/assets/app-icon.svg. Runtime uses the adjacent 512px PNG; Windows uses packaging/icons/app.ico and macOS packaging/icons/app.icns.

After editing SVG, run `python tools/generate_icons.py`. Qt SVG renders transparent PNG, multi-size ICO (16–256px) and ICNS (16–1024px), without another image editor. Commit generated assets with their source. QApplication sets the window icon; packaging sets the native application icon.

## Native builds

Install Python 3.12 on the target OS, activate an environment, and run from the repository root:

```bash
python -m pip install ".[build]"
python tools/build_app.py
```

On the current development machine:

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate /Volumes/T9/sync/env/sync-env
```

The build extra pins PyInstaller; runtime dependencies follow pyproject.toml ranges. Building regenerates icons and replaces its own build staging directories and same-named dist output. `--skip-archive` builds directory outputs only. Python and dependencies are bundled, so target machines do not need Python/Conda.

| Platform | Desktop executable | Client executable | Release format |
| --- | --- | --- | --- |
| macOS | `dist/Simple Sync Server.app` | `dist/simple-sync-pull/simple-sync-pull` | `.tar.gz` |
| Windows | `dist/SimpleSyncServer/SimpleSyncServer.exe` | `dist/simple-sync-pull/simple-sync-pull.exe` | `.zip` |
| Linux | `dist/SimpleSyncServer/SimpleSyncServer` | `dist/simple-sync-pull/simple-sync-pull` | `.tar.gz` |

The headless executable is in dist/simple-sync-serve, named simple-sync-serve.exe on Windows and simple-sync-serve elsewhere. It requires --data-dir. Its bundle excludes Qt. System service templates are included in services in the archive.

Release names include project version, OS and build Python architecture. Each program uses directory mode: keep _internal and other neighboring dependencies. Do not copy only the executable. macOS archives include the .app, client and headless server, excluding the intermediate GUI directory. Unix tar preserves framework symlinks and executable permissions.

Build Windows/macOS/Linux on their respective native systems. Architecture follows Python; these are not cross-compiled or universal2 builds. Linux glibc is not bundled, so build and validate on the oldest required distribution. Qt still requires a supported OS, desktop and graphics libraries. Minimal Linux desktops may need extra xcb/OpenGL libraries.

## Continuous integration

.github/workflows/build.yml runs manually or on v* tags, builds on native Windows/macOS/Ubuntu 22.04 runners, and uploads Actions artifacts. It does not publish a Release automatically. Download and extract each platform archive. Runner-native architecture is reflected in artifact names; add native runners for other architectures.

CI uses an independent Python environment, without this machine's absolute Conda path. GUI, client and headless server have separate dependency analysis; the latter two exclude Qt. PyInstaller hooks collect Qt/cryptography libraries, and watchdog observers are explicitly collected. Functional tests run before packaging. Build smoke checks start the GUI offscreen in a temporary data directory, and run client/headless --help; startup failures stop the build.

0.5 includes the application English JSON catalog in every bundle, Qt's Simplified Chinese stock-dialog catalog in the GUI, and Chinese/English documentation in the release archive. Build smoke checks run both language overrides. macOS declares English and Simplified Chinese localizations. See [Localization](LOCALIZATION.md).

## Startup and state

On macOS, extract and optionally move the .app into Applications. On Windows, extract and double-click the exe. On Linux, run the GUI executable. For the supplied .desktop template, replace Exec with the executable's absolute path, and install simple-sync-server.png into a user icon directory or set Icon to its absolute path.

Organization SimpleSync and application name SFTPServer are unchanged. Packaged GUI state still uses Qt's standard application data directory, shown at the window bottom. Configuration, database, host keys and shared files are never included in release bundles or written into the application installation directory. Stop the old app, preserve its data directory, and follow [Migration](MIGRATION.md).

These are portable archives, without MSI/DMG/AppImage installers. Service templates are provided but not installed automatically. macOS uses the default ad-hoc build signature, without Developer ID notarization. Windows lacks Authenticode signing. Before public distribution, configure your own signing credentials and validate trust/first-launch behavior on target machines. Credentials do not belong in the repository.

## Acceptance

On each platform verify application/window icons, first launch/restart, system light/dark appearance and switching, language selection and English text layout, dialogs/selections/disabled text, persistence, listener start/stop, login/permissions, incremental scanning/confirmation, and CLI help plus actual downloads. Check firewall and directory authorization on first deployment. A successful CI build does not prove every runtime scenario passed.

References: [PyInstaller platform limits](https://pyinstaller.org/en/stable/), [spec/BUNDLE configuration](https://pyinstaller.org/en/stable/spec-files.html).
