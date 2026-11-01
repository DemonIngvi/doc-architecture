#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6", "jsonschema>=4"]
# ///
"""Архитектура как данные: YAML по единице — источник, ARCHITECTURE-SPINE.md — сборка.

Команды:
  split <spine> --out <dir>   разобрать Spine на единицы, только если сборка совпадает с ним побайтно
  build <dir> --spine <file>  собрать Spine из единиц
  check <dir> --spine <file>  проверить схемы, ссылки и ручные правки Spine
  unit <dir> <ID>             показать единицу и её связи
  impact <dir> <ID>           показать, что затронет отказ или изменение единицы
  pack <dir> --reqs FR-1,...  узкий пакет архитектуры для проверки требований
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

import jsonschema
import yaml

SCHEMAS = pathlib.Path(__file__).resolve().parent.parent / "schemas"
HASH_FILE = ".spine.sha256"
AD_HEADING = re.compile(r"^### (AD-\d+) — (.+)$")
AD_FIELD = re.compile(r"^- \*\*(Binds|Prevents|Rule):\*\* (.*)$")
TABLE_SECTIONS = {
    "Consistency Conventions": ("conventions.yaml", ["concern", "convention"]),
    "Stack": ("stack.yaml", ["name", "version"]),
    "Capability → Architecture Map": ("capability-map.yaml", ["capability", "lives_in", "governed_by"]),
}
FIELD_LABELS = {"binds": "Binds", "prevents": "Prevents", "rule": "Rule"}


class Dumper(yaml.SafeDumper):
    """Многострочный текст пишется блоком, чтобы YAML оставался читаемым."""


def _str(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


Dumper.add_representer(str, _str)


def dump(data):
    return yaml.dump(data, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=100)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read(path):
    return path.read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_frontmatter(text):
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise SystemExit("В Spine нет frontmatter")
    return match.group(1), text[match.end():]


def split_sections(body):
    """Делит тело по заголовкам ## вне блоков кода. Первая часть — вводная."""
    parts, current, fenced = [], [], False
    for line in body.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        if line.startswith("## ") and not fenced:
            parts.append("\n".join(current))
            current = []
        current.append(line)
    parts.append("\n".join(current))
    return parts


def parse_table(text, columns):
    rows = []
    for line in text.split("\n"):
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == len(columns):
            rows.append(dict(zip(columns, cells)))
    return rows[1:]


def render_table(rows, columns, header):
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(row[c] for c in columns) + " |" for row in rows]
    return "\n".join(lines)


def parse_decisions(section):
    """Отделяет блоки AD от остального текста раздела."""
    lines = section.split("\n")
    starts = [i for i, line in enumerate(lines) if AD_HEADING.match(line)]
    if not starts:
        return section, []
    head, decisions = "\n".join(lines[:starts[0]]), []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        ad_id, title = AD_HEADING.match(lines[start]).groups()
        unit = {"id": ad_id, "title": title}
        for line in lines[start + 1:end]:
            field = AD_FIELD.match(line)
            if field:
                unit[field.group(1).lower()] = field.group(2)
        unit["binds"] = [item.strip() for item in unit["binds"].split(",")]
        decisions.append(unit)
    return head, decisions


def parse_edges(section):
    """Извлекает граф зависимостей модулей из первой схемы раздела."""
    match = re.search(r"```mermaid\ngraph TD\n(.*?)\n```", section, re.S)
    if not match:
        return section, []
    edges = [dict(zip(("from", "to"), re.split(r"\s*-->\s*", line.strip()))) for line in match.group(1).split("\n")]
    return section.replace(match.group(0), "{{modules}}"), edges


