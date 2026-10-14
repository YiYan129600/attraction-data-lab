import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xhs_research.core import analyze, import_records, normalize_count, sample_users, select_candidates, validate_db, wilson_lower


def disclosure(value, basis="public_profile"):
    return {"value":value,"basis":basis,"evidence":{"source":"test_fixture","locator":"explicit self-report"}}


def envelope():
    return {"study_id":"study","owner_id":"owner","source_mode":"synthetic","observed_at":"2026-10-14T10:00:00+08:00","creators":[{"creator_id":"creator","name":"Fixture","url":"https://www.xiaohongshu.com/user/profile/creator","source":"test_fixture","fans":{"raw":"5000","value":5000,"precision":"exact"},"gender":[disclosure("male")]}],"notes":[{"note_id":"note","creator_id":"creator","url":"https://www.xiaohongshu.com/explore/note","title":"Fixture note","published_at":"2026-10-10T10:00:00+08:00","topics":["outdoor"],"format":"image","coverage":{"status":"complete","comparison_key":"chronological-all-v1"}}],"comments":[]}


def comment(cid, uid, gender=None, sampled=True, age=None, **extra):
    audience={"gender":[disclosure(gender)] if gender else [],"age_band":[disclosure(age)] if age else []}
    return {"comment_id":cid,"note_id":"note","user_id":uid,"level":1,"is_author":False,"profile_sampled":sampled,"intents":["shared_interest"],"interaction_target":"author","audience":audience,**extra}


class CoreTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parent / ".test_tmp"
        scratch.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=scratch)
        self.db=str(Path(self.tmp.name)/"test.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def run_data(self,data):
        import_records(self.db,[data])
        return analyze(self.db,"study","synthetic")

    def test_exact_boundary_rounded_zero_and_missing(self):
        for raw,value,precision,eligible in [("5000",5000,"exact",True),("5001",5001,"exact",False),("0.5万",5000,"exact",False),(None,None,"unknown",False),("0",0,"exact",True)]:
            with self.subTest(raw=raw):
                data=envelope(); data["creators"][0]["fans"]={"raw":raw,"value":value,"precision":precision}
                report=self.run_data(data)
                self.assertEqual(report["notes"][0]["creator"]["eligible_fans"],eligible)
        self.assertEqual(normalize_count("0",0,"exact"),{"raw":"0","value":0,"precision":"exact"})
        self.assertEqual(normalize_count("0.5万",5000,"exact")["precision"],"rounded")

    def test_u_n_duplicate_author_nested_and_unknown(self):
        data=envelope()
        data["comments"]=[comment("c1","u1","female"),comment("c2","u1","female"),comment("c3","u2",None),comment("c4","u3",None,False),comment("c5","author","female",is_author=True),comment("c6","u4","female",level=2,parent_id="c1"),comment("c7",None,"female")]
        metrics=self.run_data(data)["notes"][0]["metrics"]
        self.assertEqual((metrics["U"],metrics["N"],metrics["K"],metrics["F"]),(3,2,1,1))
        self.assertEqual(metrics["author_reply_count"],1)
        self.assertEqual(metrics["nested_comment_count"],1)
        self.assertEqual(metrics["unknown_range"],[.5,1])
        self.assertIsNone(metrics["female_effective_per_1000_fans"])
        self.assertFalse(metrics["pool_comparable"])

    def test_zero_denominators_null(self):
        metrics=self.run_data(envelope())["notes"][0]["metrics"]
        for key in ("F_over_K","K_over_N","F_over_N","unknown_range","wilson_95_lower"):
            self.assertIsNone(metrics[key])
        self.assertIsNone(wilson_lower(0,0))

    def test_minor_excluded_and_age_unknown_separate(self):
        data=envelope(); data["comments"]=[comment("m","m","female",age="minor"),comment("a","a","female",age="18–24"),comment("u","u","female")]
        report=self.run_data(data); metrics=report["notes"][0]["metrics"]
        self.assertEqual((metrics["N"],metrics["F"]),(3,3))
        self.assertEqual((metrics["adult_target"]["N"],metrics["adult_target"]["F"]),(2,2))
        self.assertEqual(metrics["adult_target"]["confirmed_adult_female"],1)
        self.assertEqual(metrics["adult_target"]["female_age_unknown"],1)
        self.assertEqual(report["audience_aggregates"]["female_sample_count"],2)

    def test_inference_conflict_no_gender_evidence(self):
        data=envelope(); first=comment("c1","u1","female"); first["audience"]["gender"].append(disclosure("male"))
        inferred=comment("c2","u2","female"); inferred["audience"]["gender"][0]["basis"]="avatar_inference"
        data["comments"]=[first,inferred]
        metrics=self.run_data(data)["notes"][0]["metrics"]
        self.assertEqual((metrics["N"],metrics["K"],metrics["F"]),(2,0,0))

    def test_effective_intents_and_other_commenter_excluded(self):
        data=envelope(); data["comments"]=[comment("good","g","female"),comment("ad","a","female",intents=["shared_interest","advertisement"]),comment("argue","x","female",interaction_target="other_commenter"),comment("attack","z","female",intents=["person_appreciation","attack"])]
        self.assertEqual(self.run_data(data)["notes"][0]["metrics"]["adult_target"]["F_effective"],1)

    def test_modes_isolated_and_hmac_scoped_private(self):
        data=envelope(); data["comments"]=[comment("c","private-raw-user","female")]
        self.run_data(data)
        real=copy.deepcopy(data); real["source_mode"]="real"; real["comments"]=[]
        import_records(self.db,[real])
        self.assertEqual(analyze(self.db,"study","real")["notes"][0]["metrics"]["U"],0)
        exported=json.dumps(analyze(self.db,"study","synthetic"))
        self.assertNotIn("private-raw-user",exported); self.assertNotIn("user_key",exported)
        with sqlite3.connect(self.db) as db:
            payload=db.execute("SELECT payload FROM snapshots WHERE entity='comments'").fetchone()[0]
        db.close()
        self.assertNotIn("private-raw-user",payload)
        self.assertEqual(len(json.loads(payload)["user_key"]),64)

    def test_idempotent_and_preserve_observation_snapshots(self):
        data=envelope(); data["comments"]=[comment("c","u","female")]
        first=import_records(self.db,[data]); duplicate=import_records(self.db,[data])
        self.assertEqual(first["inserted"]["comments"],1); self.assertEqual(duplicate["inserted"],{})
        new=copy.deepcopy(data); new["observed_at"]="2026-10-15T10:00:00+08:00"
        import_records(self.db,[new])
        report=analyze(self.db,"study","synthetic")
        self.assertEqual(report["data_quality"]["snapshots"]["comments"],2)
        self.assertEqual(report["notes"][0]["metrics"]["U"],1)

    def test_later_fans_dont_change_earlier_note(self):
        data=envelope(); self.run_data(data)
        later={"study_id":"study","owner_id":"owner","source_mode":"synthetic","observed_at":"2026-10-16T10:00:00+08:00","creators":[{"creator_id":"creator","fans":{"raw":"6000","precision":"exact"}}]}
        import_records(self.db,[later])
        report=analyze(self.db,"study","synthetic")
        self.assertEqual(report["notes"][0]["creator"]["fans"]["value"],5000)

    def test_partial_profile_no_full_female_normalization(self):
        data=envelope(); data["comments"]=[comment("1","1","female"),comment("2","2",sampled=False)]
        metrics=self.run_data(data)["notes"][0]["metrics"]
        self.assertTrue(metrics["pool_comparable"])
        self.assertIsNone(metrics["female_effective_per_1000_fans"])
        self.assertEqual(metrics["adult_target"]["F_effective"],1)
        self.assertNotIn("estimated_total_female",metrics)

    def test_truncated_pool_no_comparison(self):
        data=envelope(); data["notes"][0]["coverage"]={"status":"partial","limit":10,"stop_reason":"top_comments_only","comparison_key":"hot-v1"}
        data["comments"]=[comment("1","1","female")]
        note=self.run_data(data)["notes"][0]
        self.assertIsNone(note["metrics"]["U_per_1000_fans_comparable"])
        self.assertEqual(note["reference_case"]["status"],"structure_reference")

    def test_single_note_never_recommended_even_all_female(self):
        data=envelope(); data["comments"]=[comment(str(i),str(i),"female",age="25–34") for i in range(35)]
        note=self.run_data(data)["notes"][0]
        self.assertGreater(note["metrics"]["adult_target"]["wilson_95_lower"],.5)
        self.assertNotEqual(note["reference_case"]["status"],"recommended")
        self.assertIn("matched_group_top_quartile",note["reference_case"]["unmet_conditions"])

    def test_matched_group_recommend_and_author_cap(self):
        data=envelope(); data["notes"]=[]; data["comments"]=[]; data["creators"]=[]
        for i in range(4):
            base=envelope(); creator=base["creators"][0]; creator["creator_id"]=f"a{i}"; data["creators"].append(creator)
            note=base["notes"][0]; note["note_id"]=f"n{i}"; note["creator_id"]=f"a{i}"; data["notes"].append(note)
            for j in range(40 if i==3 else i+1):
                c=comment(f"c{i}-{j}",f"u{i}-{j}","female",age="25–34"); c["note_id"]=f"n{i}"; data["comments"].append(c)
        report=self.run_data(data)
        self.assertEqual(report["notes"][3]["reference_case"]["status"],"recommended")
        self.assertTrue(report["comparison_groups"][0]["sample_sufficient"])
        one=envelope(); one["notes"]=[{**one["notes"][0],"note_id":f"extra{i}"} for i in range(5)]
        import_records(self.db,[one])
        report=analyze(self.db,"study","synthetic")
        self.assertEqual(sum(n["main_sample"] for n in report["notes"] if n["creator"]["creator_id"]=="creator"),3)

    def test_owner_outcomes_private_and_efficiency(self):
        data=envelope(); data["outcomes"]=[{"outcome_id":"out","note_id":"note","production_minutes":120,"reads":1000,"reads_basis":"owner_dashboard_7d","reads_source":"authorized_export","female_effective_commenters":4,"female_initiated_dms":2,"attribution":"explicit_note","dm_initiation_rule":"new_female_initiated_not_previously_contacted","gender_evidence_basis":"public_or_authorized_explicit","minor_excluded":True}]
        report=self.run_data(data); self.assertNotIn("owner_outcomes",report)
        private=analyze(self.db,"study","synthetic","owner")["owner_outcomes"]["records"][0]
        self.assertEqual(private["female_commenters_per_hour"],2)
        self.assertEqual(private["attributed_female_dms_per_1000_reads"],2)
        other=copy.deepcopy(data); other["owner_id"]="other"; import_records(self.db,[other])
        with self.assertRaisesRegex(ValueError,"owner"):
            analyze(self.db,"study","synthetic")
        self.assertEqual(len(analyze(self.db,"study","synthetic","owner")["owner_outcomes"]["records"]),1)

    def test_selection_and_profile_sample_reproducible(self):
        data=envelope(); data["comments"]=[comment(str(i),str(i),sampled=False) for i in range(15)]
        self.run_data(data)
        a=sample_users(self.db,"study","note","synthetic",5,"seed")
        self.assertEqual(a,sample_users(self.db,"study","note","synthetic",5,"seed"))
        self.assertEqual(a["U"],15); self.assertEqual(len(a["selected_user_keys"]),5)
        self.assertEqual(analyze(self.db,"study","synthetic")["notes"][0]["metrics"]["N"],0)
        self.assertEqual(len(select_candidates(self.db,"study","synthetic",24)["selected"]),1)

    def test_sensitive_and_invalid_mode_atomic(self):
        data=envelope(); data["cookie"]="secret"
        with self.assertRaisesRegex(ValueError,"sensitive"):
            import_records(self.db,[data])
        data=envelope(); data["source_mode"]="mixed"
        with self.assertRaisesRegex(ValueError,"source_mode"):
            import_records(self.db,[data])

    def test_validate_references_and_visual_evidence(self):
        data=envelope(); data["notes"][0]["visual_labels"]=[{"label":"center"}]
        self.run_data(data)
        self.assertFalse(validate_db(self.db,"study","synthetic")["valid"])

    def test_cli_jsonl_key_path(self):
        source=Path(self.tmp.name)/"input.jsonl"; source.write_text(json.dumps(envelope()),encoding="utf-8")
        output=Path(self.tmp.name)/"report.json"
        for command in (["init","--db",self.db],["import","--db",self.db,"--input",str(source)],["analyze","--db",self.db,"--study","study","--source-mode","synthetic","--output",str(output)],["validate","--db",self.db,"--study","study","--source-mode","synthetic"]):
            result=subprocess.run([sys.executable,"-m","xhs_research",*command],capture_output=True,text=True,encoding="utf-8")
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["source_mode"],"synthetic")

    def test_same_id_and_profile_url_deduplicate(self):
        data=envelope(); via_url=comment("c2",None,"female"); via_url["user_profile_url"]="https://www.xiaohongshu.com/user/profile/u?xsec_token=NOT_SAVED"
        data["comments"]=[comment("c1","u","female"),via_url]
        metrics=self.run_data(data)["notes"][0]["metrics"]
        self.assertEqual((metrics["U"],metrics["N"]),(1,1))

    def test_inconsistent_count_and_identity_rejected(self):
        with self.assertRaisesRegex(ValueError,"disagree"):
            normalize_count("5001",5000,"exact")
        data=envelope(); c=comment("c","u"); c["user_profile_url"]="https://www.xiaohongshu.com/user/profile/other"; data["comments"]=[c]
        with self.assertRaisesRegex(ValueError,"different users"):
            self.run_data(data)

    def test_interest_strength_and_style_not_conflated(self):
        data=envelope(); c=comment("c","u","female"); c["audience"]["interests"]=[disclosure("running","single_topic")]; c["audience"]["presented_style"]=[disclosure("sporty","public_presentation")]; data["comments"]=[c]
        aggregate=self.run_data(data)["audience_aggregates"]
        self.assertEqual(aggregate["interests"]["known"],0)
        self.assertEqual(aggregate["topic_interests"]["known"],1)
        self.assertEqual(aggregate["presented_style"]["known"],1)
        self.assertEqual(aggregate["preferred_style"]["known"],0)

    def test_exact_zero_fans_eligible_but_ratios_null(self):
        data=envelope(); data["creators"][0]["fans"]={"raw":"0","value":0,"precision":"exact"}; data["comments"]=[comment("c","u","female")]
        note=self.run_data(data)["notes"][0]
        self.assertEqual(note["creator"]["fans"]["value"],0)
        self.assertEqual(note["creator"]["fans"]["precision"],"exact")
        self.assertTrue(note["creator"]["eligible_fans"])
        self.assertEqual(note["creator"]["fans_band"],"0–500")
        for metric in ("U_per_1000_fans_observed","U_per_1000_fans_comparable","female_effective_per_1000_fans"):
            self.assertIsNone(note["metrics"][metric])

    def test_zero_fans_group_keeps_u_comparison_without_ratio(self):
        data=envelope(); data["creators"]=[]; data["notes"]=[]; data["comments"]=[]
        for i in range(4):
            template=envelope(); creator=template["creators"][0]; creator["creator_id"]=f"a{i}"; creator["fans"]={"raw":"0","value":0,"precision":"exact"}; data["creators"].append(creator)
            note=template["notes"][0]; note["note_id"]=f"n{i}"; note["creator_id"]=f"a{i}"; data["notes"].append(note)
            for j in range(i+1):
                c=comment(f"c{i}-{j}",f"u{i}-{j}","female"); c["note_id"]=f"n{i}"; data["comments"].append(c)
        report=self.run_data(data)
        self.assertEqual(report["comparison_groups"][0]["notes"],4)
        self.assertTrue(report["notes"][3]["comparison"]["high_interaction_supported"])
        self.assertIsNone(report["notes"][3]["comparison"]["normalized_percentile"])

    def test_author_note_self_report_tag_accepted_theme_not_gender(self):
        data=envelope(); data["creators"][0]["gender"]=[{"value":"male","basis":"public_self_report","evidence":{"source":"author_note_tag","note_id":"note","locator":"platform_tags","value":"#薄肌男孩"}}]
        data["notes"][0]["platform_tags"]=["薄肌男孩"]
        self.assertEqual(self.run_data(data)["notes"][0]["creator_gender_status"],"male")
        data["creators"][0]["gender"]=[]
        data["observed_at"]="2026-10-15T10:00:00+08:00"
        self.assertEqual(self.run_data(data)["notes"][0]["creator_gender_status"],"unknown")


if __name__=="__main__":
    unittest.main()
