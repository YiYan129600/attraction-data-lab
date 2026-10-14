"""Standard-library storage and descriptive analysis; no collection or inference."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
TABLES = ("searches", "creators", "notes", "comments", "outcomes")
ID_FIELDS = {"searches": "search_id", "creators": "creator_id", "notes": "note_id", "comments": "comment_id", "outcomes": "outcome_id"}
AGE_BANDS = ("18–24", "25–34", "35–44", "45+", "unknown")
BAD_INTENTS = {"advertisement", "irrelevant", "attack", "广告", "无关", "攻击", "广告/重复/无关"}
GOOD_INTENTS = {"person_appreciation", "shared_interest", "practical_question", "author_conversation", "contact_intent", "人物欣赏", "兴趣共鸣", "实用询问", "面向作者的交流", "明确表达联系意愿"}
FORBIDDEN_KEYS = {"password", "cookie", "cookies", "token", "access_token", "refresh_token", "authorization", "credentials", "raw_profile", "private_message_text", "phone", "email"}
SELECTION_POLICIES = {"matched_group_research", "high_comment_female_case_study"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("observed_at must be ISO 8601 with timezone")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO 8601 timestamp") from exc
    if date.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return date


def normalize_count(raw=None, value=None, precision=None):
    """Never promote an abbreviated display into an exact count."""
    parsed = None
    abbreviated = False
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        parsed = raw
    elif isinstance(raw, str):
        text = raw.strip().replace(",", "")
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([万wW千kK]?)", text)
        if match:
            abbreviated = bool(match[2])
            multiplier = 10000 if match[2] in {"万", "w", "W"} else 1000 if match[2] else 1
            parsed = float(match[1]) * multiplier
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError("count value must be a non-negative integer or null")
        if not abbreviated and parsed is not None and parsed != value:
            raise ValueError("exact count raw and value disagree")
        parsed = int(value)
    if parsed is not None and (not math.isfinite(parsed) or parsed < 0 or int(parsed) != parsed):
        raise ValueError("count must be a non-negative integer")
    result_precision = precision or ("rounded" if abbreviated else "exact" if parsed is not None else "unknown")
    if result_precision not in {"exact", "rounded", "unknown"}:
        raise ValueError("count precision must be exact|rounded|unknown")
    if abbreviated:
        result_precision = "rounded"
    if result_precision == "unknown":
        parsed = None
    return {"raw": raw, "value": int(parsed) if parsed is not None else None, "precision": result_precision}


def fans_band(count):
    value = count["value"]
    if count["precision"] != "exact" or value is None or value > 5000:
        return None
    return "0–500" if value <= 500 else "501–2000" if value <= 2000 else "2001–5000"


def _check_private(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise ValueError(f"unsupported sensitive field: {key}")
            _check_private(child)
    elif isinstance(value, list):
        for child in value:
            _check_private(child)


class _Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(db_path):
    db = sqlite3.connect(db_path, factory=_Connection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS studies (
            study_id TEXT NOT NULL, source_mode TEXT NOT NULL CHECK(source_mode IN ('real','synthetic')),
            owner_id TEXT NOT NULL, config TEXT NOT NULL,
            PRIMARY KEY(study_id, source_mode, owner_id));
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id INTEGER PRIMARY KEY, study_id TEXT NOT NULL, source_mode TEXT NOT NULL,
            owner_id TEXT NOT NULL, entity TEXT NOT NULL, record_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
            UNIQUE(study_id,source_mode,owner_id,entity,record_id,digest),
            FOREIGN KEY(study_id,source_mode,owner_id) REFERENCES studies(study_id,source_mode,owner_id));
        CREATE INDEX IF NOT EXISTS snapshot_lookup ON snapshots(study_id,source_mode,entity,record_id);
        """)
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version',?)", (str(SCHEMA_VERSION),))
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('hmac_key',?)", (secrets.token_hex(32),))
    return {"schema_version": SCHEMA_VERSION, "database": str(Path(db_path).resolve())}


