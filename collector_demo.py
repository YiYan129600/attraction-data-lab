"""Offline synthetic collector example; creates private local artifacts only."""
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent


def main():
    config = json.loads((ROOT / "templates/collector-config.json").read_text(encoding="utf-8"))
    capture = json.loads((ROOT / "examples/capture-demo.synthetic.json").read_text(encoding="utf-8"))
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    for row in capture["records"]:
        row["observed_at"] = stamp
    sample_path = ROOT / "data/private/collector-demo/capture-demo.synthetic.json"
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(json.dumps(capture, ensure_ascii=False, indent=2), encoding="utf-8")
    config.update(study_id="collector-demo", source_mode="synthetic", provider="replay", request_interval=0,
                  captures=str(sample_path), max_candidates=2, max_deep_notes=2,
                  queries={"fitness": "男生健身"})
    path = ROOT / "data/private/collector-demo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "xhs_research.collector", "run", "--config", str(path)], cwd=ROOT, check=True)
    print("Synthetic collector complete. Reports: data/private/collector/<scope>/runs/<run-id>/")


if __name__ == "__main__":
    main()
