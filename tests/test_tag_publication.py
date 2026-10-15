import copy
import importlib.util
import json
from pathlib import Path
import unittest

from xhs_research.tag_analysis import scope_stats, summary
from xhs_research.tag_publication import COUNTS, DEEP, export_aggregates, read_labels, validate_aggregates

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    source = json.loads((ROOT / 'examples/tag-posts.synthetic.json').read_text(encoding='utf-8'))
    rows = [p for p in source['posts'] if p['tag_known']]
    stats = dict(schema_version='tag-statistics-v1', source_mode='synthetic',
                 scopes={name: scope_stats(rows) for name in ('all_tagged', 'verified_lowfan', 'balanced_comparison')})
    audit = dict(counts={**{k: 0 for k in COUNTS}, 'archive_unique':4, 'tag_known':3,
                        'tag_known_likes_verified_exact':2, 'tag_known_with_both_exact_metrics':2,
                        'actual_deep_distinct': {k: 0 for k in DEEP}})
    return stats, audit


class TagPublicationTests(unittest.TestCase):
    def test_exact_zero_estimates_missing_and_verified_provenance(self):
        posts = [dict(likes=0), dict(likes=None), dict(likes=12000, metric_evidence={'likes': {'precision':'display_estimate'}}), dict(likes=True)]
        value = summary(posts, 'likes')
        self.assertEqual((value['n'], value['missing'], value['min'], value['display_estimate_count']), (1, 3, 0, 1))
        self.assertEqual(summary([dict(likes_verified=10)], 'likes_verified')['n'], 0)

    def test_shared_strata_require_two_on_both_sides(self):
        posts = [dict(tags=['健身'] if i < 2 else [], platform_comments=i, source_cohort='demo', topic_stratum='02') for i in range(4)]
        value = scope_stats(posts)['tags'][0]['metrics']['platform_comments']['stratified']
        self.assertEqual((value['covered_samples'], value['covered_strata'], value['weighted_mean_diff']), (4, 1, -2))
        posts[0]['topic_stratum'] = None
        self.assertEqual(scope_stats(posts)['tags'][0]['metrics']['platform_comments']['stratified']['covered_strata'], 0)

    def test_nested_personal_fields_are_dropped_by_fresh_allowlist(self):
        stats, audit = inputs()
        row = stats['scopes']['all_tagged']['tags'][0]
        row.update(raw_forms=['PRIVATE_RAW'], creator_id='PRIVATE_CREATOR', topic_composition={'PRIVATE_TOPIC': 1})
        row['metrics']['likes']['cases'] = {'high':[{'id':'PRIVATE_POST', 'title':'PRIVATE_TITLE'}]}
        row['metrics']['likes']['stratified']['strata'] = [{'source_cohort':'PRIVATE_PATH'}]
        audit['inputs'] = [{'path':'PRIVATE_PATH'}]
        value = export_aggregates(stats, audit, {'健身', '日常'}, 'synthetic-test')
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertNotIn('户外', json.dumps(value, ensure_ascii=False))
        self.assertEqual(value['tag_label_counts'], dict(total=3, approved=2, withheld=1))
        self.assertEqual(value['source_mode'], 'synthetic')
        self.assertTrue(validate_aggregates(value, {'健身','日常'}))

    def test_validator_rejects_extra_fields_and_invalid_denominators(self):
        stats, audit = inputs()
        value = export_aggregates(stats, audit, {'健身','日常'}, 'synthetic-test')
        for mutate in (lambda d: d.update(cookie='PRIVATE'),
                       lambda d: d['scopes']['all_tagged']['tags'][0]['metrics']['likes'].update(cases=[]),
                       lambda d: d['scopes']['all_tagged'].update(n_posts=99)):
            bad = copy.deepcopy(value)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_aggregates(bad, {'健身','日常'})

    def test_html_escapes_and_never_embeds_personal_records(self):
        stats, audit = inputs()
        value = export_aggregates(stats, audit, {'健身','日常'}, 'synthetic-test')
        spec = importlib.util.spec_from_file_location('aggregate_report', ROOT / 'scripts/build_revision_report.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.render(value)
        self.assertIn('相关不等于因果', report)
        self.assertNotIn('<script', report)
        self.assertNotIn('creator_id', report)
        self.assertNotIn('fetch(', report)

    def test_current_public_release_validates_without_private_inputs(self):
        for path in (ROOT / 'aggregates').glob('*.json'):
            value = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(validate_aggregates(value, read_labels(ROOT / 'templates/public-tag-labels.json')))
            self.assertEqual(value['counts']['archive_unique'], 1845)
            self.assertEqual(value['tag_label_counts']['total'], 834)


if __name__ == '__main__':
    unittest.main()