def read_input(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _envelopes(items):
    """Each JSONL line may be an envelope or one typed contextual record."""
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("input must contain JSON objects")
        if "entity" in item:
            entity = item["entity"]
            if entity not in TABLES:
                raise ValueError("entity must be searches|creators|notes|comments|outcomes")
            envelope = {k: item.get(k) for k in ("study_id", "owner_id", "source_mode", "observed_at")}
            envelope[entity] = [item.get("record", {})]
            yield envelope
        else:
            yield item


def _identity(record, key, context):
    identity = record.pop("user_id", None)
    url = record.pop("user_profile_url", None)
    profile_id = None
    if url:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname not in {"xiaohongshu.com", "www.xiaohongshu.com"} or not re.fullmatch(r"/user/profile/[^/]+/?",parts.path):
            raise ValueError("user_profile_url must be a Xiaohongshu public profile URL")
        profile_id = parts.path.rstrip("/").rsplit("/",1)[1]
    if identity is not None:
        if isinstance(identity,bool) or not str(identity).strip():
            raise ValueError("user_id must be a nonempty stable platform ID")
        identity = str(identity).strip()
        if profile_id is not None and identity != profile_id:
            raise ValueError("user_id and profile URL identify different users")
    elif profile_id:
        identity = profile_id
    if identity:
        identity = "id:" + identity
    record.pop("user_name", None)
    record.pop("nickname", None)
    # Imported pseudonyms cannot impersonate an original stable platform identity.
    record.pop("user_key", None)
    record["user_key"] = hmac.new(bytes.fromhex(key), (context + "\0" + identity).encode(), hashlib.sha256).hexdigest() if identity else None
    record["identity_status"] = "stable" if identity else "unknown"


def _normal_record(entity, record, observed, key, context):
    record = json.loads(canonical(record))
    record_id = record.get(ID_FIELDS[entity])
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError(f"{entity} require nonempty {ID_FIELDS[entity]}")
    record["observed_at"] = record.get("observed_at") or observed
    record["observed_at"] = parse_time(record["observed_at"]).isoformat()
    if entity == "creators":
        fans = record.get("fans") or {}
        record["fans"] = {**{k: fans[k] for k in ("source", "evidence") if k in fans}, **normalize_count(fans.get("raw"), fans.get("value"), fans.get("precision"))}
        record["fans_band"] = fans_band(record["fans"])
        record["eligible_fans"] = record["fans_band"] is not None
    if entity == "notes":
        if not record.get("creator_id"):
            raise ValueError("notes require creator_id")
        coverage = record.setdefault("coverage", {})
        coverage.setdefault("status", "unknown")
        if coverage["status"] not in {"complete", "partial", "unknown"}:
            raise ValueError("coverage status must be complete|partial|unknown")
        for count_name, count in list(record.get("counts", {}).items()):
            record["counts"][count_name] = normalize_count(count.get("raw"), count.get("value"), count.get("precision"))
    if entity == "comments":
        if not record.get("note_id"):
            raise ValueError("comments require note_id")
        if record.get("level") not in {1, 2, None}:
            raise ValueError("comment level must be 1|2|null")
        if record.get("level") == 1 and record.get("parent_id"):
            raise ValueError("first-level comment cannot have parent_id")
        for flag in ("is_author", "profile_sampled"):
            if record.get(flag) not in {True, False, None}:
                raise ValueError(f"{flag} must be boolean|null")
        _identity(record, key, context)
        record.setdefault("profile_sampled", False)
        record.setdefault("intents", [])
        # Require human/platform evidence; inferred gender is never promoted.
        record.setdefault("audience", {})
    if entity == "outcomes":
        for field in ("production_minutes", "reads", "female_effective_commenters", "female_initiated_dms"):
            val = record.get(field)
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0):
                raise ValueError(f"{field} must be a nonnegative number|null")
        if record.get("attribution") not in {None, "explicit_note", "platform_source", "period_unattributed", "unknown"}:
            raise ValueError("invalid DM attribution")
    return record


def import_records(db_path, items):
    init_db(db_path)
    inserted, duplicates = Counter(), Counter()
    with connect(db_path) as db:
        key = db.execute("SELECT value FROM metadata WHERE key='hmac_key'").fetchone()[0]
        for envelope in _envelopes(items):
            _check_private(envelope)
            study, owner, mode = (envelope.get(k) for k in ("study_id", "owner_id", "source_mode"))
            if not all(isinstance(v, str) and v.strip() for v in (study, owner)):
                raise ValueError("study_id and owner_id are required")
            if mode not in {"real", "synthetic"}:
                raise ValueError("source_mode must explicitly be real|synthetic")
            observed = parse_time(envelope.get("observed_at")).isoformat()
            config = envelope.get("study", {})
            db.execute("INSERT OR IGNORE INTO studies VALUES (?,?,?,?)", (study, mode, owner, canonical(config)))
            if "study" in envelope:
                db.execute("UPDATE studies SET config=? WHERE study_id=? AND source_mode=? AND owner_id=?", (canonical(config),study,mode,owner))
            context = canonical([study, mode, owner])
            for entity in TABLES:
                rows = envelope.get(entity, [])
                if not isinstance(rows, list):
                    raise ValueError(f"{entity} must be a list")
                for row in rows:
                    record = _normal_record(entity, row, observed, key, context)
                    payload = canonical(record)
                    digest = hashlib.sha256(payload.encode()).hexdigest()
                    cur = db.execute("INSERT OR IGNORE INTO snapshots(study_id,source_mode,owner_id,entity,record_id,observed_at,payload,digest) VALUES (?,?,?,?,?,?,?,?)", (study, mode, owner, entity, record[ID_FIELDS[entity]], record["observed_at"], payload, digest))
                    (inserted if cur.rowcount else duplicates)[entity] += 1
    return {"inserted": dict(inserted), "duplicates": dict(duplicates)}


