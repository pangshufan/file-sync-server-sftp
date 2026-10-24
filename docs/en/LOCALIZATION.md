# Localization and translation maintenance

[简体中文](../LOCALIZATION.md) | [English home](../../README.en.md)

## Language selection

0.5.0 initially supports Simplified Chinese and English. At desktop startup, Qt's ordered system UI language preferences are matched against supported languages. Any zh variant, including zh-CN, zh-TW and zh-Hans, selects Simplified Chinese; en selects English. If no supported preference is found, including the C locale, English is used. There is currently no separate Traditional Chinese catalog.

Language is selected at process startup. Restart after changing the system language. Light/dark appearance continues updating live, independently of language. Usernames, directories, filenames, userids, permission keys, protocol operations/statuses, error codes, checksums, schema versions and existing progress are not translated.

Override automatic detection with an environment variable:

```bash
SIMPLE_SYNC_LANGUAGE=en python -m sync_server
SIMPLE_SYNC_LANGUAGE=zh python -m sync_server
SIMPLE_SYNC_LANGUAGE=system python -m sync_server
```

Windows PowerShell:

```powershell
$env:SIMPLE_SYNC_LANGUAGE = "en"
simple-sync-server
```

Unset or system means automatic detection. The override is process-local and is not saved to config.json. Headless server/client remain Qt-free and detect from LANGUAGE, LC_ALL, LC_MESSAGES, LANG, in that order, then Python locale. Service environments may differ from the desktop session; set an explicit override if needed.

## Translation scope

Coverage includes the main window, user/permission/key/sync settings, login protection, file details, dynamic counts/status, prompts, validation errors, runtime messages, CLI help and password prompts. Qt buttons/calendar use qtbase_zh_CN.qm for Chinese; English uses Qt defaults. Native file-dialog system controls remain localized by the operating system.

JSONL event/field identifiers remain unchanged; new human-readable text follows the server language. Existing logs, old database error strings and client-reported text are not rewritten. Third-party native errors may use their own language. Third-party clients should act on error codes, never translated message text.

## Catalogs and distribution

src/sync_server/i18n.py provides Qt-free detection and formatting. Chinese source messages use tr calls; English translations reside in src/sync_server/locales/en.json. Dynamic values use named placeholders. Never pass an already interpolated string to tr. Permission labels translate lazily so importing configuration before GUI initialization cannot freeze their language. Displayed status labels are separate from protocol values.

qt_i18n.py initializes language before creating the main window and installs Qt's Chinese translator. Wheels include the English JSON catalog. PyInstaller includes it in all three entry bundles, with the Chinese qm additionally in the GUI. The headless distribution still excludes Qt.

```bash
python tools/check_translations.py
python -m pytest tests/test_i18n.py -q
```

The checker verifies nonempty English entries without Chinese leftovers, matching placeholder names/formats, static tr coverage, unwrapped Chinese messages, an English counterpart for every Chinese document in docs, and valid local document links. Developers must still use tr for new messages and inspect layouts for longer translations.

Adding a language requires extending matching, supplying a matching catalog, choosing Qt translations and updating packaging/tests. Keep Chinese and English documentation aligned. Preserve command lines, arguments, JSON fields and the meaning of historical measurements. Historical protocol v1 must stay explicitly marked obsolete.

## English documents

README.en.md is the English entry. docs/en contains Operations, Migration, Packaging, Localization, Implementation checklist, Validation and both protocol versions. Documents link to their language counterparts; release archives contain both sets.
