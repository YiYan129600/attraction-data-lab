import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reporting


def fixture():
    return {"schema_version": "1", "study_id": "synthetic-test", "source_mode": "synthetic", "status": "observed_descriptive", "notes": [{"note_id": "test-note", "url": "https://www.xiaohongshu.com/explore/test?xsec_token=PRIVATE_TOKEN", "creator_gender_status": "male", "creator": {"creator_id": "creator-secret", "name": "测试账号", "url": "https://www.xiaohongshu.com/user/profile/test", "fans": {"raw": "100", "value": 100, "precision": "exact"}, "fans_band": "0–500", "eligible_fans": True, "bio": "PRIVATE_BIO"}, "content": {"title": "测试健身", "text": "__SCRIPT__ </script><script>alert(1)</script> & \u2028", "topics": ["健身运动"], "platform_tags": ["训练日常"], "body_tags": [], "media": [{"url": "https://remote.invalid/image.jpg"}]}, "metrics": {"U": 8, "N": 4, "K": 2, "F": 1, "F_over_K": 99, "adult_target": {"N": 3, "K": 1, "F": 1}}, "coverage": {"status": "partial"}, "comparison": {}, "visual_labels": [{"dimension": "subject", "value": "人物+活动", "evidence": "封面", "status": "unreviewed"}, {"dimension": "composition", "value": "中景"}], "reference_case": {"status": "structure_reference", "reasons": [], "unmet_conditions": ["adult_target_N_ge_30"]}, "comments": [{"user_key": "PRIVATE_USER", "profile_url": "PRIVATE_PROFILE", "bio": "PRIVATE_BIO"}]}], "owner_outcomes": [{"owner_id": "PRIVATE_OWNER"}], "cookie": "PRIVATE_COOKIE", "audience_aggregates": {"female_sample_count": 1, "interests": {"known": 1, "unknown": 0, "groups": [{"label": "运动", "count": 1, "sample_sufficient": False}]}}}