def _latest(db, study, mode, owner=None):
    sql = "SELECT * FROM snapshots WHERE study_id=? AND source_mode=?"
    args = [study, mode]
    if owner is not None:
        sql += " AND owner_id=?"
        args.append(owner)
    result = {name: {} for name in TABLES}
    times = {}
    for row in db.execute(sql, args):
        # Keep owner namespaces separate; outcomes must never leak by omission.
        identity = (row["owner_id"], row["record_id"])
        stamp = (parse_time(row["observed_at"]), row["snapshot_id"])
        k = (row["entity"], identity)
        if stamp > times.get(k, (datetime.min.replace(tzinfo=parse_time(row["observed_at"]).tzinfo), 0)):
            times[k] = stamp
            result[row["entity"]][identity] = json.loads(row["payload"])
    return result


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def wilson_lower(successes, total, z=1.959963984540054):
    if not total:
        return None
    p = successes / total
    return (p + z*z/(2*total) - z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))) / (1 + z*z/total)


def _disclosed(entries, allowed):
    values = {x.get("value") for x in entries if isinstance(x, dict) and x.get("value") in allowed and x.get("basis") in {"public_self_report", "public_profile", "authorized_aggregate"} and x.get("evidence")}
    return next(iter(values)) if len(values) == 1 else "unknown"


def _profile(comments):
    evidence = [c.get("audience", {}) for c in comments if c.get("profile_sampled")]
    gender = _disclosed([e for p in evidence for e in p.get("gender", [])], {"female", "male", "other"})
    age = _disclosed([e for p in evidence for e in p.get("age_band", [])], {*AGE_BANDS[:-1], "minor"})
    # Contradictory age reports stay unknown, but explicit minor evidence cannot disappear.
    minor = any(e.get("value") == "minor" and e.get("basis") in {"public_self_report", "public_profile", "authorized_aggregate"} and e.get("evidence") for p in evidence for e in p.get("age_band", []))
    labels = {}
    for dimension in ("interests", "presented_style", "preferred_style"):
        allowed = {"public_self_report", "public_content_repeated"} if dimension == "interests" else {"public_self_report", "public_presentation"} if dimension == "presented_style" else {"public_self_report"}
        labels[dimension] = sorted({e["value"] for p in evidence for e in p.get(dimension, []) if e.get("value") and e.get("evidence") and e.get("basis") in allowed})
    labels["topic_interests"] = sorted({e["value"] for p in evidence for e in p.get("interests", []) if e.get("value") and e.get("evidence") and e.get("basis") == "single_topic"})
    return {"gender": gender, "age_band": age, "minor": minor, **labels}


def _effective(comment):
    intents = set(comment.get("intents", []))
    return bool(intents & GOOD_INTENTS) and not bool(intents & BAD_INTENTS) and comment.get("interaction_target") != "other_commenter"


def _sample_metrics(users):
    n = len(users)
    k = sum(u["profile"]["gender"] != "unknown" for u in users)
    f = sum(u["profile"]["gender"] == "female" for u in users)
    effective = sum(u["profile"]["gender"] == "female" and u["effective"] for u in users)
    return {"N": n, "K": k, "F": f, "F_effective": effective, "F_over_K": ratio(f,k), "K_over_N": ratio(k,n), "F_over_N": ratio(f,n), "unknown_gender": n-k, "unknown_range": [f/n, (f+n-k)/n] if n else None, "wilson_95_lower": wilson_lower(f,k)}


