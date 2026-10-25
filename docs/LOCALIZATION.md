# 多语言与翻译维护

[English](en/LOCALIZATION.md)

## 语言选择

0.5.0 首先支持简体中文和英文。桌面启动时读取 Qt 的系统 UI 语言偏好列表，按顺序选择支持的语言：zh（包括 zh-CN、zh-TW、zh-Hans 等）显示简体中文，en 显示英文。列表中没有支持的语言（含 C locale）时回退英文。当前没有繁体中文单独译本。

语言在进程启动时确定；修改系统语言后重新启动应用。明暗主题仍在运行中自动切换，语言选择不影响主题。用户名、目录、文件名、userid、权限键、协议操作/状态、错误码、摘要、数据库版本和已有同步记录不翻译。

可用环境变量覆盖自动识别：

```bash
SIMPLE_SYNC_LANGUAGE=en python -m sync_server
SIMPLE_SYNC_LANGUAGE=zh python -m sync_server
SIMPLE_SYNC_LANGUAGE=system python -m sync_server
```

Windows PowerShell：

```powershell
$env:SIMPLE_SYNC_LANGUAGE = "en"
simple-sync-server
```

未设置或设置 system 时自动识别。覆盖只作用于当前进程，不写入 config.json。无头服务和客户端保持无 Qt 导入，自动识别使用 LANGUAGE、LC_ALL、LC_MESSAGES、LANG（按该顺序）及 Python locale；服务进程环境可能与桌面会话不同，可显式设置覆盖。

## 翻译范围

覆盖主窗口、用户/权限/公钥/同步设置、登录保护、同步明细、动态计数与状态、提示、校验错误、运行消息、命令行帮助与密码提示。标准 Qt 按钮和日历使用 qtbase_zh_CN.qm；英文使用 Qt 默认文本。原生文件选择器的系统控件仍由操作系统本地化。

持久 JSONL 的事件名和字段名保持不变，新生成的说明文本使用服务端语言。已有日志、SQLite 中保存的旧错误和客户端自行上报的错误文本不重写；第三方组件原生错误可能仍使用其自身语言。第三方客户端应依据错误码处理，不依赖 message 文案。

0.5.2 起，客户端和无头入口在 argparse/提示输出之前，将可重新配置的 stdout/stderr 显式设为 UTF-8；管道和文件接收端按 UTF-8 解码。无 reconfigure 的自定义捕获对象保留原状，不在该步骤更改 stdin。PyInstaller 包还通过 spec 的 `X utf8=1` 在解释器启动时启用 UTF-8，避免冻结程序忽略 CI 的 Python 环境变量后回到 CP1252。详见 [PyInstaller 解释器选项](https://pyinstaller.org/en/stable/spec-files.html#specifying-python-interpreter-options)。

## 词条与打包

src/sync_server/i18n.py 为无 Qt 的语言检测与格式化模块；中文源消息通过 tr 调用，英文位于 src/sync_server/locales/en.json。动态变量使用命名占位符，禁止把已经插值的字符串传入 tr。权限标签延迟翻译，避免 GUI 初始化前导入配置时固定语言。UI 状态显示与底层协议值分离。

qt_i18n.py 在主窗口创建前初始化语言，并安装 Qt 自带的中文翻译。英文词条随 wheel 打包；PyInstaller 三个入口均收集词条，GUI 额外收集 Qt 中文 qm 文件。无头包不因此引入 Qt。

```bash
python tools/check_translations.py
python -m pytest tests/test_i18n.py -q
```

检查工具验证英文词条非空、没有残留中文、占位符名称/格式一致、静态 tr 调用已收录、中文消息未漏包装，以及 docs 下中文文件都有对应英文文件、文档本地链接有效。新增界面文案仍需开发者使用 tr，并进行实际界面检查，避免英文变长后裁切。

新增语言需扩展语言匹配、增加同结构词条、选择对应 Qt 翻译并更新打包/测试。翻译提交应同时更新中英文文档，保留命令、参数、JSON 字段和历史验证数据的含义；历史协议 v1 必须继续标注已停用。

## 英文文档

README.en.md 为英文入口；docs/en 下包含运维、迁移、打包、多语言、执行清单、验证记录及协议 v1/v2 的英文版本。每份文档提供对应语言链接，分发包同时包含两套文档。
