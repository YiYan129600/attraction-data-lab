"""Offline incremental planning; no network or browser control."""
import argparse
import hashlib
import json
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from import_browser_captures import load_captures, gender_evidence, age_evidence
from xhs_research.core import normalize_count, parse_time


def canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(row):
    return hashlib.sha256(canonical(row).encode()).hexdigest()


def gender(row, creator=False):
    values = {e['value'] for e in gender_evidence(row, creator=creator)}
    return next(iter(values)) if len(values) == 1 else 'unknown'


def fresh(row, now, days=7):
    return bool(row and row.get('reviewed') is True and timedelta(0) <= now - parse_time(row['observed_at']) <= timedelta(days=days))


def external_users(note):
    return list(dict.fromkeys(c['user_id'] for c in note.get('comments', []) if c.get('user_id') and c.get('level') == 1 and c.get('is_author') is False and not c.get('parent_id')))

def normalize_theme(value):
    text=str(value or '').lower()
    for theme,words in [('body_display',['body_display','身材展示','薄肌','腹肌']),('fitness',['fitness','健身','增肌']),('outdoor',['outdoor','户外','徒步','露营']),('daily',['daily','日常','生活']),('car',['car','汽车','车主']),('hobby',['hobby','爱好','桌游','游戏','料理','做饭'])]:
        if any(word in text for word in words):return theme
    return 'unknown'

def cache_seed(rows, now, days=7):
    latest={}
    for row in rows:
        if row['kind']=='audience_dom' and row.get('user_id'):
            uid=row['user_id']
            if uid not in latest or parse_time(row['observed_at'])>=parse_time(latest[uid]['observed_at']):latest[uid]=row
    return [{k:row.get(k) for k in ['kind','user_id','reviewed','observed_at','gender','gender_evidence','tags_text']} for row in latest.values() if fresh(row,now,days)]