def _note_metrics(comments, creator, coverage):
    stable = defaultdict(list)
    unknown_identity = 0
    author_replies = 0
    nested = 0
    for c in comments:
        if c.get("is_author") is True:
            author_replies += 1
        if c.get("level") == 2 or c.get("parent_id"):
            nested += 1
        if c.get("level") == 1 and not c.get("parent_id") and c.get("is_author") is False:
            if c.get("user_key"):
                stable[c["user_key"]].append(c)
            else:
                unknown_identity += 1
    sampled = [{"key": key, "profile": _profile(rows), "effective": any(_effective(c) for c in rows)} for key, rows in stable.items() if any(c.get("profile_sampled") for c in rows)]
    adults = [u for u in sampled if not u["profile"]["minor"]]
    metrics = _sample_metrics(sampled)
    metrics["adult_target"] = _sample_metrics(adults)
    metrics["adult_target"]["confirmed_adult_female"] = sum(u["profile"]["gender"] == "female" and u["profile"]["age_band"] in AGE_BANDS[:-1] for u in adults)
    metrics["adult_target"]["female_age_unknown"] = sum(u["profile"]["gender"] == "female" and u["profile"]["age_band"] == "unknown" for u in adults)
    metrics.update(U=len(stable), observed_comment_count=len(comments), author_reply_count=author_replies, nested_comment_count=nested, unknown_identity_first_level=unknown_identity, author_status_unknown=sum(c.get("is_author") is None for c in comments), level_unknown=sum(c.get("level") is None for c in comments), minor_excluded=len(sampled)-len(adults), whole_pool_profiled=bool(stable) and len(sampled)==len(stable) and unknown_identity==0)
    fans = creator.get("fans", {})
    denominator = fans.get("value") if fans.get("precision") == "exact" else None
    comparable = coverage.get("status") == "complete" and bool(coverage.get("comparison_key")) and unknown_identity == 0 and not metrics["author_status_unknown"] and not metrics["level_unknown"]
    metrics["pool_comparable"] = comparable
    metrics["U_per_1000_fans_observed"] = ratio(1000*len(stable), denominator)
    metrics["U_per_1000_fans_comparable"] = metrics["U_per_1000_fans_observed"] if comparable else None
    metrics["female_effective_per_1000_fans"] = ratio(1000*metrics["adult_target"]["F_effective"], denominator) if comparable and metrics["whole_pool_profiled"] else None
    return metrics, sampled


def _age_bucket(note):
    if not note.get("published_at"):
        return "unknown"
    try:
        days = (parse_time(note["observed_at"]) - parse_time(note["published_at"])).days
    except ValueError:
        return "unknown"
    return "0–7d" if 0 <= days <= 7 else "8–30d" if days <= 30 and days >= 0 else "31–90d" if days <= 90 and days >= 0 else "90d+" if days >= 0 else "unknown"


def _public_note(note, creator, metrics):
    allowed_content = ("title", "text", "platform_tags", "body_tags", "topics", "media", "published_at", "format", "production_resources", "text_labels")
    return {"note_id": note["note_id"], "url": note.get("url"), "observed_at": note["observed_at"], "creator": {k: creator.get(k) for k in ("creator_id", "name", "url", "fans", "fans_band", "eligible_fans", "observed_at")}, "creator_fans_source_recorded": bool(creator.get("source") or creator.get("fans", {}).get("source") or creator.get("fans", {}).get("evidence")), "creator_gender_status": _disclosed(creator.get("gender", []), {"male", "female", "other"}), "content": {k: note.get(k) for k in allowed_content}, "platform_counts": note.get("counts", {}), "coverage": note.get("coverage", {}), "visual_labels": note.get("visual_labels", []), "metrics": metrics, "comparison": {"topic": (note.get("topics") or ["unknown"])[0], "fans_band": creator.get("fans_band"), "age_bucket": _age_bucket(note), "format": note.get("format", "unknown")}, "main_sample": False}


def _screen_case(note, minimum_platform_comments):
    comments = note.get("platform_counts", {}).get("comments", {"raw": None, "value": None, "precision": "unknown"})
    sample = note["metrics"]["adult_target"]
    excluded, pending = [], []
    if comments.get("precision") != "exact" or comments.get("value") is None:
        pending.append("platform_comments_not_exact")
    elif comments["value"] < minimum_platform_comments:
        excluded.append("platform_comments_below_minimum")
    fans = note["creator"].get("fans") or {}
    if not note["creator"].get("eligible_fans"):
        if fans.get("precision") == "exact" and fans.get("value") is not None and fans["value"] > 5000:
            excluded.append("fans_above_5000")
        else:
            pending.append("fans_gate_unconfirmed")
    if note["creator_gender_status"] not in {"male", "unknown"}:
        excluded.append("creator_not_publicly_disclosed_male")
    elif note["creator_gender_status"] == "unknown":
        pending.append("creator_gender_unconfirmed")
    if sample["N"] < 8:
        pending.append("screen_sample_N_below_8")
    elif sample["K_over_N"] is None or sample["K_over_N"] < .6:
        pending.append("screen_gender_coverage_below_60pct")
    elif sample["F_over_K"] is None:
        pending.append("screen_gender_unknown")
    elif sample["F_over_K"] <= .5:
        excluded.append("screen_female_share_not_majority")
    return {"status": "excluded" if excluded else "pending" if pending else "qualified", "reasons": excluded + pending, "platform_comments": comments, "observed_screen_sample": {key: sample[key] for key in ("N", "K", "F", "K_over_N", "F_over_K")}, "thresholds": {"minimum_platform_comments": minimum_platform_comments, "minimum_profile_N": 8, "minimum_gender_coverage": .6, "minimum_F_over_K_exclusive": .5}, "evidence_status": "screening_only_not_final_majority_confirmation"}


