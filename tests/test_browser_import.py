import json
import tempfile
import unittest
from pathlib import Path

from import_browser_captures import _coverage, age_evidence, convert, gender_evidence
from xhs_research.core import analyze, import_records


def row(kind, **fields):
    return {"kind":kind,"observed_at":"2026-10-14T02:00:00Z","_capture_file":"fixture.json","_capture_index":0,**fields}


class BrowserImportTests(unittest.TestCase):
    def test_author_tag_requires_explicit_review(self):
        records = [row("creator_dom", creator_id="a", stats=[]), row("note_dom", note_id="n", creator_id="a", tags=["薄肌男孩"])]
        self.assertEqual(convert(records)[0]["creators"][0]["gender"], [])
        records[1]["author_self_report_reviewed"] = True
        self.assertEqual(convert(records)[0]["creators"][0]["gender"][0]["value"], "male")

    def test_only_explicit_profile_evidence_and_age(self):
        self.assertEqual(gender_evidence({"gender":"female","gender_evidence":"avatar"}),[])
        self.assertEqual(gender_evidence({"gender":"female","gender_evidence":"公开资料 #female"})[0]["value"],"female")
        self.assertEqual(age_evidence({"tags_text":"大学生"}),[])
        self.assertEqual(age_evidence({"tags_text":"17岁"})[0]["value"],"minor")
        self.assertEqual(age_evidence({"tags_text":"212岁"}),[])
        self.assertEqual(age_evidence({"tags_text":"29岁"})[0]["value"],"25–34")

    def test_end_visible_alone_never_complete(self):
        note={"end_visible":True,"comments_raw":"12","comment_header":"共 12 条评论","comments":[{"id":"c"}],"expand_buttons":[]}
        self.assertEqual(_coverage(note)["status"],"partial")
        note["comments_raw"]="1"; note["comment_header"]="共 1 条评论"
        self.assertEqual(_coverage(note)["status"],"complete")
        note["expand_buttons"]=["展开 1 条回复"]
        self.assertEqual(_coverage(note)["status"],"partial")

    def test_real_bridge_reviewed_unknown_and_deep_sample(self):
        rows=[row("creator_dom",creator_id="a",name="a",stats=[{"label":"粉丝","raw":"0"}],gender_icon=["#male"]),row("note_dom",note_id="n",creator_id="a",title="test",desc="#健身",tags=["#健身"],type="normal",date_raw="09-01",comments_raw="50",comments=[{"id":f"c{i}","user_id":f"u{i}","level":1,"is_author":False,"content":"test"} for i in range(10)])]
        rows += [row("audience_dom",user_id=f"u{i}",reviewed=True,gender="female" if i%2 else None,gender_evidence="公开资料 #female" if i%2 else "",tags_text="19岁") for i in range(10)]
        envelope,audit=convert(rows)
        self.assertEqual(sum(c["profile_sampled"] for c in envelope["comments"]),10)
        self.assertEqual(sum(c["initial_screen_selected"] for c in envelope["comments"]),8)
        self.assertIsNone(envelope["notes"][0]["published_at"])
        self.assertEqual(envelope["notes"][0]["coverage"]["status"],"partial")
        self.assertEqual(envelope["creators"][0]["fans"]["value"],0)
        self.assertFalse(audit["inferred_comment_intents"])

    def test_case_aggregate_excludes_low_comment_and_unknown_creator(self):
        rows=[row("creator_dom",creator_id="a",stats=[{"label":"粉丝","raw":"10"}],gender_icon=["#male"])]
        for nid,total in [("qualified",50),("low",49)]:
            rows.append(row("note_dom",note_id=nid,creator_id="a",title="test",desc="#健身",tags=["#健身"],type="normal",comments_raw=str(total),comments=[{"id":f"{nid}{i}","user_id":f"{nid}{i}","level":1,"is_author":False} for i in range(8)]))
            rows += [row("audience_dom",user_id=f"{nid}{i}",reviewed=True,gender="female",gender_evidence="公开资料 #female",tags_text="29岁") for i in range(8)]
        envelope,_=convert(rows)
        scratch=Path(__file__).parent/".test_tmp"; scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            db=str(Path(temp)/"fixture.sqlite")
            import_records(db,[envelope])
            report=analyze(db,envelope["study_id"],"real")
            self.assertEqual(report["summary"]["screen_qualified_count"],1)
            self.assertEqual(report["notes"][0]["reference_case"]["status"], "pending")
            self.assertEqual(report["summary"]["screen_excluded_count"],1)
            self.assertEqual(report["audience_aggregates"]["female_sample_count"],8)
            self.assertIn("screen-qualified",report["audience_aggregates"]["scope"])

    def test_supplied_interest_annotations_survive_without_demographic_inference(self):
        rows = [row("note_dom", note_id="n", creator_id="a", comments=[{"id":"c", "user_id":"u", "level":1, "is_author":False}]),
                row("audience_dom", user_id="u", reviewed=True, desc="徒步爱好者", audience_annotations={
                    "interests":[{"value":"hiking", "basis":"public_self_report", "evidence":{"value":"徒步爱好者"}}],
                    "preferred_style":[{"value":"sporty", "basis":"avatar_guess", "evidence":"avatar"}],
                    "gender":[{"value":"female", "basis":"name_guess"}]})]
        envelope, _ = convert(rows)
        audience = envelope["comments"][0]["audience"]
        self.assertEqual(audience["interests"][0]["value"], "hiking")
        self.assertEqual(audience["preferred_style"], [])
        self.assertEqual(audience["gender"], [])
        rows[1].pop("audience_annotations")
        self.assertEqual(convert(rows)[0]["comments"][0]["audience"]["interests"], [])


if __name__ == "__main__":
    unittest.main()
