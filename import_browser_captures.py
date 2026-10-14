"""Offline bridge from authorized DOM captures to the research input contract.

Usage: python import_browser_captures.py --db data/private/research.sqlite
Only reads local JSON; never opens a browser, logs in, or accesses the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from xhs_research.core import analyze, canonical, import_records, normalize_count, parse_time, validate_db

VERSION = "authorized-dom-bridge-v1"
SOURCE = "authorized_browser_dom"
SELF_MALE_TAGS = {"薄肌男孩", "反差系男子", "男妈妈", "要做一个有胸肌的男人"}


def gender_evidence(row, *, creator=False):
    values = row.get("gender_icon", []) if creator else [row.get("gender_evidence", "")]
    if isinstance(values, str):
        values = [values]
    observed = row.get("observed_at")
    result = []
    for text in values:
        for gender in ("male", "female"):
            if re.search(r"#" + gender + r"(?:\b|$)", str(text)):
                if not creator and row.get("gender") not in {gender, None}:
                    continue
                result.append({"value": gender, "basis": "public_profile", "evidence": {"source": SOURCE, "locator": "public_profile.gender_svg", "value": "#" + gender, "observed_at": observed}, "version": VERSION})
    return result


def age_evidence(row):
    # Only explicitly displayed age. Occupation, school, photo and nickname are unused.
    ages = re.findall(r"(?<!\d)(\d{1,3})\s*岁(?!\d)", str(row.get("tags_text") or ""))
    result = []
    for text in ages:
        age = int(text)
        if not 0 <= age <= 120:
            continue
        band = "minor" if age < 18 else "18–24" if age <= 24 else "25–34" if age <= 34 else "35–44" if age <= 44 else "45+"
        result.append({"value": band, "basis": "public_profile", "evidence": {"source": SOURCE, "locator": "public_profile.tags_text.explicit_age", "value": text + "岁", "observed_at": row.get("observed_at")}, "version": VERSION})
    return result


def _source_evidence(row, locator):
    return {"source": SOURCE, "capture_file": row["_capture_file"], "locator": locator, "observed_at": row["observed_at"]}


def _publication(row):
    raw = str(row.get("date_raw") or "")
    if "编辑" in raw:
        return None, "edit_time_raw_not_publication_time"
    match = re.search(r"(?<!\d)(\d{4}-\d{1,2}-\d{1,2})(?!\d)", raw)
    if match:
        try:
            date = datetime.strptime(match[1], "%Y-%m-%d")
            return date.isoformat() + "+08:00", "date_only"
        except ValueError:
            pass
    # Month/day lacks year and time: keep visible text, do not guess a publication timestamp.
    return None, "unknown"


def _count(raw):
    return normalize_count(raw=raw)


def _coverage(row):
    comments = {c["id"]: c for c in row.get("comments", []) if c.get("id")}
    count = _count(row.get("comments_raw"))
    header = re.search(r"共\s*([\d,]+)\s*条评论", str(row.get("comment_header") or ""))
    header_count = int(header[1].replace(",", "")) if header else None
    complete = (row.get("end_visible") is True and not row.get("expand_buttons") and count["precision"] == "exact" and count["value"] == len(comments) and header_count == count["value"])
    return {"status": "complete" if complete else "partial", "limit": len(comments), "stop_reason": "visible_end_and_exact_all_level_count_match" if complete else "visible_dom_pool_only_or_unexpanded_replies", "sort": row.get("comment_sort", "unspecified_platform_order"), "sampling_rule": "all_native_comment_ids_visible_in_captured_DOM", "comparison_key": "complete_native_all_levels_v1" if complete else "visible_DOM_pool_unspecified_sort_v1", "platform_count_basis": "platform_display_all_comments", "loaded_count_basis": "captured_native_comments_including_replies", "end_visible": row.get("end_visible") is True, "unexpanded_reply_buttons": len(row.get("expand_buttons") or []), "header_value": header_count}


def load_captures(paths):
    rows = []
    seen = set()
    for path in sorted(map(Path, paths)):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("records", [data])
        if not isinstance(data, list):
            raise ValueError(f"expected capture record array: {path.name}")
        for index, row in enumerate(data):
            if not isinstance(row, dict) or not row.get("kind"):
                raise ValueError(f"invalid capture record: {path.name}:{index}")
            digest = hashlib.sha256(canonical(row).encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            parse_time(row.get("observed_at"))
            rows.append({**row, "_capture_file": path.name, "_capture_index": index})
    return sorted(rows, key=lambda row: (parse_time(row["observed_at"]), row["_capture_file"], row["_capture_index"]))


def convert(rows, study_id="xhs-20261014-pilot", owner_id="local-research", screen_size=8):
    if not rows:
        raise ValueError("no capture records")
    notes_dom = [row for row in rows if row["kind"] == "note_dom"]
    creators_dom = defaultdict(list)
    profiles = defaultdict(list)
    annotations = defaultdict(list)
    for row in rows:
        if row["kind"] == "creator_dom":
            creators_dom[row["creator_id"]].append(row)
        elif row["kind"] == "audience_dom" and row.get("user_id"):
            profiles[row["user_id"]].append(row)
        elif row["kind"] == "visual_annotation":
            annotations[row["note_id"]].append(row)
    envelope = {"study_id": study_id, "owner_id": owner_id, "source_mode": "real", "observed_at": max((row["observed_at"] for row in rows), key=parse_time), "study": {"selection_policy": "high_comment_female_case_study", "minimum_platform_comments": 50, "sampling_version": VERSION, "sampling_rule": "initial_first_8_distinct_stable_external_first_level_users_then_additional_actually_reviewed_users_from_expanded_visible_DOM", "allowed_use": "authorized public-content descriptive case study", "threshold_status": "initial_not_pilot_calibrated"}, "searches": [], "creators": [], "notes": [], "comments": [], "outcomes": []}
    # Self-report tags are narrow and require the note to describe its own author.
    author_self_tags = defaultdict(list)
    for note in notes_dom:
        if note.get("author_self_report_reviewed") is not True:
            continue
        if "男朋友" in str(note.get("desc") or "") and ("跟着他" in str(note.get("desc") or "") or "他还没健身" in str(note.get("desc") or "")):
            continue
        for tag in note.get("tags", []):
            if str(tag).lstrip("#") in SELF_MALE_TAGS:
                author_self_tags[note["creator_id"]].append({"value": "male", "basis": "public_self_report", "evidence": {**_source_evidence(note, "note.platform_tags.author_self_report"), "source": "author_note_tag", "note_id": note["note_id"], "value": tag}, "version": VERSION})
    for creator_id, observations in creators_dom.items():
        for row in observations:
            fans_raw = next((s.get("raw") for s in row.get("stats", []) if str(s.get("label") or "").strip() == "粉丝"), None)
            gender = gender_evidence(row, creator=True) + [e for e in author_self_tags[creator_id] if parse_time(e["evidence"]["observed_at"]) <= parse_time(row["observed_at"])]
            envelope["creators"].append({"creator_id": creator_id, "name": row.get("name"), "url": f"https://www.xiaohongshu.com/user/profile/{creator_id}", "observed_at": row["observed_at"], "source": SOURCE, "fans": {**_count(fans_raw), "source": SOURCE, "evidence": _source_evidence(row, "creator_dom.stats[label=粉丝]")}, "gender": gender})
    skipped_comments = 0
    for row in notes_dom:
        note_id, creator_id = row["note_id"], row["creator_id"]
        source_stamp = parse_time(row["observed_at"])
        creator_rows = creators_dom.get(creator_id, [])
        previous = [c for c in creator_rows if parse_time(c["observed_at"]) <= source_stamp]
        # Completion time may be after the original note DOM read because its author
        # profile was captured next. Preserve both times and never backdate fans.
        creator_row = max(previous, key=lambda c: parse_time(c["observed_at"])) if previous else min(creator_rows, key=lambda c: parse_time(c["observed_at"])) if creator_rows else None
        collection_time = max([row["observed_at"]] + ([creator_row["observed_at"]] if creator_row else []), key=parse_time)
        if creator_row:
            self_tags_at_collection = [e for e in author_self_tags[creator_id] if parse_time(e["evidence"]["observed_at"]) <= parse_time(collection_time)]
            original = next(c for c in envelope["creators"] if c["creator_id"] == creator_id and c["observed_at"] == creator_row["observed_at"])
            combined_gender = gender_evidence(creator_row, creator=True) + self_tags_at_collection
            if combined_gender != original["gender"]:
                envelope["creators"].append({**original, "observed_at": collection_time, "source_original_snapshot_observed_at": creator_row["observed_at"], "gender": combined_gender})
        tags = list(row.get("tags") or [])
        text = row.get("desc")
        publication, precision = _publication(row)
        visual_labels = []
        for annotation in annotations[note_id]:
            for dimension in ("subject", "composition", "person_area_band", "face", "scene", "light"):
                if annotation.get(dimension):
                    index = annotation.get("media_index", 0)
                    visual_labels.append({"media_id": f"{note_id}:{index}", "role": "cover" if index == 0 else "later_image", "dimension": dimension, "value": annotation[dimension], "evidence": {**_source_evidence(annotation, f"visual_annotation.media_index={index}"), "description": annotation.get("evidence")}, "version": annotation.get("version", VERSION), "annotator": annotation.get("reviewer", "manual"), "status": "reviewed"})
        topics = [a["primary_theme"] for a in annotations[note_id] if a.get("primary_theme")]
        if not topics and any(any(token in tag for token in ("健身", "增肌", "减脂", "腹肌")) for tag in tags):
            topics = ["fitness"]
        relation = "partner" if text and "男朋友" in text and ("跟着他" in text or "他还没健身" in text) else "unknown"
        text_labels = [{"dimension": "author_subject_relation", "value": relation, "evidence": _source_evidence(row, "note.desc.explicit_partner_statement"), "version": VERSION}] if relation == "partner" else []
        envelope["notes"].append({"note_id": note_id, "creator_id": creator_id, "url": f"https://www.xiaohongshu.com/explore/{note_id}", "observed_at": collection_time, "source_note_observed_at": row["observed_at"], "source": SOURCE, "source_evidence": _source_evidence(row, "note_dom"), "title": row.get("title"), "text": text, "platform_tags": tags, "body_tags": re.findall(r"#[^#\s]+", str(text or "")), "topics": sorted(set(topics)), "topic_annotation_basis": "visual_annotation_or_explicit_tag_rule", "format": "video" if row.get("type") == "video" else "image" if row.get("type") == "normal" else "unknown", "published_at": publication, "published_at_precision": precision, "date_raw": row.get("date_raw"), "counts": {"comments": _count(row.get("comments_raw")), "likes": _count(row.get("likes_raw")), "collects": _count(row.get("collects_raw"))}, "coverage": _coverage(row), "media": [{"media_id": f"{note_id}:{media.get('index', i)}", "role": "cover" if i == 0 else "later_image", "type": "image", "url": media.get("url")} for i, media in enumerate(row.get("media") or [])], "visual_labels": visual_labels, "text_labels": text_labels})
        selected = []
        for c in row.get("comments", []):
            uid = c.get("user_id")
            if c.get("level") == 1 and c.get("is_author") is False and not c.get("parent_id") and uid and uid not in selected:
                selected.append(uid)
        selected = set(selected[:screen_size])
        for c in row.get("comments", []):
            if not c.get("id"):
                skipped_comments += 1
                continue
            uid = c.get("user_id")
            external_first_level = c.get("level") == 1 and c.get("is_author") is False and not c.get("parent_id")
            relevant = profiles.get(uid, []) if external_first_level else []
            # Use all explicitly reviewed evidence up to the latest supplied observation.
            # Keeping each evidence timestamp makes contradictions and later reads visible.
            reviewed = [p for p in relevant if p.get("reviewed") is True]
            sampled = bool(reviewed) and external_first_level
            checked_at = max([p["observed_at"] for p in relevant], key=parse_time) if relevant else None
            audience = {"gender": [], "age_band": [], "interests": [], "presented_style": [], "preferred_style": []}
            for profile in reviewed:
                audience["gender"].extend(gender_evidence(profile))
                audience["age_band"].extend(age_evidence(profile))
                allowed = {
                    "interests": {"public_self_report", "public_content_repeated", "single_topic"},
                    "presented_style": {"public_self_report", "public_presentation"},
                    "preferred_style": {"public_self_report"},
                }
                supplied = profile.get("audience_annotations") or {}
                for dimension, bases in allowed.items():
                    for label in supplied.get(dimension, []):
                        if isinstance(label, dict) and label.get("value") and label.get("basis") in bases and label.get("evidence"):
                            audience[dimension].append({**label, "version": label.get("version", VERSION)})
            sampling_rule = f"initial_first_{screen_size}_distinct_stable_external_first_level_in_captured_DOM" if uid in selected else "additional_actually_reviewed_external_first_level_from_loaded_pool" if sampled else "not_in_reviewed_profile_sample"
            envelope["comments"].append({"comment_id": c["id"], "note_id": note_id, "user_id": uid, "level": c.get("level"), "parent_id": c.get("parent_id"), "is_author": c.get("is_author"), "content": c.get("content"), "date_raw": c.get("date_raw"), "published_at": None, "observed_at": max([collection_time] + ([checked_at] if checked_at else []), key=parse_time), "source_comment_observed_at": row["observed_at"], "profile_sampled": sampled, "profile_selected": uid in selected or sampled, "initial_screen_selected": uid in selected, "profile_sampling_rule": sampling_rule, "profile_checked_at": checked_at, "profile_check_status": "reviewed" if reviewed else "unavailable" if relevant else "not_checked", "audience": audience, "intents": c.get("intents", []), "interaction_target": c.get("interaction_target", "unknown"), "intent_annotation_status": "unannotated" if not c.get("intents") else "supplied_annotation"})
    for row in rows:
        if row["kind"] in {"search_dom", "access_probe"}:
            identifier = hashlib.sha256(canonical({k: v for k, v in row.items() if not k.startswith("_")}).encode()).hexdigest()[:20]
            envelope["searches"].append({"search_id": identifier, "query": row.get("query"), "sort": row.get("sort"), "observed_at": row["observed_at"], "sampling_rule": "visible_search_candidates_then_exact_fans_and_public_gender_screen", "sampling_version": VERSION, "source": SOURCE, "status": row.get("status", "observed"), "failure_reason": row.get("failure_reason"), "candidate_count": row.get("candidate_count"), "candidates": row.get("candidates", []), "capture_file": row["_capture_file"]})
    audit = {"unique_capture_records": len(rows), "capture_kinds": dict(Counter(row["kind"] for row in rows)), "envelope_rows": {kind: len(envelope[kind]) for kind in ("searches", "creators", "notes", "comments")}, "skipped_comments_missing_native_id": skipped_comments, "profile_selection_rule": f"initial_first_{screen_size}_distinct_stable_external_first_level_then_additional_actually_reviewed_from_expanded_loaded_pool", "notes_subject_partner": sum(any(label.get("value") == "partner" for label in note["text_labels"]) for note in envelope["notes"]), "inferred_comment_intents": False}
    return envelope, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, default=Path("data/private/browser-captures"))
    parser.add_argument("--db", type=Path, default=Path("data/private/research.sqlite"))
    parser.add_argument("--study", default="xhs-20261014-pilot")
    parser.add_argument("--owner", default="local-research")
    parser.add_argument("--envelope-output", type=Path, default=Path("data/private/browser-envelope.json"))
    parser.add_argument("--analysis-output", type=Path, default=Path("data/private/browser-analysis.json"))
    args = parser.parse_args(argv)
    try:
        paths = sorted(args.captures.glob("capture-*.json")) if args.captures.is_dir() else [args.captures]
        envelope, audit = convert(load_captures(paths), args.study, args.owner)
        args.envelope_output.parent.mkdir(parents=True, exist_ok=True)
        args.envelope_output.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        imported = import_records(str(args.db), [envelope])
        analysis = analyze(str(args.db), args.study, "real")
        analysis["conversion_audit"] = audit
        args.analysis_output.parent.mkdir(parents=True, exist_ok=True)
        args.analysis_output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        quality = validate_db(str(args.db), args.study, "real")
        print(json.dumps({"import": imported, "audit": audit, "summary": analysis["summary"], "validation": quality, "outputs": {"envelope": str(args.envelope_output.resolve()), "analysis": str(args.analysis_output.resolve())}}, ensure_ascii=False, indent=2))
        return 0 if quality["valid"] else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
