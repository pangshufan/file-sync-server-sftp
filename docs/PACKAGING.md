# 应用图标与多平台打包

[English](en/PACKAGING.md)

## 图标

图标采用蓝色圆角底、文件夹和蓝/青双向同步箭头。可编辑源文件为 `src/sync_server/assets/app-icon.svg`，运行时使用同目录的 512px PNG，Windows 使用 `packaging/icons/app.ico`，macOS 使用 `packaging/icons/app.icns`。

修改 SVG 后运行 `python tools/generate_icons.py`。脚本用 Qt SVG 渲染，无需额外图像处理软件，生成透明背景 PNG、多尺寸 ICO（16–256px）和 ICNS（16–1024px）。生成文件一并纳入版本管理。应用入口设置 QApplication 图标，窗口继承此图标；系统应用图标由打包配置设置。

## 本机构建

在目标操作系统安装 Python 3.12，创建或激活开发环境，然后在项目根目录执行：

```bash
python -m pip install ".[build]"
python tools/build_app.py
```

当前开发机器也可以先执行：

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate /Volumes/T9/sync/env/sync-env
```

`build` 可选依赖固定 PyInstaller 版本；运行依赖仍遵循 pyproject.toml 的版本范围。构建会重新生成图标，覆盖项目自身的 build 暂存目录及同名 dist 输出。`--skip-archive` 只构建目录产物。产物包含 Python 和依赖，目标机器无需安装 Conda/Python。

| 平台 | 桌面入口 | 命令行入口 | 分发包 |
| --- | --- | --- | --- |
| macOS | `dist/Simple Sync Server.app` | `dist/simple-sync-pull/simple-sync-pull` | `.tar.gz` |
| Windows | `dist/SimpleSyncServer/SimpleSyncServer.exe` | `dist/simple-sync-pull/simple-sync-pull.exe` | `.zip` |
| Linux | `dist/SimpleSyncServer/SimpleSyncServer` | `dist/simple-sync-pull/simple-sync-pull` | `.tar.gz` |

无头入口位于各平台 `dist/simple-sync-serve/`，Windows 文件名为 `simple-sync-serve.exe`，其他平台为 `simple-sync-serve`；启动须指定 `--data-dir`。无头包不包含 Qt，系统服务模板随分发包置于 `services/`。

分发包名包含项目版本、系统和构建 Python 的架构。每个程序均使用目录模式，必须保留旁边的 `_internal` 等依赖；不能只复制可执行文件。macOS 发布包只包含 `.app` 和独立 CLI、无头服务，不包含 PyInstaller 中间生成的 GUI 目录。Unix tar 包保留框架符号链接与执行权限。

Windows、macOS、Linux 必须各自在对应系统构建；架构跟随 Python，不是交叉编译或 macOS universal2。Linux 的 glibc 不随程序打包，应在所需最低发行版上构建并验收。Qt 仍要求受支持的操作系统、桌面环境及图形库，Linux 精简系统可能需要额外安装 xcb/OpenGL 库。

## CI 配置

`.github/workflows/build.yml` 支持手动触发和 `v*` 标签触发，在 Windows、macOS、Ubuntu 22.04 原生 runner 上构建并上传 Actions artifacts，不自动发布 Release。上传后下载各平台的压缩包解压即可。runner 使用其原生架构，结果名称标识实际架构；需要其他架构时增加对应原生 runner。

CI 使用独立 Python 环境，不引用本机 Conda 绝对路径。GUI、客户端和无头服务采用独立依赖分析，后两者不打包 Qt。PyInstaller 的 Qt/cryptography hooks 收集运行库，watchdog 观察器显式收集。构建脚本还会运行打包 GUI 的 `--smoke-test`（offscreen 加载图标、创建窗口并退出，数据写入临时目录）及 CLI/无头入口的 `--help`，启动失败会中止打包。

0.5 所有入口均打包英文 JSON 词条，GUI 额外收集 Qt 简体中文标准控件翻译；归档包含 README.en.md 和 docs/en 全套英文文档。构建检查同时验证中英文启动，macOS 声明中英文支持，详见 [多语言说明](LOCALIZATION.md)。

0.5.2 的三个 EXE 均显式启用 `X utf8=1`；不能只依赖工作流中的 PYTHONUTF8。源码工具子进程用 UTF-8 环境启动；冻结程序烟测则故意传入 PYTHONUTF8=0 / PYTHONIOENCODING=cp1252:strict，并捕获中英文帮助输出，严格按 UTF-8 解码和检查对应语言正文。失败子进程的 stdout/stderr 会先输出再报告失败，避免丢失实际报错。

## 启动与数据

macOS 解压后可将 `.app` 拖入“应用程序”；Windows 解压后双击 exe；Linux 可直接运行 GUI 程序。Linux 附带 `.desktop` 模板，安装快捷方式时须将 `Exec` 改成实际可执行文件的绝对路径，将图标安装到用户图标目录并命名为 `simple-sync-server.png`，或将 `Icon` 改成图标绝对路径。

组织名 `SimpleSync` 和应用名 `SFTPServer` 保持不变。打包程序继续使用 Qt 标准应用数据目录，窗口底部可查看实际位置。配置、SQLite、SSH 主机密钥和用户共享文件不进入安装包，也不写入 `.app`/安装目录。更新时停止旧程序，保留数据目录，按 [迁移说明](MIGRATION.md) 备份和升级。

当前产物为便携包，不包含 MSI/DMG/AppImage 安装器；提供系统服务配置模板，但不自动安装。macOS 本机 PyInstaller 构建使用默认临时签名，不包含 Developer ID 公证；Windows 不包含 Authenticode 签名。正式对外分发前应接入持有者自己的签名凭据，并在目标机器验证系统信任和首次启动流程。签名凭据不放入仓库。

## 验收

每个平台均需检查：应用/窗口图标、首次启动和重启、系统明暗配色及运行中切换（含弹窗、选中项、禁用文字）、设置持久化、启动/停止监听、登录和目录权限、增量扫描及下载确认、独立 CLI 的 `--help` 和实际下载。首次部署还需检查防火墙、共享目录访问授权。CI 构建成功不代表这些运行场景已经验收。

参考：[PyInstaller 构建平台限制](https://pyinstaller.org/en/stable/)、[spec/BUNDLE 配置](https://pyinstaller.org/en/stable/spec-files.html)。
