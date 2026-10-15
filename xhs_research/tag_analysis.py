"""Offline descriptive tag statistics; no network and no personal output fields.

Adapted from the local evidence reconciliation and revision report builders.
Input records remain private. Only explicitly approved generic tag labels are
retained by the separate public aggregate exporter.
"""
import collections
import math
import statistics
import unicodedata

METRICS = ("likes", "likes_verified", "platform_comments")


def is_exact(post, key):
    evidence = post.get('metric_evidence', {}).get(key, {})
    if evidence.get('precision', 'exact') != 'exact':
        return False
    if key == 'likes_verified' and '#search.likes_raw' not in str(evidence.get('source', '')):
        return False
    return type(post.get(key)) is int and post[key] >= 0


def norm_tag(value):
    return unicodedata.normalize("NFKC", value).strip().lstrip("#＃").strip().casefold()


def summary(posts, key):
    eligible = [p for p in posts if is_exact(p, key)]
    xs = sorted(p[key] for p in eligible)
    n = len(xs)
    def quantile(frac):
        if not n:
            return None
        pos = (n - 1) * frac
        low, high = math.floor(pos), math.ceil(pos)
        return xs[low] + (xs[high] - xs[low]) * (pos - low)
    trim = max(1, int(n * .1)) if n >= 10 else 0
    return dict(n=n, missing=len(posts)-n, mean=statistics.mean(xs) if n else None,
                median=statistics.median(xs) if n else None, q1=quantile(.25), q3=quantile(.75),
                min=min(xs) if n else None, max=max(xs) if n else None,
                unique_authors=len({p.get('creator_id') for p in eligible if p.get('creator_id')}),
                trimmed_mean=statistics.mean(xs[trim:-trim]) if trim else None,
                display_estimate_count=sum(p.get('metric_evidence', {}).get(key, {}).get('precision') == 'display_estimate' for p in posts))


def scope_stats(posts):
    """Compute unadjusted and shared source/topic comparisons, never causal effects.

    Inputs must use canonical tags and a pre-reviewed topic_stratum. Unknown topics
    do not enter strata; each side needs at least two samples in a shared stratum.
    """
    tags = sorted({t for p in posts for t in p['tags']})
    rows = []
    for tag in tags:
        yes = [p for p in posts if tag in p['tags']]
        no = [p for p in posts if tag not in p['tags']]
        row = dict(tag=tag, tag_posts=len(yes), no_tag_posts=len(no), metrics={},
                   top3_cooccurring_tags=[dict(tag=t, n=n) for t, n in collections.Counter(t for p in yes for t in p['tags'] if t != tag).most_common(3)])
        for key in METRICS:
            a, b = summary(yes, key), summary(no, key)
            diff = a['mean'] - b['mean'] if a['n'] and b['n'] else None
            ya = sorted(p[key] for p in yes if is_exact(p, key))
            na = sorted(p[key] for p in no if is_exact(p, key))
            robust = statistics.mean(ya[:-1]) - statistics.mean(na[:-1]) if len(ya) > 1 and len(na) > 1 else None
            strata = collections.defaultdict(lambda: [[], []])
            for p in posts:
                topic = p.get('topic_stratum')
                if is_exact(p, key) and topic not in (None, '', 'unknown', '未知'):
                    strata[(p.get('source_cohort', 'unknown'), topic)][0 if tag in p['tags'] else 1].append(p[key])
            covered = [dict(source_cohort=co, topic=topic, with_tag_n=len(y), without_tag_n=len(n),
                            diff=statistics.mean(y)-statistics.mean(n), weight=len(y)+len(n))
                       for (co, topic), (y, n) in sorted(strata.items()) if len(y) >= 2 and len(n) >= 2]
            weight = sum(x['weight'] for x in covered)
            row['metrics'][key] = dict(with_tag=a, without_tag=b, diff=diff,
                ratio_pct=diff/b['mean']*100 if diff is not None and b['mean'] else None,
                insufficient=a['n'] < 5 or b['n'] < 5,
                remove_each_group_max=dict(diff=robust, direction='positive' if robust and robust > 0 else 'negative' if robust and robust < 0 else 'equal' if robust == 0 else None),
                stratified=dict(weighted_mean_diff=sum(x['diff']*x['weight'] for x in covered)/weight if weight else None,
                    covered_samples=weight, covered_strata=len(covered),
                    missing_or_unmapped_topic_samples=sum(is_exact(p, key) and p.get('topic_stratum') in (None, '', 'unknown', '未知') for p in posts), strata=covered))
        rows.append(row)
    return dict(n_posts=len(posts), unique_tags=len(tags), metrics={k: summary(posts, k) for k in METRICS}, tags=rows)
