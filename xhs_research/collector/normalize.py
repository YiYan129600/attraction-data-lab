"""Explicit source normalization, not demographic inference."""
import copy
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from ..core import FORBIDDEN_KEYS, _disclosed, _note_metrics, normalize_count, parse_time

PRIVATE_KEYS = FORBIDDEN_KEYS | {"xsec_token", "xsectoken", "web_session", "session", "auth_token"}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower() not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        def url(match):
            parts = urlsplit(match[0])
            return urlunsplit((parts.scheme, parts.netloc.split("@")[-1], parts.path, "", ""))
        value = re.sub(r"https?://[^\s<>\"']+", url, value, flags=re.I)
        value = re.sub(r"(?i)(?:xsec_token|web_session|authorization|auth_token)\s*[=:]\s*[^\s,;]+", "[redacted]", value)
        value = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b", "[redacted]", value)
        return value
    return value


def envelope(config, stamp=None, **entities):
    return clean({"study_id": config.study_id, "owner_id": config.owner_id, "source_mode": config.source_mode,
                  "observed_at": stamp or now(), "study": {"selection_policy": "high_comment_female_case_study", "minimum_platform_comments": 50,
                  "sampling_version": "collector-v2-1", "sampling_rule": "frozen_first_8_then_up_to_30_distinct_external_root_users",
                  "allowed_use": "authorized descriptive research"}, **entities})


def gender(entries):
    return _disclosed(entries or [], {"male", "female", "other"})


def metrics(comments, selected=None):
    # Ephemeral stable identities only for the existing core's calculation;
    # persistence still uses import_records' scoped HMAC conversion.
    rows = [{**c, "user_key": c.get("user_id")} for c in comments if selected is None or c.get("user_id") in selected]
    return _note_metrics(rows, {}, {"status": "partial"})[0]["adult_target"]


def classify(sample):
    if sample["N"] < 8 or sample["K_over_N"] is None or sample["K_over_N"] < .6:
        return "unknown"
    return "positive" if sample["F_over_K"] > .5 else "counterexample"


def external_users(comments):
    return list(dict.fromkeys(c["user_id"] for c in comments if c.get("user_id") and c.get("level") == 1 and c.get("is_author") is False and not c.get("parent_id")))


def merge_comment(old, new):
    result = {**old, **new}
    same_user = not old or old.get("user_id") == new.get("user_id", old.get("user_id"))
    if old and same_user and new.get("profile_check_status") not in {"reviewed", "unavailable"}:
        for key in ("profile_sampled", "audience", "profile_checked_at", "profile_check_status"):
            if key in old:
                result[key] = old[key]
    elif not same_user:
        result.update(profile_sampled=False, audience={}, profile_checked_at=None, profile_check_status="identity_changed")
    if old and old.get("content") != new.get("content"):
        result["previous_annotation"] = {k: old.get(k) for k in ("intents", "interaction_target", "intent_annotation")}
        result.update(intents=[], interaction_target="unknown", intent_annotation_status="annotation_review_required")
    elif old:
        for key in ("intents", "interaction_target", "intent_annotation"):
            if key not in new or new.get("intent_annotation_status") not in {"reviewed", "supplied_annotation"}:
                if key in old:
                    result[key] = old[key]
    return clean(result)


