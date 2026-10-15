"""Export only allowlisted numeric aggregates and reviewed generic tag labels."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xhs_research.tag_publication import export_aggregates, read_labels, validate_aggregates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--statistics', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--labels', type=Path, default=Path(__file__).resolve().parents[1] / 'templates/public-tag-labels.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    labels = read_labels(args.labels)
    result = export_aggregates(json.loads(args.statistics.read_text(encoding='utf-8-sig')),
                               json.loads(args.audit.read_text(encoding='utf-8-sig')), labels, args.dataset)
    validate_aggregates(result, labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(dataset_id=result['dataset_id'], labels=result['tag_label_counts']), ensure_ascii=False))


if __name__ == '__main__':
    main()
