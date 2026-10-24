"""Check catalog coverage, format placeholders and bilingual document pairs."""
import ast
import json
import re
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]


def check_catalog():
    catalog = json.loads((ROOT / "src/sync_server/locales/en.json").read_text(encoding="utf-8"))
    errors = []
    for source, translated in catalog.items():
        if not translated or any("\u4e00" <= char <= "\u9fff" for char in translated):
            errors.append(f"Missing English translation: {source}")
        def fields(text):
            return {(name, spec, conversion) for _, name, spec, conversion in Formatter().parse(text) if name is not None}
        if fields(source) != fields(translated):
            errors.append(f"Placeholder mismatch: {source}")
    for path in (ROOT / "src/sync_server").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                source = node.args[0].value
                if source not in catalog:
                    errors.append(f"Uncatalogued message at {path.name}:{node.lineno}: {source}")
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and any("\u4e00" <= char <= "\u9fff" for char in node.value)):
                parent = parents.get(node)
                wrapped = (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                           and parent.func.id == "tr" and parent.args[0] is node)
                # These two dictionaries intentionally store untranslated labels.
                label = path.name == "i18n.py"
                ancestor = parent
                while ancestor and not label:
                    label = (path.name == "config.py" and isinstance(ancestor, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == "LABELS" for target in ancestor.targets))
                    ancestor = parents.get(ancestor)
                if not wrapped and not (label and node.value in catalog):
                    errors.append(f"Unwrapped message at {path.name}:{node.lineno}: {node.value}")
    return errors


def check_documents():
    errors = []
    if not (ROOT / "README.en.md").is_file():
        errors.append("Missing README.en.md")
    for source in (ROOT / "docs").glob("*.md"):
        if not (source.parent / "en" / source.name).is_file():
            errors.append(f"Missing English document: {source.name}")
    for source in [ROOT / "README.md", ROOT / "README.en.md", *(ROOT / "docs").rglob("*.md")]:
        for target in re.findall(r"\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            if path and not (source.parent / path).exists():
                errors.append(f"Broken document link in {source.relative_to(ROOT)}: {target}")
    return errors


def main():
    errors = check_catalog() + check_documents()
    if errors:
        raise SystemExit("\n".join(errors))
    print("Catalog placeholders, message coverage and English document pairs are valid")


if __name__ == "__main__":
    main()