def rest_profile(payload, user_id, stamp):
    """Bound xpzouying REST profile shape; integer gender is intentionally unused."""
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("userBasicInfo"), dict):
        raise ValueError("profile_schema")
    basic = data["userBasicInfo"]
    returned = basic.get("user_id") or data.get("user_id")
    if returned and returned != user_id:
        raise ValueError("profile_identity_mismatch")
    audience = {k: [] for k in ("gender", "age_band", "interests", "presented_style", "preferred_style")}
    # Only a literal disclosure string with an explicit field provenance is mapped.
    # The upstream integer default does not establish that a user disclosed it.
    disclosed = basic.get("gender_disclosure")
    if disclosed in {"male", "female", "other"} and basic.get("gender_disclosure_source") == "public_profile":
        audience["gender"] = [{"value": disclosed, "basis": "public_profile", "evidence": {"source": "xhs-rest-public-disclosure-extension", "locator": "data.userBasicInfo.gender_disclosure", "observed_at": stamp}}]
    age = basic.get("age_disclosure")
    if type(age) is int and 0 <= age <= 120 and basic.get("age_disclosure_source") == "public_profile":
        band = "minor" if age < 18 else "18–24" if age <= 24 else "25–34" if age <= 34 else "35–44" if age <= 44 else "45+"
        audience["age_band"] = [{"value": band, "basis": "public_profile", "evidence": {"source": "xhs-rest-public-disclosure-extension", "locator": "data.userBasicInfo.age_disclosure", "observed_at": stamp}}]
    interactions = data.get("interactions")
    if not isinstance(interactions, list):
        raise ValueError("profile_interactions_schema")
    matches = [i for i in interactions if isinstance(i, dict) and i.get("type") == "fans"]
    fans = normalize_count(raw=matches[0].get("count") if len(matches) == 1 else None)
    creator = {"creator_id": user_id, "observed_at": stamp, "url": f"https://www.xiaohongshu.com/user/profile/{user_id}",
               "fans": {**fans, "source": "xhs-rest", "evidence": {"locator": "data.interactions[type=fans]", "observed_at": stamp}}, "gender": audience["gender"], "source": "xhs-rest"}
    return clean({"user_id": user_id, "observed_at": stamp, "reviewed": True, "audience": audience, "creator": creator, "source": "xhs-rest"})


def rest_note(payload, note_id, stamp, theme="unknown"):
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("note_schema")
    # Official REST wraps FeedDetailResponse in data:{feed_id,data:{note,comments}}.
    if data.get("feed_id") != note_id or not isinstance(data.get("data"), dict):
        raise ValueError("note_schema")
    data = data["data"]
    card = data.get("note")
    if not isinstance(card, dict) or card.get("noteId") != note_id:
        raise ValueError("note_identity_or_schema")
    user = card.get("user")
    if not isinstance(user, dict) or not user.get("userId"):
        raise ValueError("note_creator_schema")
    author = user["userId"]
    interact = card.get("interactInfo")
    if not isinstance(interact, dict):
        raise ValueError("note_counts_schema")
    note = {"note_id": note_id, "creator_id": author, "observed_at": stamp, "source_note_observed_at": stamp,
            "url": f"https://www.xiaohongshu.com/explore/{note_id}", "title": card.get("title"), "text": card.get("desc"), "source": "xhs-rest",
            "topics": [theme] if theme != "unknown" else [], "topic_annotation_basis": "search_theme_not_visual", "format": "video" if card.get("type") == "video" else "image" if card.get("type") == "normal" else "unknown",
            "counts": {"comments": normalize_count(raw=interact.get("commentCount")), "likes": normalize_count(raw=interact.get("likedCount")), "collects": normalize_count(raw=interact.get("collectedCount"))},
            "platform_tags": [t["name"] for t in card.get("tagList", []) if isinstance(t, dict) and isinstance(t.get("name"), str)],
            "coverage": {"status": "partial", "sort": "unspecified_platform_order", "sampling_rule": "source_returned_comments", "stop_reason": "REST_does_not_prove_all_level_completeness", "comparison_key": "xhs_rest_partial_v1"}}
    container = data.get("comments")
    if not isinstance(container, dict) or not isinstance(container.get("list"), list):
        raise ValueError("comments_schema")
    comments = []
    def append(row, level, parent=None):
        if not isinstance(row, dict):
            raise ValueError("comment_schema")
        if not isinstance(row.get("id"), str) or not row["id"]:
            return
        if row.get("noteId") and row["noteId"] != note_id:
            raise ValueError("comment_note_identity_mismatch")
        info = row.get("userInfo") or {}
        uid = info.get("userId")
        comments.append({"comment_id": row["id"], "note_id": note_id, "user_id": uid, "observed_at": stamp, "source_comment_observed_at": stamp,
                         "source": "xhs-rest", "level": level, "parent_id": parent, "is_author": uid == author if uid else None,
                         "content": row.get("content"), "profile_sampled": False, "audience": {}, "intents": [], "interaction_target": "unknown"})
        for child in row.get("subComments") or []:
            append(child, 2, row["id"])
    for row in container["list"]:
        append(row, 1)
    note["coverage"]["limit"] = len({c["comment_id"] for c in comments})
    return clean({"note": note, "comments": comments})