def split_spine(spine, out):
    text = read(spine)
    frontmatter, body = split_frontmatter(text)
    files, order, parts = {}, [], split_sections(body)
    files["frontmatter.yaml"] = frontmatter + "\n"
    for index, part in enumerate(parts):
        title = part.split("\n", 1)[0][3:].strip() if part.startswith("## ") else ""
        name = f"sections/{index:02d}.md"
        if title in TABLE_SECTIONS:
            file_name, columns = TABLE_SECTIONS[title]
            table = re.search(r"((?:^\|.*\n?)+)", part, re.M).group(1)
            header = [cell.strip() for cell in table.split("\n")[0].strip("|").split("|")]
            files[file_name] = dump({"header": header, "rows": parse_table(table, columns)})
            part = part.replace(table.rstrip("\n"), "{{table}}")
        elif title == "Deferred":
            items = re.findall(r"^- (.+)$", part, re.M)
            files["deferred.yaml"] = dump({"items": items})
            part = part.replace("\n".join(f"- {item}" for item in items), "{{deferred}}")
        elif title == "Invariants & Rules":
            head, decisions = parse_decisions(part)
            head, edges = parse_edges(head)
            files["modules.yaml"] = dump({"edges": edges})
            for unit in decisions:
                files[f"decisions/{unit['id']}.yaml"] = dump(unit)
            part = head + "\n{{decisions}}"
            order_ids = [unit["id"] for unit in decisions]
            files["decisions/_order.yaml"] = dump({"order": order_ids})
        files[name] = part
        order.append(name)
    files["_order.yaml"] = dump({"sections": order})
    return files, text


def render_decision(unit):
    binds = ", ".join(unit["binds"])
    lines = [f"### {unit['id']} — {unit['title']}", "", f"- **Binds:** {binds}"]
    lines += [f"- **Prevents:** {unit['prevents']}", f"- **Rule:** {unit['rule']}", ""]
    return "\n".join(lines)


def source(files, name):
    """Читает единицу из словаря при разборе или с диска при сборке."""
    if isinstance(files, dict):
        return files[name]
    return read(files / name)


def present(files, name):
    return name in files if isinstance(files, dict) else (files / name).exists()


def build_spine(files):
    def data(name):
        return yaml.safe_load(source(files, name))

    parts = []
    for name in data("_order.yaml")["sections"]:
        part = source(files, name)
        if "{{table}}" in part:
            for file_name, columns in TABLE_SECTIONS.values():
                title = next(k for k, v in TABLE_SECTIONS.items() if v[0] == file_name)
                if part.startswith(f"## {title}") and present(files, file_name):
                    table = data(file_name)
                    part = part.replace("{{table}}", render_table(table["rows"], columns, table["header"]))
        if "{{deferred}}" in part:
            part = part.replace("{{deferred}}", "\n".join(f"- {i}" for i in data("deferred.yaml")["items"]))
        if "{{modules}}" in part:
            edges = "\n".join(f"  {e['from']} --> {e['to']}" for e in data("modules.yaml")["edges"])
            part = part.replace("{{modules}}", f"```mermaid\ngraph TD\n{edges}\n```")
        if "{{decisions}}" in part:
            order = data("decisions/_order.yaml")["order"]
            rendered = "\n".join(render_decision(data(f"decisions/{ad}.yaml")).rstrip("\n") + "\n" for ad in order)
            part = part.replace("{{decisions}}", rendered)
        parts.append(part)
    return f"---\n{source(files, 'frontmatter.yaml').rstrip(chr(10))}\n---\n" + "\n".join(parts)


def schema_for(rel):
    top = rel.split("/")[0]
    names = {"decisions": "decision", "components": "component", "blocks": "block", "integrations": "integration",
             "stores": "store", "actors": "actor", "processes": "process", "services": "service", "systems": "system",
             "functions": "function", "data": "data", "flows": "flow", "nodes": "node"}
    if rel.endswith("_order.yaml"):
        return None
    if top in names and "/" in rel:
        return names[top]
    return {"params.yaml": "params", "fmea.yaml": "fmea", "quality.yaml": "quality", "types.yaml": "types"}.get(rel)