def _references(notes, selection_policy="matched_group_research", minimum_platform_comments=50):
    groups = defaultdict(list)
    author_counts = Counter()
    case_study = selection_policy == "high_comment_female_case_study"
    if case_study:
        for note in notes:
            note["screening"] = _screen_case(note, minimum_platform_comments)
    # Deterministic selection makes the author cap inspectable and reproducible.
    for note in sorted(notes, key=lambda n: (n["observed_at"], n["note_id"])):
        author = note["creator"].get("creator_id")
        eligible_main = not case_study or note["screening"]["status"] == "qualified"
        note["main_sample"] = eligible_main and author_counts[author] < 3
        if eligible_main:
            author_counts[author] += 1
        c = note["comparison"]
        if note["main_sample"] and note["metrics"]["pool_comparable"] and note["creator"].get("eligible_fans") and c["age_bucket"] != "unknown" and c["topic"] != "unknown" and c["format"] != "unknown":
            group = (c["topic"],c["fans_band"],c["age_bucket"],c["format"], note["coverage"].get("comparison_key"))
            groups[group].append(note)
    comparison_groups = []
    for key, rows in groups.items():
        authors = len({n["creator"]["creator_id"] for n in rows})
        adequate = len(rows) >= 4 and authors >= 2
        values = [n["metrics"]["U"] for n in rows]
        norm = [n["metrics"]["U_per_1000_fans_comparable"] for n in rows if n["metrics"]["U_per_1000_fans_comparable"] is not None]
        # Empirical top quartile; equal-valued groups have no high-versus-normal evidence.
        for n in rows:
            rank = sum(x <= n["metrics"]["U"] for x in values)/len(rows)
            normalized_value = n["metrics"]["U_per_1000_fans_comparable"]
            normalized_adequate = len(norm) >= 4
            rank_norm = sum(x <= normalized_value for x in norm)/len(norm) if normalized_value is not None and normalized_adequate else None
            cutoff = sorted(values,reverse=True)[math.ceil(len(values)*.25)-1]
            cutoff_norm = sorted(norm,reverse=True)[math.ceil(len(norm)*.25)-1] if normalized_adequate else None
            high_normalized = normalized_value is not None and cutoff_norm is not None and normalized_value >= cutoff_norm and normalized_value > min(norm)
            high = adequate and ((n["metrics"]["U"] >= cutoff and n["metrics"]["U"] > min(values)) or high_normalized)
            n["comparison"].update(group_size=len(rows), creator_count=authors, sample_sufficient=adequate, U_percentile=rank if adequate else None, normalized_percentile=rank_norm if adequate else None, normalized_comparison_size=len(norm), high_interaction_supported=high)
        comparison_groups.append({"stratum": list(key), "notes": len(rows), "creators": authors, "sample_sufficient": adequate, "method": "empirical matched-group top quartile; minimum 4 notes/2 authors; all complete comparable pools"})
    for n in notes:
        reasons = []
        m = n["metrics"]; a=m["adult_target"]
        checks = [(n["creator"].get("eligible_fans"), "exact_fans_le_5000"), (n["creator_fans_source_recorded"], "fans_snapshot_source_recorded"), (n["creator_gender_status"] == "male", "creator_male_public_evidence"), (n["main_sample"], "maximum_3_main_notes_per_creator"), (m["pool_comparable"], "complete_comparable_comment_pool"), (n["comparison"].get("high_interaction_supported"), "matched_group_top_quartile"), (a["N"]>=30, "adult_target_N_ge_30"), (a["K"]>=20, "adult_target_K_ge_20"), (a["K_over_N"] is not None and a["K_over_N"]>=.6, "gender_coverage_ge_60pct"), (a["wilson_95_lower"] is not None and a["wilson_95_lower"]>.5, "female_Wilson_lower_gt_50pct"), (a["F_effective"]>=10, "female_effective_ge_10"), (bool(n["creator"].get("url")) and bool(n.get("url")), "public_source_links")]
        if case_study:
            checks = [(passed, label) for passed, label in checks if label != "matched_group_top_quartile"]
            checks.append((n["screening"]["status"] == "qualified", "high_comment_female_case_screen_qualified"))
        reasons = [label for passed,label in checks if not passed]
        status = "recommended" if not reasons else "structure_reference" if n["creator"].get("eligible_fans") and (n["content"].get("title") or n["content"].get("text") or n["content"].get("media")) else "pending"
        if case_study and n["screening"]["status"] != "qualified":
            status = "pending"
            reasons.append("screen_" + n["screening"]["status"])
        if case_study and any(not passed for passed, label in checks if label in {
            "creator_male_public_evidence", "adult_target_N_ge_30", "adult_target_K_ge_20",
            "gender_coverage_ge_60pct", "female_Wilson_lower_gt_50pct", "female_effective_ge_10"
        }):
            status = "pending"
        n["reference_case"] = {"status": status, "unmet_conditions": reasons, "reasons": [label for passed,label in checks if passed] + (["female_majority_case_study_no_causal_or_improvement_claim"] if case_study else []), "threshold_version": "initial-plan-section-4-v1" + ("-high-comment-case-study-v1" if case_study else ""), "threshold_status": "initial_not_pilot_calibrated", "design": selection_policy}
    return comparison_groups


