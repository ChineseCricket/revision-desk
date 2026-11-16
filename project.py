"""Read a private workspace and normalize its input for the review interface."""
import hashlib
import json
import re
from pathlib import Path

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
RESERVED = {"all", "constructor", "prototype", "toString", "valueOf", "hasOwnProperty",
            "isPrototypeOf", "propertyIsEnumerable", "toLocaleString"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or value in RESERVED:
        raise ValueError("Use a distinct ASCII identifier with letters, digits, - or _")
    return value


def words(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def load_project(root):
    paths = {}
    images = {}

    def file_bytes(name):
        file = (root / name).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError(f"Missing or external project input: {name}")
        raw = file.read_bytes()
        paths[name] = digest(raw)
        return raw

    config = json.loads(file_bytes("project.json"))
    project = words(config["project"], "project")
    tasks = [{"id": valid_id(t["id"]), "title": words(t["title"], "task title")} for t in config["tasks"]]
    issues = [{"id": valid_id(i["id"]), "comment": words(i["comment"], "review comment")} for i in config["issues"]]
    ids = [g["id"] for g in tasks + issues]
    if len(ids) != len(set(ids)) or not tasks:
        raise ValueError("Use unique group identifiers and at least one task")
    docs = {}
    for side in ("original", "current"):
        spec = config[side]
        source = spec["source"]
        lines = file_bytes(source).decode("utf-8-sig").splitlines()
        pages = []
        for number, page in enumerate(spec.get("pages", []), 1):
            name = page["file"]
            raw = file_bytes(name)
            if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
                raise ValueError(f"Expected PNG page: {name}")
            url = f"/pages/{side}/{number}.png"
            images[url] = raw
            pages.append(dict(number=number, image=url, width=int.from_bytes(raw[16:20], "big"),
                              height=int.from_bytes(raw[20:24], "big")))
        docs[side] = dict(source_lines=lines, pages=pages, source_label=source,
                          label="当前稿件" if side == "current" else "送审原稿")
    data = dict(project=project, tasks=tasks, issues=issues, items=[], documents={},
                pages=docs["original"]["pages"], source_lines=docs["original"]["source_lines"],
                source_label=docs["original"]["source_label"], current_document=docs["current"],
                tex_sha256=paths[config["original"]["source"]])
    return data, paths, docs, images


def location(edit, side, doc):
    key = "current_line" if side == "current" else "line"
    line = edit.get(key)
    if line is not None and (type(line) != int or not 1 <= line <= len(doc["source_lines"])):
        raise ValueError(f"{key} is outside the {side} source")
    key = "current_anchor" if side == "current" else "anchor"
    a = edit.get(key)
    if a is None:
        return line, None
    page, rect = a["page"], a["rect"]
    if type(page) != int or not 1 <= page <= len(doc["pages"]):
        raise ValueError(f"Unknown {side} page")
    p = doc["pages"][page - 1]
    if not isinstance(rect, list) or len(rect) != 4 or any(type(n) not in (int, float) for n in rect):
        raise ValueError("Expected four pixel coordinates")
    x, y, right, bottom = rect
    if not (0 <= x < right <= p["width"] and 0 <= y < bottom <= p["height"]):
        raise ValueError(f"Rectangle outside {side} page")
    return line, dict(page=page, rect=rect, line=line)


def proposal(raw, data, docs):
    item_id = valid_id(raw["id"])
    task_ids = {g["id"] for g in data["tasks"]}
    issue_ids = {g["id"] for g in data["issues"]}
    task, issues = raw["task"], raw.get("issues", [])
    if item_id in task_ids | issue_ids or task not in task_ids or not isinstance(issues, list) or not set(issues) <= issue_ids:
        raise ValueError("Item ID or group reference is invalid")
    kind = raw.get("kind", "change")
    if kind not in ("change", "reply", "question"):
        raise ValueError("kind must be change, reply or question")
    item = dict(id=item_id, task=task, issues=issues, title=words(raw["title"], "title"),
                body=words(raw["body"], "body"), kind=kind, source=item_id, mapping="源文对照",
                lines=[], current_lines=[], anchors=[], current_anchors=[], edits=[])
    item["lifecycle"] = raw.get("lifecycle", "active")
    item["review_deferred"] = raw.get("review_deferred", False)
    if item["lifecycle"] not in ("active", "withdrawn") or type(item["review_deferred"]) is not bool:
        raise ValueError("Invalid lifecycle or review_deferred")
    if item["lifecycle"] == "withdrawn":
        item["withdrawal_reason"] = words(raw.get("withdrawal_reason"), "withdrawal_reason")
    if not isinstance(raw.get("edits", []), list):
        raise ValueError("edits must be an array")
    for edit in raw.get("edits", []):
        before = words(edit["before"], "before")
        after = edit["after"]
        if not isinstance(after, str):
            raise ValueError("after must be text (empty means deletion)")
        original, anchor = location(edit, "original", docs["original"])
        current, current_anchor = location(edit, "current", docs["current"])
        if original:
            source_line = docs["original"]["source_lines"][original - 1]
            remaining = "\n".join(docs["original"]["source_lines"][original - 1:])
            offset = remaining.find(before)
            if offset < 0 or offset >= len(source_line):
                raise ValueError("The original line must locate the quoted before text")
        item["edits"].append(dict(before=before, after=after, line=original, current_line=current,
                                  anchor=anchor, current_anchor=current_anchor))
        for key, value in (("lines", original), ("current_lines", current), ("anchors", anchor), ("current_anchors", current_anchor)):
            if value is not None and value not in item[key]:
                item[key].append(value)
    return item