def check(root, spine):
    problems = []
    for path in sorted(root.rglob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        name = schema_for(rel)
        if name:
            schema = yaml.safe_load(read(SCHEMAS / f"{name}.schema.yaml"))
            for error in jsonschema.Draft202012Validator(schema).iter_errors(load(path)):
                problems.append(f"{rel}: {error.message}")
    if (root / "decisions/_order.yaml").exists():
        order = load(root / "decisions/_order.yaml")["order"]
        real = {path.stem for path in (root / "decisions").glob("AD-*.yaml")}
        problems += [f"AD вне порядка: {ad}" for ad in sorted(real - set(order))]
        problems += [f"В порядке нет файла: {ad}" for ad in sorted(set(order) - real)]
    components = {path.stem for path in (root / "components").glob("*.yaml")} if (root / "components").exists() else set()
    for folder in sorted((root / "blocks").iterdir()) if (root / "blocks").exists() else []:
        if folder.is_dir() and components and folder.name not in components:
            problems.append(f"blocks/{folder.name}: нет такого компонента")
    if spine.exists():
        recorded = (root / HASH_FILE).read_text(encoding="utf-8").strip() if (root / HASH_FILE).exists() else ""
        if sha(read(spine)) != recorded:
            problems.append("Spine не совпадает с последней сборкой: правьте YAML и выполните build")
    return problems


def all_units(root):
    for path in sorted(root.rglob("*.yaml")):
        if path.name.startswith("_") or not isinstance(load(path), dict) or "id" not in load(path):
            continue
        yield path, load(path)


def show_unit(root, unit_id):
    found = next(((p, u) for p, u in all_units(root) if u["id"] == unit_id), None)
    if not found:
        raise SystemExit(f"Единица {unit_id} не найдена")
    path, unit = found
    links = sorted(p.stem for p, other in all_units(root) if unit_id in json.dumps(other, ensure_ascii=False) and other is not unit and p != path)
    return dump({k: v for k, v in unit.items() if v not in (None, [], "")}) + f"связано: {', '.join(links) or 'нет'}\n"


MODEL_FOLDERS = ("actors", "processes", "services", "systems", "functions", "components", "data", "stores",
                 "flows", "integrations", "nodes", "blocks")
SKIP_FIELDS = {"id", "title", "owner", "lifecycle", "criticality"}


def model_units(root):
    return {unit["id"]: unit for path, unit in all_units(root) if path.relative_to(root).parts[0] in MODEL_FOLDERS}


def references(unit, ids):
    """Находит в единице значения, которые являются кодами других единиц."""
    found, stack = set(), [v for k, v in unit.items() if k not in SKIP_FIELDS]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack += value.values()
        elif isinstance(value, list):
            stack += value
        elif isinstance(value, str) and value in ids:
            found.add(value)
    return found


def impact(root, unit_id):
    """Что затронет отказ или изменение единицы: всё, что ссылается на неё прямо или через других."""
    units = model_units(root)
    if unit_id not in units:
        raise SystemExit(f"Единица {unit_id} не найдена")
    used_by = {key: {other for other, unit in units.items() if key in references(unit, units)} for key in units}
    seen, queue = set(), [unit_id]
    while queue:
        for other in used_by[queue.pop()] - seen - {unit_id}:
            seen.add(other)
            queue.append(other)
    return [f"{key}: {units[key]['title']} ({units[key]['criticality']})" for key in sorted(seen)]


def model_pack(root, wanted):
    """Функции требований и то, с чем они связаны на один шаг: данные, потоки, интеграции."""
    units = model_units(root)
    chosen = {key for key, unit in units.items() if wanted & set(unit.get("reqs") or []) and unit.get("system")}
    data = {name for key in chosen for name in (*units[key]["reads"], *units[key]["writes"])}
    flows = {key for key, unit in units.items() if unit.get("flow_type") and data & set(unit["objects"])}
    channels = {units[key]["over"] for key in flows if units[key].get("over")}
    keep = sorted(chosen | data | flows | (channels & set(units)))
    trimmed = [{k: v for k, v in units[key].items() if k not in ("owner", "lifecycle", "criticality")} for key in keep]
    return ["## Модель системы", dump(trimmed)] if trimmed else []


def pack(root, reqs):
    """Собирает только то, что относится к требованиям, вместо всего Spine."""
    wanted = set(reqs)
    chosen = [u for p, u in all_units(root) if p.parent.name == "decisions"
              and ("all" in u["binds"] or wanted & expand(u["binds"]))]
    out = ["# Архитектура: только относящееся к " + ", ".join(sorted(wanted)), ""]
    out += ["## Парадигма", yaml.safe_load(read(root / "frontmatter.yaml"))["paradigm"], ""]
    if (root / "stack.yaml").exists():
        out += ["## Стек", "\n".join(f"{r['name']} {r['version']}" for r in load(root / "stack.yaml")["rows"]), ""]
    if (root / "modules.yaml").exists():
        edges = load(root / "modules.yaml")["edges"]
        out += ["## Разрешённые зависимости модулей", "\n".join(f"{e['from']} -> {e['to']}" for e in edges), ""]
    out += ["## Решения", dump(chosen), "## Соглашения", dump(load(root / "conventions.yaml")["rows"])]
    if (root / "capability-map.yaml").exists():
        rows = [r for r in load(root / "capability-map.yaml")["rows"] if wanted & expand([r["capability"]])]
        out += ["## Где живёт", dump(rows)]
    if (root / "deferred.yaml").exists():
        out += ["## Отложено", dump(load(root / "deferred.yaml")["items"])]
    return "\n".join(out + model_pack(root, wanted))


def expand(ids):
    """Разворачивает записи вида «FR-1, FR-2» и «FR-11 — FR-15» в отдельные коды."""
    result = set()
    for item in ids:
        text = item.replace("`", "")
        for start, end in re.findall(r"(\w+-\d+) — (\w+-\d+)", text):
            prefix = start.split("-")[0]
            first, last = int(start.split("-")[1]), int(end.split("-")[1])
            result |= {f"{prefix}-{n}" for n in range(first, last + 1)}
        result |= set(re.findall(r"\w+-\d+", text))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("split", "build", "check"):
        cmd = sub.add_parser(name)
        cmd.add_argument("path", type=pathlib.Path)
        cmd.add_argument("--out", type=pathlib.Path)
        cmd.add_argument("--spine", type=pathlib.Path)
    cmd = sub.add_parser("unit")
    cmd.add_argument("path", type=pathlib.Path)
    cmd.add_argument("id")
    cmd = sub.add_parser("impact")
    cmd.add_argument("path", type=pathlib.Path)
    cmd.add_argument("id")
    cmd = sub.add_parser("pack")
    cmd.add_argument("path", type=pathlib.Path)
    cmd.add_argument("--reqs", required=True)
    args = parser.parse_args()

    if args.command == "split":
        files, original = split_spine(args.path, args.out)
        rebuilt = build_spine(files)
        if rebuilt != original:
            at = next((i for i, (a, b) in enumerate(zip(rebuilt, original)) if a != b), min(len(rebuilt), len(original)))
            raise SystemExit(f"Сборка не совпала со Spine в позиции {at}, файлы не записаны:\n{original[at-60:at+60]!r}\n{rebuilt[at-60:at+60]!r}")
        for name, text in files.items():
            write(args.out / name, text)
        write(args.out / HASH_FILE, sha(original) + "\n")
        print(f"Единиц записано: {len(files)}, сборка совпала со Spine побайтно")
    elif args.command == "build":
        text = build_spine(args.path)
        write(args.spine, text)
        write(args.path / HASH_FILE, sha(text) + "\n")
        print(f"Spine собран: {args.spine}")
    elif args.command == "check":
        problems = check(args.path, args.spine)
        print("\n".join(problems) if problems else "Архитектура без замечаний")
        sys.exit(1 if problems else 0)
    elif args.command == "unit":
        print(show_unit(args.path, args.id))
    elif args.command == "impact":
        print("\n".join(impact(args.path, args.id)) or "Ничего не затронуто")
    elif args.command == "pack":
        print(pack(args.path, [r.strip() for r in args.reqs.split(",")]))


if __name__ == "__main__":
    main()
