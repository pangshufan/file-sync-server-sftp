"""Qt-free message catalog shared by the GUI, workers and command-line tools."""
import json
import locale
import os
from pathlib import Path


def choose_language(preferences):
    for value in preferences:
        language = value.replace("_", "-").split("-", 1)[0].lower()
        if language in ("zh", "en"):
            return language
    return "en"


def system_preferences():
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(name):
            return os.environ[name].split(":")
    try:
        return [locale.getlocale()[0] or "en"]
    except (ValueError, TypeError):
        return ["en"]


def detect_language(preferences=None):
    override = os.environ.get("SIMPLE_SYNC_LANGUAGE", "system")
    if override != "system":
        return choose_language([override])
    return choose_language(system_preferences() if preferences is None else preferences)


_language = detect_language()
_english = json.loads((Path(__file__).parent / "locales/en.json").read_text(encoding="utf-8"))


def set_language(language):
    """Choose once at startup; does not change any persisted settings or IDs."""
    global _language
    _language = choose_language([language])


def current_language():
    return _language


def tr(message, **values):
    translated = _english.get(message, message) if _language == "en" else message
    return translated.format(**values) if values else translated


def status_label(value):
    labels = {"pending": "待同步", "failed": "失败", "done": "已同步",
              "manual": "手动", "auto": "自动", "upsert": "更新", "delete": "删除"}
    return tr(labels.get(value, value))
