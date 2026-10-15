"""Recompute local statistics from a reconciled private combined-data document."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xhs_research.tag_analysis import scope_stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding='utf-8-sig'))
    posts = source['posts']
    scopes = dict(all_tagged=[p for p in posts if p['tag_known']],
                  verified_lowfan=[p for p in posts if p['tag_known'] and p.get('verified_lowfan_eligible')],
                  balanced_comparison=[p for p in posts if p['tag_known'] and 'A' in p.get('balanced_pool', [])])
    result = dict(schema_version='tag-statistics-v1', source_mode=source.get('source_mode', 'real'), scopes={k: scope_stats(v) for k, v in scopes.items()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print('Local statistics written; use export_tag_aggregates.py before sharing.')


if __name__ == '__main__':
    main()
