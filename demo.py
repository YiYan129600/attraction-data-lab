"""Run a deterministic, synthetic end-to-end research example offline."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def fixture():
    observed = "2026-10-14T12:00:00+08:00"
    def evidence(value):
        return {"value": value, "basis": "public_self_report", "evidence": {"source": "synthetic_fixture", "locator": "explicit disclosure", "observed_at": observed}}
    data = {"study_id": "demo", "owner_id": "demo", "source_mode": "synthetic", "observed_at": observed,
            "study": {"selection_policy": "high_comment_female_case_study", "minimum_platform_comments": 50},
            "creators": [], "notes": [], "comments": []}
    for index, (title, topic, female, known) in enumerate([
        ("模拟：户外路线讨论", "outdoor", 8, 10),
        ("模拟：训练器械讨论", "fitness", 2, 10),
        ("模拟：日常生活分享", "daily", 2, 3),
    ], 1):
        creator = f"{index:024x}"
        note = f"{index + 100:024x}"
        data["creators"].append({"creator_id": creator, "name": f"模拟作者 {index}", "url": f"https://www.xiaohongshu.com/user/profile/{creator}", "fans": {"raw": "1000", "value": 1000, "precision": "exact"}, "gender": [evidence("male")]})
        data["notes"].append({"note_id": note, "creator_id": creator, "url": f"https://www.xiaohongshu.com/explore/{note}", "title": title, "text": "此记录完全虚构，仅用于演示分析链路。", "topics": [topic], "format": "image", "counts": {"comments": {"raw": "120", "value": 120, "precision": "exact"}}, "coverage": {"status": "partial", "sampling_rule": "synthetic_ten_users", "comparison_key": "synthetic_demo_v1"}})
        for user in range(10):
            gender = "female" if user < female else "male"
            data["comments"].append({"comment_id": f"demo-{index}-{user}", "note_id": note, "user_id": f"synthetic-user-{index}-{user}", "level": 1, "is_author": False, "profile_sampled": True, "audience": {"gender": [evidence(gender)] if user < known else [], "age_band": [evidence("25–34")]}, "interaction_target": "author", "intents": ["shared_interest"]})
    return data


def run(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main():
    private = ROOT / "data/private/demo"
    private.mkdir(parents=True, exist_ok=True)
    source = private / "observations.json"
    source.write_text(json.dumps(fixture(), ensure_ascii=False, indent=2), encoding="utf-8")
    db = str(private / "research.sqlite")
    analysis = str(private / "analysis.json")
    run("-m", "xhs_research", "import", "--db", db, "--input", str(source))
    run("-m", "xhs_research", "validate", "--db", db, "--study", "demo", "--source-mode", "synthetic")
    run("-m", "xhs_research", "analyze", "--db", db, "--study", "demo", "--source-mode", "synthetic", "--output", analysis)
    run("reporting.py", "--analysis", analysis, "--output", "exports/demo/report.html")
    run("community.py", "export", "--analysis", analysis, "--dataset", "demo-v1", "--output", "exports/demo/contribution.json")
    run("community.py", "validate", "exports/demo/contribution.json")
    run("community.py", "merge", "exports/demo/contribution.json", "--db", "exports/demo/community.sqlite", "--html", "exports/demo/community.html")
    print("Synthetic demo ready: exports/demo/report.html and exports/demo/community.html")


if __name__ == "__main__":
    main()