def plan(rows, previous=None, now=None, cache_days=7, themes=None, control_quota=1):
    now = now or datetime.now(timezone.utc)
    latest = {}
    for row in rows:
        key = (row['kind'], row.get('note_id') if row['kind'] == 'note_dom' else row.get('creator_id') if row['kind'] == 'creator_dom' else row.get('user_id') if row['kind'] == 'audience_dom' else digest({k:v for k,v in row.items() if not k.startswith('_')}))
        if key not in latest or parse_time(row['observed_at']) >= parse_time(latest[key]['observed_at']):
            latest[key] = row
    old = {t['task_id']:t for t in (previous or {}).get('tasks', [])}
    tasks = {}
    def add(action, object_id, theme='unknown', **details):
        theme=normalize_theme(theme)
        task_id = digest([action, object_id])[:24]
        task = {'task_id':task_id, 'action':action, 'object_id':object_id, 'theme':theme, 'state':'pending', **details}
        if task_id in old:
            task['state'] = old[task_id].get('state', 'pending')
            if task['state']=='done' and action not in {'search'}: task['state']='pending'
        if task_id in tasks and details.get('note_id'):
            task['dependent_note_ids']=list(dict.fromkeys(tasks[task_id].get('dependent_note_ids',[tasks[task_id].get('note_id')])+[details['note_id']]))
        tasks[task_id] = task
    notes = [v for (kind,_),v in latest.items() if kind == 'note_dom']
    theme_map={r['note_id']:normalize_theme(r.get('primary_theme')) for r in rows if r['kind']=='visual_annotation' and r.get('primary_theme')}
    for r in rows:
        if r['kind']=='note_dom' and r.get('query') and r['note_id'] not in theme_map:theme_map[r['note_id']]=normalize_theme(r['query'])
    for theme in themes or ['body_display','fitness','outdoor','daily','car','hobby']:
        add('search', theme, theme)
    for row in rows:
        if row['kind'] == 'search_dom':
            for c in row.get('candidates', []):
                if c.get('note_id') and ('note_dom',c['note_id']) not in latest:
                    cid=c.get('creator_id'); creator=latest.get(('creator_dom',cid))
                    if not cid:
                        add('manual_review',c['note_id'],row.get('query') or 'unknown',reason='candidate_author_id_missing');continue
                    if cid and creator is None:add('author_probe',cid,row.get('query') or 'unknown',note_id=c['note_id']);continue
                    if creator:
                        fans=normalize_count(raw=next((s.get('raw') for s in creator.get('stats',[]) if str(s.get('label','')).strip()=='粉丝'),None))
                        if (fans['value'] is not None and fans['value']>5000) or gender(creator,True) in {'female','other'}:
                            add('excluded',c['note_id'],row.get('query') or 'unknown',reason='verified_author_gate_failure');continue
                        if fans['precision']!='exact' or fans['value'] is None or gender(creator,True)!='male':
                            add('manual_review',c['note_id'],row.get('query') or 'unknown',reason='candidate_author_gate_unknown');continue
                    add('note_probe', c['note_id'], row.get('query') or 'unknown')
    cache_hits = 0
    controls = Counter()
    cohorts = Counter()
    def fan_band(note):
        creator=latest.get(('creator_dom',note['creator_id']),{})
        fans=normalize_count(raw=next((s.get('raw') for s in creator.get('stats',[]) if str(s.get('label','')).strip()=='粉丝'),None))
        value=fans['value']
        return None if value is None else 0 if value<=500 else 1 if value<=2000 else 2
    positive_bands=defaultdict(set)
    for note in notes:
        ps=[latest.get(('audience_dom',u)) for u in external_users(note)[:8]]
        if len(ps)==8 and all(fresh(p,now,cache_days) for p in ps) and not any(any(e['value']=='minor' for e in age_evidence(p)) for p in ps):
            gs=[gender(p) for p in ps]; k=sum(g!='unknown' for g in gs)
            if k/8>=.6 and k and gs.count('female')/k>.5:positive_bands[theme_map.get(note['note_id']) or note.get('primary_theme') or note.get('query') or 'unknown'].add(fan_band(note))
    notes.sort(key=lambda note: (fan_band(note) not in positive_bands[theme_map.get(note['note_id']) or note.get('primary_theme') or note.get('query') or 'unknown'], note['note_id']))
    for note in notes:
        nid, cid = note['note_id'], note['creator_id']
        theme = normalize_theme(theme_map.get(nid) or note.get('primary_theme') or note.get('query'))
        creator = latest.get(('creator_dom', cid))
        count = normalize_count(raw=note.get('comments_raw'))
        if creator is None:
            add('author_probe', cid, theme, note_id=nid)
            continue
        fans = normalize_count(raw=next((s.get('raw') for s in creator.get('stats',[]) if str(s.get('label','')).strip() == '粉丝'),None))
        g = gender(creator, True)
        if (fans['value'] is not None and fans['value'] > 5000) or g in {'female','other'} or (count['value'] is not None and count['value'] < 50):
            add('excluded', nid, theme, reason='verified_gate_failure')
            continue
        if fans['precision'] != 'exact' or fans['value'] is None or g != 'male' or count['value'] is None or count['value'] < 50:
            add('manual_review', nid, theme, reason='unknown_or_rounded_gate')
            continue
        users = external_users(note)
        first = users[:8]
        deep_users=users[:30]
        checked_users=[u for u in deep_users if fresh(latest.get(('audience_dom',u)),now,cache_days)]
        deep_evidence=len(checked_users)>8
        reviewed = [latest.get(('audience_dom',u)) for u in (checked_users if deep_evidence else first)]
        if len(first) < 8:
            add('comment_expand', nid, theme, target=8)
        ready = len(first) == 8 and all(fresh(latest.get(('audience_dom',u)),now,cache_days) for u in first)
        deep_complete=len(deep_users)==30 and len(checked_users)==30
        known = [gender(p) for p in reviewed if p and fresh(p,now,cache_days) and not any(e['value']=='minor' for e in age_evidence(p))]
        k = sum(g in {'male','female','other'} for g in known)
        f = known.count('female')
        n=len(known)
        positive = ready and n>=8 and k/n >= .6 and k > 0 and f/k > .5
        control = ready and n>=8 and k/n >= .6 and k > 0 and f/k <= .5
        cohort = 'positive' if positive else 'counterexample' if control else 'unknown'
        cohorts[cohort] += 1
        reason = ('checked_sample_all_explicit_male' if known and all(g=='male' for g in known) else 'checked_sample_no_explicit_female' if f==0 else 'checked_sample_male_heavy' if known.count('male')>f else 'checked_sample_balanced_or_other') if control else None
        if control and controls[theme] >= control_quota and not deep_complete:
            add('counterexample_screen', nid, theme, cohort=cohort, reason=reason, quota_status='deferred_quota')
            continue
        if control: controls[theme] += 1
        extend = positive or control
        selected = users[:30] if extend else first
        if extend and len(users) < 30:
            add('comment_expand', nid, theme, target=30, cohort=cohort)
        for uid in selected:
            p = latest.get(('audience_dom',uid))
            if fresh(p,now,cache_days):
                cache_hits += 1
                continue
            add('counterexample_deep' if control else 'profile_deep' if extend else 'profile_screen', uid, theme, note_id=nid, cohort=cohort, reason=reason)
        if control:
            add('counterexample_deep' if deep_complete else 'counterexample_screen', nid, theme, cohort=cohort, reason=reason, quota_status='completed_evidence' if deep_complete else 'selected', sample_checked=len(checked_users), adult_n=n)
            if deep_complete:tasks[digest(['counterexample_deep',nid])[:24]]['state']='done'
        elif ready and not extend:
            add('unknown', nid, theme, cohort='unknown', reason='insufficient_known_gender')
    # Keep pending work even if a later partial DOM snapshot no longer lists it.
    for tid,t in old.items():
        if tid not in tasks and t.get('action') not in {'excluded','unknown','counterexample_screen'} and t.get('state','pending') not in {'done','cancelled'}:
            if t.get('action') in {'profile_screen','profile_deep','counterexample_deep','comment_expand','manual_review'}:
                dependencies=t.get('dependent_note_ids') or [t.get('note_id') or (t.get('object_id') if t.get('action') in {'comment_expand','manual_review'} else None)]
                missing=[nid for nid in dependencies if nid and ('note_dom',nid) not in latest]
                if dependencies and all(dependencies):
                    if not missing:continue
                    t={**t,'dependent_note_ids':missing,'note_id':missing[0]}
            if t.get('action')=='search' and normalize_theme(t.get('object_id')) != t.get('object_id'):continue
            if t.get('action')=='note_probe' and ('note_dom',t['object_id']) in latest:continue
            if t.get('action')=='author_probe' and ('creator_dom',t['object_id']) in latest:continue
            if t.get('action')=='comment_expand' and ('note_dom',t['object_id']) in latest and len(external_users(latest[('note_dom',t['object_id'])]))>=t.get('target',30):continue
            if t.get('action')=='note_probe' and any(t['object_id'] in x.get('dependent_note_ids',[x.get('note_id')]) for x in tasks.values() if x['action']=='author_probe'):
                continue
            if t.get('action')=='note_probe' and any(x['object_id']==t['object_id'] and x['action']=='excluded' for x in tasks.values()):continue
            if t.get('action')=='note_probe' and any(x['object_id']==t['object_id'] and x['action']=='manual_review' for x in tasks.values()):continue
            if t.get('action') in {'profile_screen','profile_deep','counterexample_deep'} and fresh(latest.get(('audience_dom',t['object_id'])),now,cache_days):
                continue
            tasks[tid] = t
    buckets = defaultdict(deque)
    for task in tasks.values():
        buckets[task['theme']].append(task)
    ordered = []
    while any(buckets.values()):
        for theme in sorted(buckets):
            if buckets[theme]: ordered.append(buckets[theme].popleft())
    summary = {'tasks':len(ordered), 'actions':dict(Counter(t['action'] for t in ordered)), 'states':dict(Counter(t['state'] for t in ordered)), 'cache_hits':cache_hits, 'latest_notes':len(notes), 'cohorts':{c:cohorts[c] for c in ['positive','counterexample','unknown']}, 'control_quota_per_theme':control_quota}
    return {'version':1, 'generated_at':now.isoformat(), 'cache_days':cache_days, 'tasks':ordered}, summary