def _audience_aggregate(users):
    female = [u for u in users.values() if u["profile"]["gender"] == "female" and not u["profile"]["minor"]]
    result = {"scope": "deduplicated sampled women, explicitly minor excluded; unknown age is not confirmed adult", "female_sample_count": len(female), "small_cell_minimum": 10, "age_band": {}, "interests": {}, "topic_interests": {}, "presented_style": {}, "preferred_style": {}, "multilabel": True}
    for dimension in ("age_band", "interests", "topic_interests", "presented_style", "preferred_style"):
        counts = Counter()
        unknown = 0
        for u in female:
            labels = [u["profile"][dimension]] if dimension == "age_band" else u["profile"][dimension]
            if not labels or labels == ["unknown"]:
                unknown += 1
            for label in labels:
                if label != "unknown":
                    counts[label] += 1
        result[dimension] = {"known": len(female)-unknown, "unknown": unknown, "groups": [{"label": label, "count": count if count>=10 else None, "sample_sufficient": count>=10, "status": "available" if count>=10 else "insufficient_sample"} for label,count in sorted(counts.items())]}
    return result


def analyze(db_path, study_id, source_mode="real", owner_id=None, selection_policy=None, minimum_platform_comments=None):
    if source_mode not in {"real", "synthetic"}:
        raise ValueError("source_mode must be real|synthetic")
    with connect(db_path) as db:
        exists = db.execute("SELECT count(*) FROM studies WHERE study_id=? AND source_mode=?", (study_id,source_mode)).fetchone()[0]
        if not exists:
            raise ValueError("study not found for requested source_mode")
        owners = [r[0] for r in db.execute("SELECT owner_id FROM studies WHERE study_id=? AND source_mode=?", (study_id,source_mode))]
        if owner_id is None and len(owners)>1:
            raise ValueError("study has multiple owner namespaces; --owner is required")
        if owner_id is not None and owner_id not in owners:
            raise ValueError("owner not found for requested study and source_mode")
        config_row = db.execute("SELECT config FROM studies WHERE study_id=? AND source_mode=? AND owner_id=?", (study_id,source_mode,owner_id or owners[0])).fetchone()
        study_config = json.loads(config_row[0])
        selection_policy = selection_policy or study_config.get("selection_policy", "matched_group_research")
        minimum_platform_comments = minimum_platform_comments if minimum_platform_comments is not None else study_config.get("minimum_platform_comments", 50)
        if selection_policy not in SELECTION_POLICIES:
            raise ValueError("selection_policy must be matched_group_research|high_comment_female_case_study")
        if isinstance(minimum_platform_comments, bool) or not isinstance(minimum_platform_comments, int) or minimum_platform_comments < 0:
            raise ValueError("minimum_platform_comments must be a nonnegative integer")
        records = _latest(db, study_id, source_mode, owner_id)
        snapshot_counts = dict(db.execute("SELECT entity,count(*) FROM snapshots WHERE study_id=? AND source_mode=?" + (" AND owner_id=?" if owner_id else "") + " GROUP BY entity", [study_id,source_mode]+([owner_id] if owner_id else [])).fetchall())
    comment_groups = defaultdict(list)
    for (owner,_),comment in records["comments"].items():
        comment_groups[(owner,comment["note_id"])].append(comment)
    notes=[]; global_users=defaultdict(list)
    for (owner,note_id),note in records["notes"].items():
        # Use the collection-time snapshot, never a later follower count.
        with connect(db_path) as db:
            candidates = [json.loads(r[0]) for r in db.execute("SELECT payload FROM snapshots WHERE study_id=? AND source_mode=? AND owner_id=? AND entity='creators' AND record_id=? ORDER BY snapshot_id DESC", (study_id,source_mode,owner,note["creator_id"]))]
        candidates = [c for c in candidates if parse_time(c["observed_at"]) <= parse_time(note["observed_at"])]
        creator = max(candidates,key=lambda c:parse_time(c["observed_at"])) if candidates else {"creator_id":note["creator_id"]}
        metrics,sampled = _note_metrics(comment_groups[(owner,note_id)],creator,note.get("coverage",{}))
        public_note = _public_note(note,creator,metrics)
        notes.append(public_note)
        if selection_policy == "high_comment_female_case_study":
            public_note["screening"] = _screen_case(public_note, minimum_platform_comments)
            if public_note["screening"]["status"] != "qualified":
                continue
        for user in sampled:
            global_users[user["key"]].extend([c for c in comment_groups[(owner,note_id)] if c.get("user_key")==user["key"] and c.get("profile_sampled")])
    merged = {key:{"profile":_profile(comments)} for key,comments in global_users.items()}
    groups=_references(notes, selection_policy, minimum_platform_comments)
    result = {"schema_version":SCHEMA_VERSION, "study_id":study_id, "source_mode":source_mode, "status":"observed_descriptive" if notes else "no_data", "notes":notes, "summary":{"notes":len(notes),"creators":len({n["creator"]["creator_id"] for n in notes}),"main_sample_notes":sum(n["main_sample"] for n in notes),"reference_cases":dict(Counter(n["reference_case"]["status"] for n in notes)),"cross_note_sampled_users":len(merged)},"comparison_groups":groups,"data_quality":{"snapshots":snapshot_counts,"current_records":{entity:len(rows) for entity,rows in records.items() if entity!="outcomes"},"source_mode_isolated":True,"comments_coverage":dict(Counter(n["coverage"].get("status","unknown") for n in notes)),"profile_N_sum":sum(n["metrics"]["N"] for n in notes),"pool_U_sum":sum(n["metrics"]["U"] for n in notes),"searches":[{k:search.get(k) for k in ("search_id","query","sort","observed_at","sampling_rule","sampling_version","status","failure_reason","source")} for search in records["searches"].values()]},"audience_aggregates":_audience_aggregate(merged),"limitations":["Observed comment pool is not all readers or all platform users.","Profile disclosure and comment sorting bias remain; Wilson interval does not remove these biases.","Current fan snapshot is not historical follower count at publication.","Initial thresholds require pilot calibration before confirmatory research."]}
    if owner_id is not None:
        result["owner_outcomes"] = outcome_analysis(list(records["outcomes"].values()))
        result["owner_id"] = owner_id
    result["selection_policy"] = selection_policy
    result["minimum_platform_comments"] = minimum_platform_comments
    if selection_policy == "high_comment_female_case_study":
        result["audience_aggregates"]["scope"] = "deduplicated sampled women from screen-qualified, explicit-male, exact-fans-eligible notes only; explicitly minor excluded; unknown age is not confirmed adult"
        for status in ("qualified", "excluded", "pending"):
            result["summary"]["screen_" + status + "_count"] = sum(n["screening"]["status"] == status for n in notes)
        result["limitations"].extend(["This is a purposively selected high-comment female-majority case study; low-comment and screened low-female-share posts are excluded from deep case analysis.", "An 8-user screen only prioritizes further investigation; it does not establish final female majority.", "Without low-performance controls this design cannot establish causes, improvements, comparative effectiveness, or platform-wide female preferences."])
    return result


