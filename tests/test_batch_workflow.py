import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from batch_workflow import plan,fresh,digest,gender,normalize_theme,cache_seed
from browser_capture_bridge import CaptureStore

NOW=datetime.now(timezone.utc)
def profile(**kw):return dict(kind='audience_dom',user_id='u',reviewed=True,observed_at=NOW.isoformat(),**kw)

class BatchTests(unittest.TestCase):
    def test_whole_record(self):
        row=profile(); self.assertEqual(digest(row),digest(dict(reversed(list(row.items())))))
        self.assertNotEqual(digest(row),digest({**row,'observed_at':(NOW+timedelta(seconds=1)).isoformat()}))
    def test_cache(self):
        self.assertTrue(fresh(profile(gender=None),NOW))
        self.assertFalse(fresh({**profile(),'reviewed':False},NOW))
        self.assertFalse(fresh({**profile(),'observed_at':(NOW-timedelta(days=8)).isoformat()},NOW))
        self.assertEqual(gender(profile(gender_evidence='#male #female')),'unknown')
    def test_gates_and_resume(self):
        for raw,action in [('0','profile_screen'),('5000','profile_screen'),('0.5万','manual_review'),('5001','excluded')]:
            rows=[dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=['#male'],stats=[dict(label='粉丝',raw=raw)]),dict(kind='note_dom',note_id='n',creator_id='c',observed_at=NOW.isoformat(),comments_raw='50',comments=[dict(user_id='u',level=1,is_author=False)])]
            q,s=plan(rows,now=NOW);self.assertIn(action,s['actions'])
            q2,_=plan([],q,now=NOW);self.assertTrue(all(t['task_id'] in {x['task_id'] for x in q2['tasks']} for t in q['tasks'] if t['action']!='excluded'))
    def test_bridge_restart_and_credentials(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            store=CaptureStore(d); row=profile()
            self.assertEqual(store.save([row,row])[0],1)
            self.assertEqual(CaptureStore(d).save([row])[0],0)
            self.assertEqual(len(list(Path(d).glob('*.json'))),1)
            self.assertEqual(store.save([{**row,'annotation':'new'}])[0],1)
            with self.assertRaises(ValueError):store.save([{**row,'url':'https://x/?xsec_token=secret'}])
    def test_minor_screen_exclusion(self):
        rows=[dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=['#male'],stats=[dict(label='粉丝',raw='0')]),dict(kind='note_dom',note_id='n',creator_id='c',observed_at=NOW.isoformat(),comments_raw='50',comments=[dict(user_id=str(i),level=1,is_author=False) for i in range(8)])]
        rows += [{**profile(gender='female',gender_evidence='#female',tags_text='16岁'), 'user_id':str(i)} for i in range(8)]
        _,s=plan(rows,now=NOW);self.assertNotIn('profile_deep',s['actions']);self.assertIn('unknown',s['actions'])
    def test_counterexample_and_quota(self):
        rows=[dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=['#male'],stats=[dict(label='粉丝',raw='5000')])]
        for n in ['n1','n2']:
            rows.append(dict(kind='note_dom',note_id=n,creator_id='c',observed_at=NOW.isoformat(),comments_raw='50',comments=[dict(user_id=str(i),level=1,is_author=False) for i in range(30)]))
        rows += [{**profile(gender='male',gender_evidence='#male'), 'user_id':str(i)} for i in range(8)]
        q,s=plan(rows,now=NOW)
        self.assertEqual(s['cohorts']['counterexample'],2)
        self.assertEqual(s['actions']['counterexample_deep'],22)
        controls=[t for t in q['tasks'] if t['action']=='counterexample_screen']
        self.assertEqual(sum(t['quota_status']=='selected' for t in controls),1)
        self.assertTrue(all(t['reason']=='checked_sample_all_explicit_male' for t in controls))
    def test_candidate_cached_author_and_done_resume(self):
        rows=[dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=['#female'],stats=[dict(label='粉丝',raw='10')]),dict(kind='search_dom',observed_at=NOW.isoformat(),query='fitness',candidates=[dict(note_id='n',creator_id='c'),dict(note_id='m',creator_id='missing')])]
        q,s=plan(rows,now=NOW)
        self.assertNotIn('note_probe',s['actions']);self.assertIn('author_probe',s['actions'])
        for t in q['tasks']:t['state']='done'
        q2,_=plan(rows,q,now=NOW)
        self.assertTrue(all(t['state']=='pending' for t in q2['tasks'] if t['action']=='author_probe'))
    def test_latest_failure_seed_and_theme(self):
        self.assertEqual(normalize_theme('fitness_progress'),'fitness')
        self.assertEqual(normalize_theme('daily_life'),'daily')
        self.assertEqual(normalize_theme('unrelated query'),'unknown')
        self.assertEqual(cache_seed([profile(),{**profile(),'observed_at':(NOW+timedelta(seconds=1)).isoformat(),'reviewed':False}],NOW+timedelta(seconds=2)),[])
        q,_=plan([],now=NOW)
        self.assertEqual({t['theme'] for t in q['tasks']},{'body_display','fitness','outdoor','daily','car','hobby'})
    def test_deep_reclassifies_and_removes_obsolete_task(self):
        rows=[dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=['#male'],stats=[dict(label='粉丝',raw='5000')]),dict(kind='note_dom',note_id='n',creator_id='c',observed_at=NOW.isoformat(),comments_raw='50',comments=[dict(user_id=str(i),level=1,is_author=False) for i in range(30)])]
        rows += [{**profile(gender='female' if i<8 else 'male',gender_evidence='#female' if i<8 else '#male',tags_text='16岁' if i==29 else ''),'user_id':str(i)} for i in range(30)]
        old={'tasks':[dict(task_id='old',action='note_probe',object_id='n',theme='fitness',state='pending')]}
        q,s=plan(rows,old,now=NOW)
        self.assertEqual(s['cohorts']['counterexample'],1)
        self.assertFalse(any(t['task_id']=='old' for t in q['tasks']))
        self.assertNotIn('comment_expand',s['actions'])
        completed=[t for t in q['tasks'] if t['action']=='counterexample_deep']
        self.assertEqual(len(completed),1);self.assertEqual(completed[0]['state'],'done');self.assertEqual(completed[0]['adult_n'],29)
    def test_obsolete_deep_dependencies_and_balanced_reason(self):
        creator=dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=['#male'],stats=[dict(label='粉丝',raw='5000')])
        note=dict(kind='note_dom',note_id='n',creator_id='c',observed_at=NOW.isoformat(),comments_raw='50',comments=[dict(user_id=str(i),level=1,is_author=False) for i in range(8)])
        old={'tasks':[dict(task_id='olddeep',action='profile_deep',object_id='stale',note_id='n',theme='fitness',state='pending'),dict(task_id='oldexpand',action='comment_expand',object_id='n',theme='fitness',state='pending',target=30),dict(task_id='oldmanual',action='manual_review',object_id='n',theme='fitness',state='pending'),dict(task_id='shared',action='counterexample_deep',object_id='shared',note_id='n',dependent_note_ids=['n','missing'],theme='fitness',state='pending')]}
        q,_=plan([creator,note],old,now=NOW)
        self.assertFalse(any(t['task_id'] in {'olddeep','oldexpand','oldmanual'} for t in q['tasks']))
        self.assertEqual(next(t for t in q['tasks'] if t['task_id']=='shared')['dependent_note_ids'],['missing'])
        profiles=[{**profile(gender='female' if i<4 else 'male',gender_evidence='#female' if i<4 else '#male'),'user_id':str(i)} for i in range(8)]
        q,_=plan([creator,note]+profiles,now=NOW,control_quota=0)
        self.assertEqual(next(t for t in q['tasks'] if t['action']=='counterexample_screen')['reason'],'checked_sample_balanced_or_other')
        q,_=plan([creator,{**note,'comments_raw':'49'}],old,now=NOW)
        self.assertFalse(any(t['task_id'] in {'olddeep','oldexpand','oldmanual'} for t in q['tasks']))

    def test_candidate_unknown_gate_not_note_probe(self):
        rows=[dict(kind='creator_dom',creator_id='c',observed_at=NOW.isoformat(),gender_icon=[],stats=[dict(label='粉丝',raw='0.5万')]),dict(kind='search_dom',observed_at=NOW.isoformat(),candidates=[dict(note_id='n',creator_id='c'),dict(note_id='missingid')])]
        _,s=plan(rows,now=NOW)
        self.assertEqual(s['actions']['manual_review'],2);self.assertNotIn('note_probe',s['actions'])

if __name__=='__main__':unittest.main()