def audit(paths):
    total = bytes_total = 0
    for path in paths:
        bytes_total += path.stat().st_size
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        total += len(data if isinstance(data,list) else data.get('records',[data]))
    rows = load_captures(paths)
    unique = [{k:v for k,v in r.items() if not k.startswith('_')} for r in rows]
    return rows, {'files':len(paths),'records':total,'unique_records':len(rows),'duplicate_records':total-len(rows),'bytes':bytes_total,'compact_unique_bytes':len(canonical(unique).encode('utf-8')),'kinds':dict(Counter(r['kind'] for r in rows))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['audit','plan','checkpoint','record-batch'])
    p.add_argument('--captures',type=Path,default=Path('data/private/browser-captures'))
    p.add_argument('--output',type=Path,default=Path('data/private/batch-workflow'))
    p.add_argument('--cache-days',type=float,default=7)
    p.add_argument('--control-quota',type=int,default=1)
    p.add_argument('--theme',action='append')
    p.add_argument('--task-id')
    p.add_argument('--reason')
    p.add_argument('--state',choices=['pending','in_progress','blocked','done','cancelled'])
    p.add_argument('--status',default='completed')
    for name in ['duration_seconds','new_candidates','new_notes','profile_visits','cache_hits','tokens']:
        p.add_argument('--'+name.replace('_','-'),type=float if name=='duration_seconds' else int)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    queue_path=a.output/'queue.json'
    previous=json.loads(queue_path.read_text(encoding='utf-8')) if queue_path.exists() else None
    if a.command=='checkpoint':
        if not a.task_id or not a.state or not previous: p.error('checkpoint requires existing queue, --task-id and --state')
        if a.state=='cancelled' and not a.reason:p.error('cancelled checkpoint requires --reason')
        found=False
        for t in previous['tasks']:
            if t['task_id']==a.task_id:
                t['state']=a.state
                if a.reason:t['checkpoint_reason']=a.reason
                found=True
        if not found:p.error('unknown task-id')
        queue_path.write_text(canonical(previous)+'\n',encoding='utf-8'); print('{"checkpoint_saved":true}'); return
    if a.command=='record-batch':
        row={n:getattr(a,n) for n in ['duration_seconds','new_candidates','new_notes','profile_visits','cache_hits','tokens']}
        row.update(status=a.status,observed_at=datetime.now(timezone.utc).isoformat())
        with (a.output/'cost-log.jsonl').open('a',encoding='utf-8') as f:f.write(canonical(row)+'\n')
        print('{"batch_logged":true}'); return
    paths=sorted(a.captures.glob('capture-*.json')) if a.captures.is_dir() else [a.captures] if a.captures.exists() else []
    rows,summary=audit(paths)
    if a.command=='plan':
        queue,planning=plan(rows,previous,cache_days=a.cache_days,themes=a.theme,control_quota=max(0,a.control_quota))
        queue_path.write_text(canonical(queue)+'\n',encoding='utf-8'); summary['planning']=planning
        (a.output/'cache-seed.json').write_text(canonical(cache_seed(rows,datetime.now(timezone.utc),a.cache_days))+'\n',encoding='utf-8')
    (a.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
