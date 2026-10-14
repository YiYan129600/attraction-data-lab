"""Export all positive/control/unknown cases in the pilot's eligible cohort."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from community import export_analysis, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if Path(args.analysis).resolve() == Path(args.output).resolve():
        parser.error("output must differ from private input")
    data = read_json(args.analysis)
    source_count = len(data["notes"])
    data["notes"] = [note for note in data["notes"]
                     if note.get("creator", {}).get("eligible_fans") is True
                     and note.get("creator_gender_status") == "male"
                     and (note.get("platform_counts", {}).get("comments", {}).get("value") or 0) >= 50
                     and note.get("metrics", {}).get("adult_target", {}).get("N", 0) >= 8]
    package = export_analysis(data, args.dataset)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"input_notes": source_count, "exported_records": len(package["records"])}))


if __name__ == "__main__":
    main()
