import copy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from xhs_research.collector.config import Config, load_config
from xhs_research.collector.normalize import clean, envelope, merge_comment, rest_profile, rest_note, classify, metrics
from xhs_research.collector.providers import ReplayProvider, RestProvider, ProviderError
from xhs_research.collector.runner import Runner, BudgetStop
from xhs_research.collector.state import State, service_lock
from xhs_research.core import analyze, validate_db


def captures():
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    def row(kind, **kw):
        return {"kind": kind, "observed_at": stamp, **kw}
    rows = [row("creator_dom", creator_id="c", stats=[{"label": "粉丝", "raw": "100"}], gender_icon=["#male"])]
    for i in range(2):
        nid = f"{i + 100:024x}"
        rows.append(row("note_dom", note_id=nid, creator_id="c", title="synthetic", desc="fixture", tags=[], type="normal", comments_raw="100", query="男生健身", comments=[{"id": f"{i}-comment-{j}", "user_id": f"user-{j}", "level": 1, "is_author": False, "content": "fixture"} for j in range(30)]))
    rows += [row("audience_dom", user_id=f"user-{j}", reviewed=True, gender="female" if j < 8 else "male", gender_evidence="#female" if j < 8 else "#male", tags_text="26岁") for j in range(30)]
    return rows


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config("study", "owner", source_mode="synthetic", state_root=str(self.root / "private"), queries={"fitness": "男生健身"}, request_interval=0, max_candidates=2).validate()
        self.capture = self.root / "capture-test.json"
        self.capture.write_text(json.dumps(captures()), encoding="utf-8")
        self.states = []

    def make_state(self, config, run, **kw):
        state = State(config, run, **kw)
        self.states.append(state)
        return state

    def tearDown(self):
        for state in self.states:
            state.close()
        self.tmp.cleanup()

    def test_full_replay_frozen_samples_flip_cache_and_repeat(self):
        provider = ReplayProvider(self.config, self.capture)
        state = self.make_state(self.config, "run1")
        result = Runner(self.config, state, provider).run()
        self.assertEqual(result["screen_completed"], 2)
        self.assertEqual(result["deep_completed"], 2)
        for task in state.tasks("screen"):
            self.assertEqual(task["data"]["cohort"], "positive")
            self.assertEqual(len(task["data"]["selected"]), 8)
        for task in state.tasks("deep"):
            self.assertEqual(task["data"]["cohort"], "counterexample")
            self.assertEqual(len(task["data"]["selected"]), 30)
        profile_calls = [e for e in state.events("provider_call") if e["action"] == "profile"]
        self.assertEqual(len(profile_calls), 31)  # one author + 30 shared audience
        self.assertTrue(validate_db(str(self.config.database), "study", "synthetic")["valid"])
        before = analyze(str(self.config.database), "study", "synthetic")["data_quality"]["current_records"]
        state.close()
        second = self.make_state(self.config, "run2")
        Runner(self.config, second, ReplayProvider(self.config, self.capture)).run()
        after = analyze(str(self.config.database), "study", "synthetic")["data_quality"]["current_records"]
        self.assertEqual(before, after)
        self.assertEqual(sum(e["action"] == "profile" for e in second.events("provider_call")), 0)
        self.assertTrue((second.run_dir / "all-cases.html").exists())
        summary = json.loads((second.run_dir / "run-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["new_sufficient_deep_notes"], 0)
        self.assertIsNone(summary["visits_per_new_sufficient_deep_note"])
        second.close()

    def test_crash_recover_both_database_gaps(self):
        provider = ReplayProvider(self.config, self.capture)
        data = envelope(self.config, creators=provider.envelope["creators"], notes=provider.envelope["notes"], comments=provider.envelope["comments"])
        for point in ("after_write", "after_import"):
            cfg = replace(self.config, study_id=point)
            data = {**data, "study_id": point}
            state = self.make_state(cfg, point)
            def crash(where):
                if where == point:
                    raise RuntimeError("simulated interruption")
            with self.assertRaises(RuntimeError):
                state.persist(data, crash=crash)
            self.assertEqual(state.recover(), 1)
            self.assertEqual(state.recover(), 0)
            self.assertEqual(analyze(str(cfg.database), point, "synthetic")["data_quality"]["current_records"]["comments"], 60)
            state.close()

    def test_budget_survives_resume_no_further_calls(self):
        cfg = replace(self.config, max_requests=3)
        state = self.make_state(cfg, "limited")
        result = Runner(cfg, state, ReplayProvider(cfg, self.capture)).run()
        self.assertEqual(result["stop_reason"], "max_requests")
        self.assertEqual(result["provider_calls"], 3)
        state.close()
        state = self.make_state(cfg, "limited", create=False)
        result = Runner(cfg, state, ReplayProvider(cfg, self.capture)).run()
        self.assertEqual(result["provider_calls"], 3)
        state.close()

    def test_service_lock_cross_study_and_release(self):
        cfg = replace(self.config, provider="xhs-rest")
        other = replace(cfg, study_id="different")
        with service_lock(cfg):
            with self.assertRaises(ValueError):
                with service_lock(other):
                    pass
        with service_lock(other):
            pass

    def test_defaults_do_not_read_credentials_or_network(self):
        cfg = replace(self.config, provider="xhs-rest", credentials_file="missing-private-credential")
        with patch("urllib.request.build_opener", side_effect=AssertionError("network")):
            with self.assertRaises(ValueError):
                RestProvider(cfg)

    def test_namespace_and_resume_configuration(self):
        self.assertNotEqual(self.config.scope, replace(self.config, owner_id="other").scope)
        state = self.make_state(self.config, "scope")
        with self.assertRaises(ValueError):
            state.persist(envelope(replace(self.config, source_mode="real")))
        state.close()
        with self.assertRaises(ValueError):
            State(replace(self.config, max_requests=999), "scope", create=False)

    def test_comment_update_invalidates_intent_not_identity(self):
        old = {"comment_id": "c", "content": "old", "profile_sampled": True, "audience": {"gender": []}, "intents": ["shared_interest"]}
        new = merge_comment(old, {"comment_id": "c", "content": "new", "profile_sampled": False})
        self.assertEqual(new["intents"], [])
        self.assertEqual(new["comment_id"], "c")
        self.assertEqual(new["previous_annotation"]["intents"], ["shared_interest"])

    def test_secret_scrubbing(self):
        value = clean({"xsecToken": "DO_NOT_SAVE", "authorization": "DO_NOT_SAVE", "text": "https://www.xiaohongshu.com/explore/abc?xsec_token=DO_NOT_SAVE"})
        self.assertNotIn("DO_NOT_SAVE", json.dumps(value))

    def test_unique_new_ids_not_snapshot_rows_and_stable_arrival_order(self):
        provider = ReplayProvider(self.config, self.capture)
        state = self.make_state(self.config, "identity-order")
        runner = Runner(self.config, state, provider)
        comments = provider.envelope["comments"][:3]
        runner.persist(envelope(self.config, creators=provider.envelope["creators"], notes=provider.envelope["notes"], comments=comments + [comments[0]]))
        self.assertEqual(state.events("import")[-1]["new_unique"]["comments"], 3)
        updated = {**comments[0], "content": "new"}
        runner.persist(envelope(self.config, comments=[updated]))
        self.assertEqual([c["comment_id"] for c in state.records("comments")], [c["comment_id"] for c in comments])

    def test_shorter_response_and_identity_change(self):
        old = {"comment_id": "c", "user_id": "one", "content": "same", "profile_sampled": True, "audience": {"gender": [{"value": "female"}]}}
        new = merge_comment(old, {"comment_id": "c", "user_id": "two", "content": "same"})
        self.assertFalse(new["profile_sampled"])
        self.assertEqual(new["audience"], {})
        failed = merge_comment(old, {**old, "profile_sampled": False, "audience": {}, "profile_check_status": "unavailable"})
        self.assertFalse(failed["profile_sampled"])

    def test_cache_ttl_unknown_failure_future(self):
        state = self.make_state(self.config, "ttl")
        runner = Runner(self.config, state, ReplayProvider(self.config, self.capture))
        current = datetime.now(timezone.utc)
        p = {"reviewed": True, "observed_at": (current - timedelta(hours=25)).isoformat(), "audience": {}}
        self.assertFalse(runner.fresh(p))
        p["audience"] = {"gender": [{"value": "female", "basis": "public_profile", "evidence": {"source": "fixture"}}]}
        self.assertTrue(runner.fresh(p))
        self.assertFalse(runner.fresh({**p, "reviewed": False}))
        self.assertFalse(runner.fresh({**p, "observed_at": (current + timedelta(seconds=10)).isoformat()}))

    def test_audit_reserved_and_author_cap(self):
        cfg = replace(self.config, audit_fraction=1, max_deep_notes=2)
        state = self.make_state(cfg, "audit")
        for i, cohort in enumerate(["positive", "positive", "positive", "unknown"]):
            tid = state.add_task("screen", str(i), creator_id="same", theme="fitness", cohort=cohort, selected=[])
            state.set_task(tid, "done")
        Runner(cfg, state, ReplayProvider(cfg, self.capture)).schedule_deep()
        deep = state.tasks("deep")
        self.assertEqual(len(deep), 2)
        self.assertIn("fixed_audit", [t["data"]["selection_reason"] for t in deep])
        before = [(t["id"], t["data"]) for t in deep]
        Runner(cfg, state, ReplayProvider(cfg, self.capture)).schedule_deep()
        self.assertEqual(before, [(t["id"], t["data"]) for t in state.tasks("deep")])

    def test_profile_and_wall_budget_prevents_calls(self):
        cfg = replace(self.config, max_profile_visits=0)
        state = self.make_state(cfg, "profile-limit")
        runner = Runner(cfg, state, ReplayProvider(cfg, self.capture))
        with self.assertRaises(BudgetStop):
            runner.request("profile", {"user_id": "c"})
        self.assertEqual(state.events("provider_call"), [])
        clock = [0]
        runner = Runner(cfg, state, ReplayProvider(cfg, self.capture), clock=lambda: clock[0])
        clock[0] = cfg.max_seconds + 1
        with self.assertRaises(BudgetStop):
            runner.request("probe")
        self.assertEqual(state.events("provider_call"), [])

    def test_same_cached_profile_does_not_duplicate_evidence(self):
        state = self.make_state(self.config, "cache-delta")
        runner = Runner(self.config, state, ReplayProvider(self.config, self.capture))
        runner.profile("c", author=True)
        count = len(list((state.run_dir / "evidence").glob("*.json")))
        runner.profile("c", author=True)
        self.assertEqual(len(list((state.run_dir / "evidence").glob("*.json"))), count)

    def test_rest_default_integer_gender_never_disclosure(self):
        raw = {"data": {"userBasicInfo": {"gender": 0}, "interactions": [{"type": "fans", "count": "0.5万"}]}}
        normalized = rest_profile(raw, "user", "2026-10-14T00:00:00Z")
        self.assertEqual(normalized["audience"]["gender"], [])
        self.assertEqual(normalized["creator"]["fans"]["precision"], "rounded")
        raw["data"]["userBasicInfo"]["user_id"] = "wrong"
        with self.assertRaises(ValueError):
            rest_profile(raw, "user", "2026-10-14T00:00:00Z")

    def test_unknown_and_minor_do_not_get_replaced(self):
        profile = lambda v: [{"value": v, "basis": "public_profile", "evidence": {"source": "fixture"}}]
        comments = [{"comment_id": str(i), "user_id": str(i), "level": 1, "is_author": False, "profile_sampled": True,
                     "audience": {"gender": profile("female"), "age_band": profile("minor") if i == 0 else []}} for i in range(8)]
        sample = metrics(comments)
        self.assertEqual(sample["N"], 7)
        self.assertEqual(classify(sample), "unknown")


if __name__ == "__main__":
    unittest.main()