class ReportingTests(unittest.TestCase):
    def test_allowlist_excludes_personal_and_remote_fields(self):
        result = reporting.render_report(fixture())
        for secret in ("PRIVATE_TOKEN", "PRIVATE_USER", "PRIVATE_PROFILE", "PRIVATE_BIO", "PRIVATE_OWNER", "PRIVATE_COOKIE", "creator-secret", "remote.invalid"):
            self.assertNotIn(secret, result)
        self.assertNotIn('fetch(', result)
        self.assertIn("connect-src 'none'", result)

    def test_script_escape_preserves_json_and_cannot_terminate_script(self):
        source = fixture()
        source["notes"][0]["content"]["title"] = '<img src=x onerror="alert(2)">'
        result = reporting.render_report(source)
        match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', result, re.S)
        self.assertIsNotNone(match)
        parsed = json.loads(match[1])
        self.assertEqual(parsed["notes"][0]["content"]["text"], source["notes"][0]["content"]["text"])
        self.assertNotIn('<img src=x', result)
        self.assertNotIn('</script><script>alert', result)
        self.assertNotIn('innerHTML', result)

    def test_recomputes_denominators_and_preserves_missing_zero(self):
        source = fixture()
        m = reporting.public_data(source)["notes"][0]["metrics"]
        self.assertEqual(m["F_over_K"], .5)
        self.assertEqual(m["unknown_range"], [.25, .75])
        self.assertEqual(m["U_per_1000_fans_observed"], 80)
        self.assertIsNone(m["U_per_1000_fans_comparable"])
        source["notes"][0]["metrics"] = {"U": 0, "N": 0, "K": 0, "F": 0}
        source["notes"][0]["creator"]["fans"]["value"] = 0
        m = reporting.public_data(source)["notes"][0]["metrics"]
        self.assertEqual(m["U"], 0)
        self.assertIsNone(m["F_over_K"])
        self.assertIsNone(m["unknown_range"])
        self.assertIsNone(m["U_per_1000_fans_observed"])
        self.assertIsNone(m["adult_target"]["N"])

    def test_approximate_fans_cannot_be_recommended(self):
        source = fixture(); n = source["notes"][0]
        n["reference_case"]["status"] = "recommended"
        n["creator"]["fans"] = {"raw": "0.5万", "value": 5000, "precision": "approximate"}
        public = reporting.public_data(source)["notes"][0]
        self.assertEqual(public["reference_case"]["status"], "pending")
        self.assertFalse(public["creator"]["eligible_fans"])
        self.assertIsNone(public["creator"]["fans"]["value"])

    def test_zero_fans_remain_eligible_only_normalized_ratio_is_null(self):
        source = fixture()
        source["notes"][0]["creator"]["fans"] = {"raw": "0", "value": 0, "precision": "exact"}
        note = reporting.public_data(source)["notes"][0]
        self.assertTrue(note["creator"]["eligible_fans"])
        self.assertEqual(note["creator"]["fans"]["value"], 0)
        self.assertIsNone(note["metrics"]["U_per_1000_fans_observed"])

    def test_focused_policy_removes_excluded_details_and_retains_audit(self):
        source = fixture(); source['selection_policy'] = 'high_comment_female_case_study'; source['minimum_platform_comments'] = 50
        source['notes'][0]['screening'] = {'status': 'excluded', 'platform_comments': {'raw': '10', 'value': 10, 'precision': 'exact'}}
        excluded = json.loads(json.dumps(source['notes'][0])); excluded['note_id'] = 'excluded-detail'; excluded['content']['title'] = 'HIDDEN_LOW_COMMENT_TITLE'
        source['notes'] = [excluded, fixture()['notes'][0]]
        source['notes'][1]['screening'] = {'status': 'qualified', 'platform_comments': {'raw': '50', 'value': 50, 'precision': 'exact'}, 'observed_screen_sample': {'N': 8, 'K': 6, 'F': 4}}
        public = reporting.public_data(source)
        self.assertEqual(public['screen_counts'], {'qualified': 1, 'excluded': 1, 'pending': 0})
        self.assertEqual(len(public['notes']), 1)
        self.assertEqual(public['notes'][0]['screening']['observed_screen_sample']['F_over_K'], 4/6)
        self.assertNotIn('HIDDEN_LOW_COMMENT_TITLE', reporting.render_report(source))
        source.pop('selection_policy')
        self.assertEqual(len(reporting.public_data(source)['notes']), 2)

    def test_urls_reject_active_external_and_credential_sources(self):
        for url in ('javascript:alert(1)', 'http://www.xiaohongshu.com/a', 'https://xiaohongshu.com.evil.test/a', 'https://u:p@xiaohongshu.com/a', 'https://www.xiaohongshu.com:88/a', '//xiaohongshu.com/a', 'https://xiaohongshu.com/\nx'):
            self.assertEqual(reporting.safe_url(url), '')
        self.assertEqual(reporting.safe_url('https://www.xiaohongshu.com/explore/abc?secret=x#token'), 'https://www.xiaohongshu.com/explore/abc')

    def test_audience_small_cells_never_expose_count(self):
        group = reporting.public_data(fixture())["audience_aggregates"]["interests"]["groups"][0]
        self.assertIsNone(group["count"])

    def test_cli_writes_self_contained_empty_report(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory); source = root / 'analysis.json'; output = root / 'report.html'
            source.write_text(json.dumps({"source_mode": "real", "notes": []}), encoding='utf-8')
            reporting.main(['--analysis', str(source), '--output', str(output)])
            result = output.read_text(encoding='utf-8')
            self.assertIn('未验证：尚无真实帖子', result)
            self.assertNotIn('src="http', result)

    @unittest.skipUnless(shutil.which('node'), 'Existing Node unavailable; JS behavior test not executed')
    def test_javascript_filters_and_resource_matching(self):
        data = reporting.public_data(fixture()); data['source_mode'] = 'real'
        script = """
const assert=require('node:assert/strict');
const api=require(process.argv[1]);const data=JSON.parse(process.argv[2]);
assert.equal(api.filterCases(data.notes,{theme:'健身运动',fans:'0–500',subject:'人物+活动',composition:'中景',case_type:'structure_reference',interest:'sport',query:'测试'}).length,1);
assert.equal(api.filterCases(data.notes,{composition:'远景'}).length,0);
let p={interests:['sport','car','pet'],resources:['运动场地'],face:'no',hours:2};
let m=api.matchProfile(data,p);assert.equal(m.length,1);assert.equal(m[0].referenceCount,1);assert.ok(m[0].framing.includes('不强迫出镜'));
assert.equal(api.matchProfile({...data,source_mode:'synthetic'},p)[0].referenceCount,0);
assert.equal(api.matchProfile(data,{...p,hours:0.1}).length,0);
assert.equal(api.matchProfile(data,{...p,resources:[]}).length,0);
assert.equal(api.matchProfile(data,{...p,hours:'garbage'}).length,0);
data.notes[0].creator.eligible_fans=false;assert.equal(api.matchProfile(data,p)[0].referenceCount,0);
assert.ok(api.matchProfile(data,p)[0].status.includes('待验证假设'));
const all={interests:['sport','outdoor','cooking','photo','car','music','game','reading','pet'],resources:['运动场地','户外场景','居家厨房','摄影条件','已有车辆','音乐电影条件','游戏桌游条件','书籍展览条件','已有宠物'],hours:8,face:'hands'};
assert.equal(api.matchProfile(data,all).length,3);
const notice=api.screenNotice({selection_policy:'high_comment_female_case_study',minimum_platform_comments:50,screen_counts:{qualified:1,excluded:2,pending:3}});
assert.ok(notice.includes('评论 ≥50'));assert.ok(notice.includes('排除 2 篇'));assert.ok(notice.includes('不能作因果'));assert.ok(notice.includes('头像与昵称不作性别证据'));assert.equal(api.screenNotice(data),'');
"""
        result = subprocess.run([shutil.which('node'), '-e', script, str(reporting.ASSETS / 'report.js'), json.dumps(data)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
