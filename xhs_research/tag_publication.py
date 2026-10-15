"""Strict allowlist export for aggregate-only tag releases (separate from v1)."""
import json
import math
import re
from pathlib import Path

from .tag_analysis import METRICS

SCOPES = ('all_tagged', 'verified_lowfan', 'balanced_comparison')
SUMMARY = ('n', 'missing', 'mean', 'median', 'q1', 'q3', 'min', 'max', 'unique_authors', 'trimmed_mean', 'display_estimate_count')
COUNTS = ('archive_unique', 'tag_known', 'tag_known_with_both_exact_metrics', 'tag_known_likes_verified_exact', 'tag_known_likes_semantics_unverified')
DEEP = ('historical_front30_complete', 'balanced_deep_reads', 'balanced_deep_complete', 'any_deep_read_distinct')
POLICY = 'aggregate-only; approved generic tag labels; no cases, identities, voices, media or source paths'
LIMITATION = 'Descriptive association only; sampling and snapshot differences remain; not causal or representative of all audiences.'


def number(value):
    if value is None or type(value) in (int, float) and math.isfinite(value):
        return value
    raise ValueError('Expected finite numeric aggregate or null')


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected nonnegative integer count')
    return value


def summary(value):
    return {key: count(value[key]) if key in ('n', 'missing', 'unique_authors', 'display_estimate_count') else number(value[key]) for key in SUMMARY}


def comparison(value):
    robust, strata = value['remove_each_group_max'], value['stratified']
    direction = robust['direction']
    if direction not in ('positive', 'negative', 'equal', None) or type(value['insufficient']) is not bool:
        raise ValueError('Invalid comparison flags')
    return dict(with_tag=summary(value['with_tag']), without_tag=summary(value['without_tag']),
                diff=number(value['diff']), ratio_pct=number(value['ratio_pct']), insufficient=value['insufficient'],
                remove_each_group_max=dict(diff=number(robust['diff']), direction=direction),
                stratified=dict(weighted_mean_diff=number(strata['weighted_mean_diff']),
                    covered_samples=count(strata['covered_samples']), covered_strata=count(strata['covered_strata']),
                    missing_or_unmapped_topic_samples=count(strata['missing_or_unmapped_topic_samples'])))


def export_aggregates(stats, audit, approved_labels, dataset_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,100}', dataset_id):
        raise ValueError('Invalid release identifier')
    if stats.get('schema_version') != 'tag-statistics-v1':
        raise ValueError('Unsupported statistics schema')
    source_mode = stats.get('source_mode', 'real')
    if source_mode not in ('real', 'synthetic'):
        raise ValueError('Invalid source mode')
    if any(not isinstance(t, str) or len(t) > 80 or re.search(r'[@:/\\]|[0-9a-f]{24}|1[3-9][0-9]{9}', t) for t in approved_labels):
        raise ValueError('Approved labels must be reviewed generic terms without identifiers or paths')
    # No hashes of personal values and no public mapping back to unreviewed labels.
    all_tags = sorted({row['tag'] for name in SCOPES for row in stats['scopes'][name]['tags']})
    labels = {tag: tag if tag in approved_labels else f'未公开标签{i:04d}' for i, tag in enumerate(all_tags, 1)}
    scopes = {}
    for name in SCOPES:
        source = stats['scopes'][name]
        rows = []
        for row in source['tags']:
            rows.append(dict(tag=labels[row['tag']], tag_posts=count(row['tag_posts']), no_tag_posts=count(row['no_tag_posts']),
                metrics={key: comparison(row['metrics'][key]) for key in METRICS},
                top3_cooccurring_tags=[dict(tag=labels[item['tag']], n=count(item['n'])) for item in row['top3_cooccurring_tags']]))
        scopes[name] = dict(n_posts=count(source['n_posts']), unique_tags=count(source['unique_tags']),
                            metrics={key: summary(source['metrics'][key]) for key in METRICS}, tags=rows)
    source_counts = audit['counts']
    return dict(schema_version='public-tag-aggregates-v1', dataset_id=dataset_id, source_mode=source_mode,
                privacy_policy=POLICY, limitation=LIMITATION, counts={key: count(source_counts[key]) for key in COUNTS},
                deep_read_counts={key: count(source_counts['actual_deep_distinct'][key]) for key in DEEP},
                tag_label_counts=dict(total=len(all_tags), approved=sum(t in approved_labels for t in all_tags),
                                      withheld=sum(t not in approved_labels for t in all_tags)), scopes=scopes)


def validate_aggregates(value, approved_labels):
    """Round-trip through the allowlist, rejecting any extra field at any depth."""
    counts = value['counts']
    stats = dict(schema_version='tag-statistics-v1', source_mode=value['source_mode'], scopes=value['scopes'])
    audit = dict(counts={**counts, 'actual_deep_distinct': value['deep_read_counts']})
    labels = {row['tag'] for scope in value['scopes'].values() for row in scope['tags']}
    anonymous = {t for t in labels if re.fullmatch(r'未公开标签[0-9]{4,}', t)}
    if labels - set(approved_labels) - anonymous:
        raise ValueError('Unreviewed public tag label')
    expected = export_aggregates(stats, audit, labels, value['dataset_id'])
    expected['tag_label_counts'] = dict(total=len(labels), approved=len(labels-anonymous), withheld=len(anonymous))
    if value != expected:
        raise ValueError('Unexpected fields or inconsistent aggregate metadata')
    all_scope = value['scopes']['all_tagged']
    if counts['archive_unique'] < counts['tag_known'] or counts['tag_known'] != all_scope['n_posts']:
        raise ValueError('Archive/tag coverage mismatch')
    if counts['tag_known_likes_verified_exact'] != all_scope['metrics']['likes_verified']['n']:
        raise ValueError('Verified likes coverage mismatch')
    if counts['tag_known_with_both_exact_metrics'] > min(all_scope['metrics']['likes']['n'], all_scope['metrics']['platform_comments']['n']):
        raise ValueError('Paired metric coverage exceeds available values')
    for scope in value['scopes'].values():
        if scope['n_posts'] > all_scope['n_posts']:
            raise ValueError('Scope exceeds tagged archive')
        if any(s['n'] + s['missing'] != scope['n_posts'] for s in scope['metrics'].values()):
            raise ValueError('Invalid scope metric denominator')
        if scope['unique_tags'] != len(scope['tags']) or len({t['tag'] for t in scope['tags']}) != len(scope['tags']):
            raise ValueError('Invalid tag counts')
        for row in scope['tags']:
            if row['tag_posts'] + row['no_tag_posts'] != scope['n_posts']:
                raise ValueError('Invalid tag denominator')
            if any(not 0 < item['n'] <= row['tag_posts'] for item in row['top3_cooccurring_tags']):
                raise ValueError('Invalid cooccurrence count')
            for key in METRICS:
                for side, n in (('with_tag', row['tag_posts']), ('without_tag', row['no_tag_posts'])):
                    s = row['metrics'][key][side]
                    if s['n'] + s['missing'] != n:
                        raise ValueError('Invalid metric denominator')
    return True


def read_labels(path):
    return set(json.loads(Path(path).read_text(encoding='utf-8-sig')))
