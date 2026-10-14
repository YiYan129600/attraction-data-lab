"""Offline public report exporter. Input is xhs_research analyze JSON, never a database."""
import argparse
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ASSETS = Path(__file__).parent / "report_assets"


def safe_url(value):
    """Only public XHS source URLs; remove query credentials, tokens and fragments."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            return ""
        if host not in {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"}:
            return ""
        return urlunsplit(("https", host, parts.path, "", ""))
    except ValueError:
        return ""


def text(value):
    return value if isinstance(value, str) else ""


def texts(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def numeric_fields(value, keys):
    value = value if isinstance(value, dict) else {}
    return {key: number(value.get(key)) for key in keys}


def _sample(value):
    result = numeric_fields(value, ("N", "K", "F", "F_effective", "confirmed_adult_female", "female_age_unknown"))
    n, k, f = result["N"], result["K"], result["F"]
    valid = all(x is not None for x in (n, k, f)) and f <= k <= n
    result.update(F_over_K=f/k if valid and k else None, K_over_N=k/n if valid and n else None,
                  F_over_N=f/n if valid and n else None,
                  unknown_range=[f/n, (f+n-k)/n] if valid and n else None)
    return result


def public_data(analysis):
    """Explicit allowlist: no comments, audience evidence, owner outcomes or media."""
    notes = []
    focused = analysis.get("selection_policy") == "high_comment_female_case_study"
    screen_counts = {"qualified": 0, "excluded": 0, "pending": 0}
    for source in analysis.get("notes", []):
        if not isinstance(source, dict):
            continue
        screening = source.get("screening") or {}
        if focused:
            screen_status = screening.get("status") if screening.get("status") in screen_counts else "pending"
            screen_counts[screen_status] += 1
            if screen_status == "excluded":
                continue
        creator = source.get("creator") or {}
        content = source.get("content") or {}
        metrics = source.get("metrics") or {}
        coverage = source.get("coverage") or {}
        comparison = source.get("comparison") or {}
        ref = source.get("reference_case") or {}
        fans = creator.get("fans") or {}
        labels = []
        for label in source.get("visual_labels") or []:
            if isinstance(label, dict):
                labels.append({**{key: text(label.get(key)) for key in ("media_id", "role", "dimension", "value", "evidence", "version")}, "review_status": text(label.get("status")) or text(label.get("review_status"))})
        exact_fans = number(fans.get("value")) if fans.get("precision") == "exact" else None
        clean_metrics = _sample(metrics)
        clean_metrics.update(numeric_fields(metrics, ("U", "observed_comment_count", "author_reply_count", "nested_comment_count", "unknown_identity_first_level", "minor_excluded")))
        clean_metrics["adult_target"] = _sample(metrics.get("adult_target") or {})
        clean_metrics["pool_comparable"] = metrics.get("pool_comparable") is True
        clean_metrics["U_per_1000_fans_observed"] = 1000*clean_metrics["U"]/exact_fans if exact_fans and clean_metrics["U"] is not None else None
        clean_metrics["U_per_1000_fans_comparable"] = clean_metrics["U_per_1000_fans_observed"] if clean_metrics["pool_comparable"] else None
        status = text(ref.get("status")) or "pending"
        if status in {"recommended", "推荐参考学习"} and not (exact_fans is not None and exact_fans <= 5000 and creator.get("eligible_fans") is True):
            status = "pending"
        notes.append({"note_id": text(source.get("note_id")), "url": safe_url(source.get("url")), "observed_at": text(source.get("observed_at")),
                      "creator": {"name": text(creator.get("name")), "url": safe_url(creator.get("url")), "fans": {"raw": text(fans.get("raw")), "value": exact_fans, "precision": text(fans.get("precision"))}, "fans_band": text(creator.get("fans_band")), "eligible_fans": exact_fans is not None and exact_fans <= 5000 and creator.get("eligible_fans") is True, "observed_at": text(creator.get("observed_at"))},
                      "creator_gender_status": text(source.get("creator_gender_status")), "creator_fans_source_recorded": source.get("creator_fans_source_recorded") is True,
                      "content": {**{k: text(content.get(k)) for k in ("title", "text", "published_at", "format")}, **{k: texts(content.get(k)) for k in ("platform_tags", "body_tags", "topics", "production_resources")}},
                      "coverage": {**{k: text(coverage.get(k)) for k in ("status", "sort", "sampling_rule", "comparison_key", "stop_reason")}, "limit": number(coverage.get("limit"))},
                      "metrics": clean_metrics, "comparison": {**{k: text(comparison.get(k)) for k in ("topic", "fans_band", "age_bucket", "format")}, "sample_sufficient": comparison.get("sample_sufficient") is True, "group_size": number(comparison.get("group_size")), "creator_count": number(comparison.get("creator_count"))},
                      "reference_case": {"status": status, "reasons": texts(ref.get("reasons")), "unmet_conditions": texts(ref.get("unmet_conditions")), "threshold_status": text(ref.get("threshold_status"))}, "visual_labels": labels,
                      "screening": {"status": text(screening.get("status")), "reasons": texts(screening.get("reasons")), "platform_comments": {"raw": text((screening.get("platform_comments") or {}).get("raw")), "value": number((screening.get("platform_comments") or {}).get("value")), "precision": text((screening.get("platform_comments") or {}).get("precision"))}, "observed_screen_sample": _sample(screening.get("observed_screen_sample") or {})} if focused else None})
    audience = analysis.get("audience_aggregates") or {}
    clean_audience = {"female_sample_count": number(audience.get("female_sample_count")), "multilabel": audience.get("multilabel") is True}
    for dimension in ("age_band", "interests", "topic_interests", "presented_style", "preferred_style"):
        data = audience.get(dimension) or {}
        clean_audience[dimension] = {"known": number(data.get("known")), "unknown": number(data.get("unknown")), "groups": [{"label": text(g.get("label")), "count": number(g.get("count")) if g.get("sample_sufficient") is True else None, "sample_sufficient": g.get("sample_sufficient") is True} for g in data.get("groups", []) if isinstance(g, dict)]}
    return {"schema_version": text(analysis.get("schema_version")), "study_id": text(analysis.get("study_id")), "source_mode": "real" if analysis.get("source_mode") == "real" else "synthetic", "status": text(analysis.get("status")), "selection_policy": "high_comment_female_case_study" if focused else "default", "minimum_platform_comments": number(analysis.get("minimum_platform_comments")) if focused else None, "screen_counts": screen_counts if focused else None, "notes": notes, "audience_aggregates": clean_audience}


def script_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_report(analysis):
    data = public_data(analysis)
    template = (ASSETS / "report.html").read_text(encoding="utf-8")
    replacements = {"__STYLE__": (ASSETS / "report.css").read_text(encoding="utf-8"), "__DATA__": script_json(data), "__SCRIPT__": (ASSETS / "report.js").read_text(encoding="utf-8")}
    return re.sub(r"__STYLE__|__DATA__|__SCRIPT__", lambda m: replacements[m[0]], template)


def main(argv=None):
    parser = argparse.ArgumentParser(description="导出自包含、离线的小红书公开研究报告")
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        analysis = json.loads(args.analysis.read_text(encoding="utf-8-sig"))
        if not isinstance(analysis, dict) or not isinstance(analysis.get("notes", []), list):
            raise ValueError("analysis must be an analyze JSON object with notes array")
        report = render_report(analysis)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, f"报告导出失败：{error}\n")
    print(f"报告已生成：{args.output.resolve()}")


if __name__ == "__main__":
    main()
