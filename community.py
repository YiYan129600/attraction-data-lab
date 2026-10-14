"""Local-only allowlisted aggregate interchange. No network or database uploads."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 10000
MAX_COUNT = 2**53 - 1
POLICIES = {"matched_group_research", "high_comment_female_case_study"}
TOPICS = {"body_display", "fitness", "outdoor", "daily", "car", "hobby", "other", "unknown"}
SUBJECTS = {"person", "person_activity", "object", "landscape", "mixed", "unknown"}
COMPOSITIONS = {"close_up", "medium_shot", "wide_shot", "full_body", "collage", "unknown"}
ROOT_KEYS = {"schema_version", "dataset_id", "source_mode", "selection_policy", "records", "checksum"}
RECORD_KEYS = {"source_url", "observed_at", "fans", "platform_comment_count", "platform_comment_precision", "captured_count", "N", "K", "F", "coverage", "gender_evidence_method"}
OPTIONAL_KEYS = {"subject", "composition", "topic"}
URL_RE = re.compile(r"https://www\.xiaohongshu\.com/explore/[0-9a-f]{1,64}\Z")
TIME_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)\Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checksum(package):
    return hashlib.sha256(canonical({k: v for k, v in package.items() if k != "checksum"}).encode("utf-8")).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("input exceeds 16 MiB")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON document") from exc


def _keys(value, required, optional=frozenset()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("object has missing or unsupported fields")


def _enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("unsupported enum value")


def _count(value):
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_COUNT):
        raise ValueError("count must be null or bounded nonnegative integer")


def validate(package):
    _keys(package, ROOT_KEYS)
    if type(package["schema_version"]) is not int or package["schema_version"] != 1:
        raise ValueError("unsupported schema version")
    if not isinstance(package["dataset_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", package["dataset_id"]):
        raise ValueError("dataset_id must be 1-128 ASCII identifier characters")
    _enum(package["source_mode"], {"real", "synthetic"})
    _enum(package["selection_policy"], POLICIES)
    records = package["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("records must be a list of at most 10000 observations")
    identities = set()
    for record in records:
        _keys(record, RECORD_KEYS, OPTIONAL_KEYS)
        url = record["source_url"]
        if not isinstance(url, str) or not URL_RE.fullmatch(url):
            raise ValueError("source_url must be canonical public explore URL")
        time = record["observed_at"]
        if not isinstance(time, str) or not TIME_RE.fullmatch(time):
            raise ValueError("observed_at must be ISO 8601 with timezone")
        date = datetime.fromisoformat(time.replace("Z", "+00:00"))
        identity = (url, date.timestamp())
        if identity in identities:
            raise ValueError("duplicate observation in dataset")
        identities.add(identity)
        _keys(record["fans"], {"value", "precision"})
        _count(record["fans"]["value"])
        _enum(record["fans"]["precision"], {"exact", "rounded", "unknown"})
        if (record["fans"]["value"] is None) != (record["fans"]["precision"] == "unknown"):
            raise ValueError("fans value/precision disagree")
        for field in ("platform_comment_count", "captured_count", "N", "K", "F"):
            _count(record[field])
        _enum(record["platform_comment_precision"], {"exact", "rounded", "unknown"})
        if (record["platform_comment_count"] is None) != (record["platform_comment_precision"] == "unknown"):
            raise ValueError("platform comment count/precision disagree")
        n, k, f = (record[x] for x in ("N", "K", "F"))
        if any(v is None for v in (n, k, f)) and not all(v is None for v in (n, k, f)):
            raise ValueError("N/K/F must be all known or all null")
        if n is not None and not f <= k <= n:
            raise ValueError("require F <= K <= N")
        if n is not None and record["captured_count"] is not None and n > record["captured_count"]:
            raise ValueError("profile sample cannot exceed captured comment count")
        _enum(record["coverage"], {"complete", "partial", "unknown"})
        _enum(record["gender_evidence_method"], {"explicit_public_profile_or_self_report"})
        for field, choices in (("topic", TOPICS), ("subject", SUBJECTS), ("composition", COMPOSITIONS)):
            if field in record:
                _enum(record[field], choices)
    digest = package["checksum"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != checksum(package):
        raise ValueError("checksum mismatch")
    if len(canonical(package).encode("utf-8")) > MAX_BYTES:
        raise ValueError("package exceeds 16 MiB")
    return package


def _source_url(value):
    if not isinstance(value, str):
        raise ValueError("note needs a public source URL")
    parts = urlsplit(value)
    match = re.fullmatch(r"/explore/([0-9a-fA-F]{1,64})/?", parts.path)
    if parts.scheme not in {"https", "http"} or parts.hostname not in {"xiaohongshu.com", "www.xiaohongshu.com"} or parts.username or parts.password or parts.port or not match:
        raise ValueError("invalid source URL")
    return "https://www.xiaohongshu.com/explore/" + match[1].lower()


def _count_snapshot(value):
    if not isinstance(value, dict) or value.get("value") is None:
        return {"value": None, "precision": "unknown"}
    return {"value": value["value"], "precision": value.get("precision", "unknown")}


def _label(note, dimension, choices, mapping):
    labels = note.get("visual_labels") or []
    found = {mapping.get(item.get("value"), item.get("value")) for item in labels
             if isinstance(item, dict) and item.get("dimension") == dimension
             and item.get("status") == "reviewed" and item.get("role") == "cover"}
    known = found & (choices - {"unknown"})
    return next(iter(known)) if len(known) == 1 else "unknown"


def _topic(note):
    mapping = {"身材展示": "body_display", "健身运动": "fitness", "健身": "fitness", "fitness_progress": "fitness", "户外": "outdoor", "户外运动": "outdoor", "生活日常": "daily", "日常": "daily", "汽车": "car", "兴趣爱好": "hobby"}
    found = {mapping.get(item, item) for item in (note.get("topics") or (note.get("content") or {}).get("topics") or []) if isinstance(item, str)}
    known = found & (TOPICS - {"unknown"})
    return next(iter(known)) if len(known) == 1 else "unknown"


def export_analysis(analysis, dataset_id):
    if not isinstance(analysis, dict) or not isinstance(analysis.get("notes"), list):
        raise ValueError("analysis must contain notes list")
    if len(analysis["notes"]) > MAX_RECORDS:
        raise ValueError("too many notes")
    records = []
    for note in analysis["notes"]:
        metrics = note.get("metrics") or {}
        sample = metrics.get("adult_target") or {}
        counts = _count_snapshot((note.get("platform_counts") or {}).get("comments"))
        record = {"source_url": _source_url(note.get("url")), "observed_at": note.get("observed_at"),
                  "fans": _count_snapshot((note.get("creator") or {}).get("fans")),
                  "platform_comment_count": counts["value"], "platform_comment_precision": counts["precision"],
                  "captured_count": metrics.get("observed_comment_count"),
                  **{field: sample.get(field) for field in ("N", "K", "F")},
                  "coverage": (note.get("coverage") or {}).get("status", "unknown"),
                  "gender_evidence_method": "explicit_public_profile_or_self_report", "topic": _topic(note),
                  "subject": _label(note, "subject", SUBJECTS, {"人物": "person", "人物+活动": "person_activity", "物品": "object", "风景": "landscape", "混合": "mixed"}),
                  "composition": _label(note, "composition", COMPOSITIONS, {"特写": "close_up", "近景": "close_up", "中景": "medium_shot", "远景": "wide_shot", "全身": "full_body", "拼图": "collage"})}
        records.append(record)
    records.sort(key=lambda r: (r["source_url"], r["observed_at"] or ""))
    package = {"schema_version": 1, "dataset_id": dataset_id, "source_mode": analysis.get("source_mode"),
               "selection_policy": analysis.get("selection_policy"), "records": records}
    package["checksum"] = checksum(package)
    return validate(package)


def classify(record):
    n, k, f = (record[field] for field in ("N", "K", "F"))
    if n is None or n < 8 or k / n < .6:
        return "unknown"
    return "positive" if f / k > .5 else "counterexample"


def render_html(packages):
    escape = lambda value: html.escape(str(value), quote=True)
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>社区聚合研究</title><style>body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}td,th{padding:.5rem;border:1px solid #bbb}</style><h1>社区聚合研究</h1><p>公开来源链接可识别发布者，本数据不属于匿名数据。N/K/F 仅为每条帖子抽样，跨帖子及跨研究不能累加为独立用户数。未知年龄不等于成年。</p><p>positive / counterexample / unknown 是描述性初筛：N≥8 且 K/N≥60% 才比较 F/K&gt;50%；不代表平台总体偏好或因果结论。低评论、反例和未知均保留。</p>']
    for mode in ("real", "synthetic"):
        parts.append("<h2>" + mode + "</h2>")
        for package in packages:
            if package["source_mode"] != mode:
                continue
            parts.append("<h3>Study: " + escape(package["dataset_id"]) + "</h3><p>" + escape(package["selection_policy"]) + "</p><table><tr>" + "".join("<th>"+escape(x)+"</th>" for x in ("source", "observed_at", "topic", "fans / precision", "platform / precision", "captured", "N", "K", "F", "coverage", "screen")) + "</tr>")
            for record in package["records"]:
                values = (record["observed_at"], record.get("topic", "unknown"), str(record["fans"]["value"]) + " / " + record["fans"]["precision"], str(record["platform_comment_count"]) + " / " + record["platform_comment_precision"], record["captured_count"], record["N"], record["K"], record["F"], record["coverage"], classify(record))
                parts.append('<tr><td><a rel="noreferrer" href="'+escape(record["source_url"])+'">'+escape(record["source_url"])+"</a></td>" + "".join("<td>" + escape(v if v is not None else "unknown") + "</td>" for v in values) + "</tr>")
            parts.append("</table>")
    return "".join(parts) + "</html>"


def _atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".community-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def merge(paths, db_path, html_path):
    if not paths or len(paths) > 256:
        raise ValueError("merge needs 1-256 JSON package paths")
    outputs = {Path(db_path).resolve(), Path(html_path).resolve()}
    if len(outputs) != 2 or outputs & {Path(p).resolve() for p in paths}:
        raise ValueError("outputs must differ from each other and inputs")
    packages = {}
    total_bytes = 0
    for path in paths:
        total_bytes += Path(path).stat().st_size
        if total_bytes > 64 * 1024 * 1024:
            raise ValueError("merge inputs exceed 64 MiB")
        package = validate(read_json(path))
        identifier = package["dataset_id"]
        if identifier in packages and packages[identifier]["checksum"] != package["checksum"]:
            raise ValueError("dataset_id content conflict")
        packages[identifier] = package
    ordered = [packages[key] for key in sorted(packages)]
    page = render_html(ordered)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=db_path.parent, prefix=".community-", suffix=".sqlite")
    os.close(fd)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY, source_mode TEXT NOT NULL, selection_policy TEXT NOT NULL, checksum TEXT NOT NULL)")
            db.execute("CREATE TABLE observations(dataset_id TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(dataset_id, source_url, observed_at), FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id))")
            for package in ordered:
                db.execute("INSERT INTO datasets VALUES(?,?,?,?)", tuple(package[k] for k in ("dataset_id", "source_mode", "selection_policy", "checksum")))
                db.executemany("INSERT INTO observations VALUES(?,?,?,?)", [(package["dataset_id"], r["source_url"], r["observed_at"], canonical(r)) for r in package["records"]])
        os.replace(temporary, db_path)
        _atomic_text(html_path, page)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"datasets": len(ordered), "observations": sum(len(p["records"]) for p in ordered)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--analysis", required=True)
    export.add_argument("--dataset", required=True)
    export.add_argument("--output", required=True)
    validation = commands.add_parser("validate")
    validation.add_argument("paths", nargs="+")
    merging = commands.add_parser("merge")
    merging.add_argument("paths", nargs="+")
    merging.add_argument("--db", required=True)
    merging.add_argument("--html", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            if Path(args.analysis).resolve() == Path(args.output).resolve():
                raise ValueError("output must differ from private analysis input")
            package = export_analysis(read_json(args.analysis), args.dataset)
            _atomic_text(args.output, json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            result = {"exported_records": len(package["records"])}
        elif args.command == "validate":
            for path in args.paths:
                validate(read_json(path))
            result = {"valid": True, "packages": len(args.paths)}
        else:
            result = merge(args.paths, args.db, args.html)
    except (ValueError, TypeError, AttributeError, OverflowError, OSError, sqlite3.Error):
        # Never echo arbitrary input values, JSON text, signed URLs or secret-bearing paths.
        parser.exit(2, "community: input validation or local output failed; no input contents disclosed\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    main()