def outcome_analysis(outcomes):
    rows=[]
    for outcome in outcomes:
        minutes=outcome.get("production_minutes")
        hours=minutes/60 if minutes is not None else None
        f=outcome.get("female_effective_commenters")
        d=outcome.get("female_initiated_dms")
        attributable=outcome.get("attribution") in {"explicit_note","platform_source"}
        dm_eligible=outcome.get("dm_initiation_rule") == "new_female_initiated_not_previously_contacted" and outcome.get("gender_evidence_basis") == "public_or_authorized_explicit" and outcome.get("minor_excluded") is True
        female_eligible=outcome.get("gender_evidence_basis") == "public_or_authorized_explicit" and outcome.get("minor_excluded") is True
        eligible_d=d if dm_eligible else None
        eligible_f=f if female_eligible else None
        reads=outcome.get("reads") if outcome.get("reads_basis") and outcome.get("reads_source") else None
        rows.append({k:outcome.get(k) for k in ("outcome_id","note_id","window","observed_at","production_minutes","reads","reads_basis","reads_source","female_effective_commenters","female_initiated_dms","attribution","strategy_id")}|{"female_commenters_per_hour":ratio(eligible_f,hours) if eligible_f is not None else None,"female_dms_per_hour":ratio(eligible_d,hours) if eligible_d is not None else None,"female_comments_per_1000_reads":ratio(1000*eligible_f,reads) if eligible_f is not None else None,"attributed_female_dms_per_1000_reads":ratio(1000*eligible_d,reads) if eligible_d is not None and attributable else None,"female_target_evidence_eligible":female_eligible,"dm_evidence_eligible":dm_eligible,"status":"observed" if female_eligible and f is not None and hours else "unverified"})
    return {"status":"observed_private" if rows else "unverified_no_data","records":rows,"comparison_basis":"same owner, window, read definition and strategy; no automatic causal claim"}


