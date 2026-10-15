"""Build a standalone aggregate-only HTML report from a validated public release."""
import argparse
import html
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xhs_research.tag_publication import read_labels, validate_aggregates


def render(data):
    def fmt(value):
        return '—' if value is None else f'{value:,.2f}'.rstrip('0').rstrip('.')
    sections = []
    for name, scope in data['scopes'].items():
        rows = []
        for row in scope['tags']:
            comments, likes = row['metrics']['platform_comments'], row['metrics']['likes_verified']
            rows.append('<tr>' + ''.join('<td>'+html.escape(str(x))+'</td>' for x in (
                row['tag'], row['tag_posts'], comments['with_tag']['n'], fmt(comments['diff']),
                fmt(comments['stratified']['weighted_mean_diff']), likes['with_tag']['n'], fmt(likes['diff']),
                '不足5样本' if comments['insufficient'] else '描述性比较')) + '</tr>')
        sections.append(f'<h2>{html.escape(name)} · {scope["n_posts"]}篇</h2><div class="scroll"><table><thead><tr>'+
            ''.join('<th>'+s+'</th>' for s in ('标签', '有标签篇数', '评论数有效样本', '评论数均值差', '共同层加权差', '来源明确点赞样本', '明确点赞均值差', '样本提示'))+
            '</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>')
    counts = data['counts']
    lead = (f'归档候选 {counts["archive_unique"]} 篇；有真实标签 {counts["tag_known"]} 篇，'
            f'标签 {data["tag_label_counts"]["total"]} 个；深读记录 {data["deep_read_counts"]["any_deep_read_distinct"]} 篇。'
            f'标签样本中可读点赞 {counts["tag_known_with_both_exact_metrics"]} 篇，'
            f'来源明确 {counts["tag_known_likes_verified_exact"]} 篇，'
            f'语义待核对 {counts["tag_known_likes_semantics_unverified"]} 篇。')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+\
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">'+\
        '<title>内容标签聚合研究</title><style>body{font:15px/1.7 system-ui,sans-serif;margin:3rem auto;padding:0 1.4rem;max-width:1240px;color:#203044;background:#f7f9fc}h1{font-size:2rem}h2{margin-top:2.5rem}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:white}td,th{padding:.65rem;text-align:left;border-bottom:1px solid #dbe1ea;white-space:nowrap}th{background:#e7edf6}p{max-width:90ch}</style>'+\
        '<h1>内容标签聚合研究</h1><p>'+html.escape(lead)+'</p><p>相关不等于因果。标签样本不是全站或全部归档候选的代表；深读记录不等于核验完成。均值差为有标签组减无标签组，缺失不补零。</p>'+\
        f'<p>通用标签公开 {data["tag_label_counts"]["approved"]} 个；其余 {data["tag_label_counts"]["withheld"]} 个仅保留匿名标签统计。页面不包含帖子、账号、原声或图片。</p>'+''.join(sections)+'</html>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--labels', type=Path, default=Path(__file__).resolve().parents[1] / 'templates/public-tag-labels.json')
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding='utf-8-sig'))
    validate_aggregates(data, read_labels(args.labels))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data), encoding='utf-8')
    print('Aggregate report written.')


if __name__ == '__main__':
    main()