def select_candidates(db_path,study_id,source_mode="real",limit=24,seed="initial-v1",owner_id=None):
    report=analyze(db_path,study_id,source_mode,owner_id)
    strata=defaultdict(list)
    for note in report["notes"]:
        if report["selection_policy"] == "high_comment_female_case_study" and note.get("screening", {}).get("status") != "qualified":
            continue
        if note["creator"].get("eligible_fans"):
            c=note["comparison"]
            key=(c["topic"],c["fans_band"],c["age_bucket"],c["format"])
            strata[key].append(note)
    for rows in strata.values():
        rows.sort(key=lambda note:hashlib.sha256((seed+note["note_id"]).encode()).hexdigest())
    selected=[]; author_counts=Counter()
    while len(selected)<limit and any(strata.values()):
        for stratum in sorted(strata, key=str):
            while strata[stratum]:
                note=strata[stratum].pop(0)
                author=note["creator"]["creator_id"]
                if author_counts[author]>=3:
                    continue
                selected.append({"note_id":note["note_id"],"creator_id":author,"stratum":list(stratum),"url":note.get("url")})
                author_counts[author]+=1
                break
            if len(selected)>=limit:
                break
    return {"study_id":study_id,"source_mode":source_mode,"seed":seed,"requested":limit,"selected":selected,"rule":"stratified deterministic hash order; round robin strata; maximum 3 notes per creator","status":"sufficient" if len(selected)==limit else "candidate_shortfall"}


def sample_users(db_path,study_id,note_id,source_mode="real",limit=30,seed="profile-v1",owner_id=None):
    with connect(db_path) as db:
        owners = [r[0] for r in db.execute("SELECT owner_id FROM studies WHERE study_id=? AND source_mode=?", (study_id,source_mode))]
        if owner_id is None and len(owners)>1:
            raise ValueError("study has multiple owner namespaces; --owner is required")
        if not owners or (owner_id is not None and owner_id not in owners):
            raise ValueError("study not found for requested source_mode and owner")
        records=_latest(db,study_id,source_mode,owner_id)
    users=set()
    for c in records["comments"].values():
        if c.get("note_id")==note_id and c.get("level")==1 and c.get("is_author") is False and not c.get("parent_id") and c.get("user_key"):
            users.add(c["user_key"])
    selected=sorted(users,key=lambda key:hashlib.sha256((seed+key).encode()).hexdigest())[:limit]
    # This private local worksheet contains pseudonyms only; never a public audience list.
    return {"study_id":study_id,"source_mode":source_mode,"note_id":note_id,"seed":seed,"U":len(users),"requested":limit,"selected_user_keys":selected,"rule":"deterministic hash selection from loaded stable external first-level pool; not random sampling of the whole post","private_local_only":True,"mutates_profile_sampled":False}


def validate_db(db_path,study_id=None,source_mode="real"):
    issues=[]
    with connect(db_path) as db:
        integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            issues.append({"type":"sqlite_integrity","detail":integrity})
        studies=db.execute("SELECT study_id,source_mode,owner_id FROM studies" + (" WHERE study_id=? AND source_mode=?" if study_id else ""), [study_id,source_mode] if study_id else []).fetchall()
        for study,mode,owner in studies:
            records=_latest(db,study,mode,owner)
            for (_,nid),note in records["notes"].items():
                if (owner,note["creator_id"]) not in records["creators"]:
                    issues.append({"type":"missing_creator","note_id":nid})
                for label in note.get("visual_labels",[]):
                    if not label.get("evidence") or not label.get("version"):
                        issues.append({"type":"incomplete_visual_evidence","note_id":nid})
            for (_,cid),comment in records["comments"].items():
                if (owner,comment["note_id"]) not in records["notes"]:
                    issues.append({"type":"missing_note","comment_id":cid})
                if comment.get("parent_id") and (owner,comment["parent_id"]) not in records["comments"]:
                    issues.append({"type":"unloaded_parent","comment_id":cid,"severity":"warning"})
    return {"valid":not any(i.get("severity")!="warning" for i in issues),"sqlite_integrity":integrity,"issues":issues}
