import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import community as c


def analysis(mode="synthetic"):
    return {"source_mode": mode, "selection_policy": "high_comment_female_case_study",
            "cookie": "PRIVATE_COOKIE", "owner_outcomes": [{"private_message_text": "PRIVATE_DM"}],
            "notes": [{"url": "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01?xsec_token=SECRET#PRIVATE",
                       "observed_at": "2026-10-14T12:30:00+08:00",
                       "creator": {"name": "PRIVATE_NAME", "avatar": "PRIVATE_AVATAR", "fans": {"value": 100, "precision": "exact", "raw": "PRIVATE_RAW", "evidence": "PRIVATE_EVIDENCE"}},
                       "platform_counts": {"comments": {"value": 42, "precision": "exact"}},
                       "metrics": {"observed_comment_count": 24, "N": 99, "K": 99, "F": 99, "adult_target": {"N": 20, "K": 18, "F": 13}},
                       "coverage": {"status": "partial", "stop_reason": "PRIVATE_REASON"},
                       "comments": [{"user_id": "PRIVATE_ID", "text": "PRIVATE_COMMENT"}],
                       "content": {"title": "PRIVATE_TITLE", "text": "PRIVATE_TEXT", "topics": ["健身运动"], "platform_tags": ["PRIVATE_TAG"]},
                       "visual_labels": [{"dimension": "subject", "value": "人物+活动", "evidence": "PRIVATE_EVIDENCE"}, {"dimension": "composition", "value": "中景"}]}]}


def package(identifier="test", mode="synthetic"):
    return c.export_analysis(analysis(mode), identifier)


def seal(value):
    value["checksum"] = c.checksum(value)
    return value


class CommunityTests(unittest.TestCase):
    def test_only_reviewed_cover_labels_export(self):
        value = analysis()
        label = value["notes"][0]["visual_labels"][0]
        label.update(status="reviewed", role="cover")
        self.assertEqual(c.export_analysis(value, "reviewed")["records"][0]["subject"], "person_activity")
        label["role"] = "later_image"
        self.assertEqual(c.export_analysis(value, "later")["records"][0]["subject"], "unknown")
        label.update(role="cover", status="unreviewed")
        self.assertEqual(c.export_analysis(value, "unreviewed")["records"][0]["subject"], "unknown")

    def test_privacy_allowlist_and_actual_mapping(self):
        value = package()
        text = c.canonical(value)
        for marker in ("PRIVATE", "SECRET", "cookie", "user_id", "avatar", "evidence\"", "text\"", "raw\"", "owner"):
            self.assertNotIn(marker, text)
        self.assertEqual(set(value), c.ROOT_KEYS)
        record = value["records"][0]
        self.assertEqual(set(record), c.RECORD_KEYS | c.OPTIONAL_KEYS)
        self.assertEqual((record["N"], record["K"], record["F"]), (20, 18, 13))
        self.assertEqual(record["captured_count"], 24)
        self.assertEqual(record["topic"], "fitness")
        self.assertEqual(record["subject"], "unknown")
        self.assertEqual(record["composition"], "unknown")
        self.assertEqual(record["source_url"], "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01")

    def test_unknown_is_null_no_guessed_counts_or_topics(self):
        value = analysis()
        note = value["notes"][0]
        note["metrics"] = {}
        note["creator"] = {}
        note["platform_counts"] = {}
        note["content"]["topics"] = ["unrecognized PRIVATE topic"]
        note["visual_labels"] = []
        record = c.export_analysis(value, "unknown")["records"][0]
        for field in ("N", "K", "F", "captured_count", "platform_comment_count"):
            self.assertIsNone(record[field])
        self.assertEqual(record["fans"], {"value": None, "precision": "unknown"})
        self.assertEqual(record["topic"], "unknown")
        self.assertEqual(c.classify(record), "unknown")

    def test_rounded_platform_counts_keep_precision(self):
        value = analysis()
        value["notes"][0]["platform_counts"]["comments"] = {"value": 12000, "precision": "rounded", "raw": "1.2万"}
        record = c.export_analysis(value, "rounded")["records"][0]
        self.assertEqual(record["platform_comment_count"], 12000)
        self.assertEqual(record["platform_comment_precision"], "rounded")

    def test_actual_topic_alias_and_no_source_mode_guess(self):
        value = analysis()
        value["notes"][0]["content"]["topics"] = ["fitness_progress"]
        self.assertEqual(c.export_analysis(value, "alias")["records"][0]["topic"], "fitness")
        del value["source_mode"]
        with self.assertRaises(ValueError):
            c.export_analysis(value, "unknown-source")

    def test_record_limit_and_required_keys(self):
        value = package()
        value["records"] = value["records"] * (c.MAX_RECORDS + 1)
        with self.assertRaises(ValueError):
            c.validate(seal(value))
        value = package()
        del value["records"][0]["captured_count"]
        with self.assertRaises(ValueError):
            c.validate(seal(value))

    def test_source_url_rejects_wrong_origin_or_illegal_id(self):
        for url in ("https://evil.invalid/explore/abc", "https://www.xiaohongshu.com/explore/nonhex", "https://a@www.xiaohongshu.com/explore/abc", "https://www.xiaohongshu.com:443/explore/abc", "javascript:alert(1)", "https://www.xiaohongshu.com/explore/abc/extra"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                value = analysis()
                value["notes"][0]["url"] = url
                c.export_analysis(value, "bad")

    def test_additional_fields_rejected_every_level(self):
        for location in ("root", "record", "fans"):
            value = package()
            target = value if location == "root" else value["records"][0] if location == "record" else value["records"][0]["fans"]
            target["comments"] = "PRIVATE_COMMENT"
            with self.subTest(location=location), self.assertRaises(ValueError):
                c.validate(seal(value))

    def test_invalid_bounds_types_dates_enums_checksum(self):
        cases = {"F": [19, -1, True, 1.0, float("inf")], "N": [None, c.MAX_COUNT + 1],
                 "observed_at": ["2026-10-14T12:00:00", "2026-02-30T00:00:00Z", "yesterday"],
                 "coverage": ["full", {}], "gender_evidence_method": ["avatar_inference"],
                 "captured_count": [1], "subject": ["arbitrary free text"],
                 "topic": ["PRIVATE_TOPIC"], "source_url": ["https://www.xiaohongshu.com/explore/abc?cookie=secret"]}
        for field, invalids in cases.items():
            for invalid in invalids:
                value = package()
                value["records"][0][field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises((ValueError, TypeError)):
                    c.validate(seal(value))
        value = package()
        value["checksum"] = "0" * 64
        with self.assertRaises(ValueError):
            c.validate(value)

    def test_unknown_precision_requires_null(self):
        for field in ("fans", "platform"):
            value = package()
            if field == "fans":
                value["records"][0]["fans"]["precision"] = "unknown"
            else:
                value["records"][0]["platform_comment_precision"] = "unknown"
            with self.assertRaises(ValueError):
                c.validate(seal(value))

    def test_duplicate_observations_rejected(self):
        value = package()
        duplicate = copy.deepcopy(value["records"][0])
        duplicate["observed_at"] = "2026-10-14T04:30:00Z"
        value["records"].append(duplicate)
        with self.assertRaises(ValueError):
            c.validate(seal(value))

    def test_screens_preserve_positive_counterexample_unknown(self):
        value = analysis()
        base = value["notes"][0]
        for i, metrics in enumerate(({"N": 20, "K": 18, "F": 1}, {"N": 7, "K": 7, "F": 7}), 2):
            note = copy.deepcopy(base)
            note["url"] = f"https://www.xiaohongshu.com/explore/{i:024x}"
            note["metrics"]["adult_target"] = metrics
            value["notes"].append(note)
        result = c.export_analysis(value, "all-cases")
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual({c.classify(r) for r in result["records"]}, {"positive", "counterexample", "unknown"})

    def test_merge_idempotency_conflict_isolation_rebuild(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            a, b, changed = folder / "a.json", folder / "b.json", folder / "changed.json"
            a.write_text(c.canonical(package("study-a", "real")), encoding="utf-8")
            b.write_text(c.canonical(package("study-b")), encoding="utf-8")
            db, page = folder / "output.sqlite", folder / "output.html"
            self.assertEqual(c.merge([a, a, b], db, page), {"datasets": 2, "observations": 2})
            previous_page = page.read_text(encoding="utf-8")
            self.assertEqual(c.merge([b, a], db, page), {"datasets": 2, "observations": 2})
            self.assertEqual(previous_page, page.read_text(encoding="utf-8"))
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT source_mode FROM datasets ORDER BY dataset_id").fetchall(), [("real",), ("synthetic",)])
            finally:
                conn.close()
            value = package("study-a", "real")
            value["records"][0]["F"] = 2
            changed.write_text(c.canonical(seal(value)), encoding="utf-8")
            with self.assertRaises(ValueError):
                c.merge([a, changed], db, page)
            self.assertEqual(page.read_text(encoding="utf-8"), previous_page)
            # Database contents are never used as import data.
            db.write_bytes(b"not a trusted database")
            c.merge([a], db, page)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1)
            finally:
                conn.close()
            with self.assertRaises(ValueError):
                c.merge([db], folder / "other.sqlite", folder / "other.html")

    def test_html_escapes_and_contains_no_raw_data(self):
        value = package()
        page = c.render_html([value])
        self.assertNotIn("PRIVATE", page)
        self.assertNotIn("SECRET", page)
        self.assertIn("Study: test", page)
        self.assertIn("positive", page)
        value["dataset_id"] = '<script>alert("X")</script>'
        self.assertNotIn("<script>", c.render_html([value]))
        self.assertIn("&lt;script&gt;", c.render_html([value]))

    def test_reader_limits_duplicate_keys_nonfinite_and_nonjson(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'SQLite format 3\x00', b'[' * 3000):
                path.write_bytes(data)
                with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                    c.read_json(path)
            with path.open("wb") as stream:
                stream.truncate(c.MAX_BYTES + 1)
            with self.assertRaises(ValueError):
                c.read_json(path)

    def test_cli_export_validate_merge_and_redacted_error(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            private, exported = folder / "private.json", folder / "aggregate.json"
            private.write_text(json.dumps(analysis()), encoding="utf-8")
            script = str(Path(c.__file__))
            commands = (["export", "--analysis", str(private), "--dataset", "cli-test", "--output", str(exported)],
                        ["validate", str(exported)],
                        ["merge", str(exported), "--db", str(folder / "merged.sqlite"), "--html", str(folder / "merged.html")])
            for args in commands:
                result = subprocess.run([sys.executable, script, *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("PRIVATE", result.stdout + result.stderr)
            exported.write_text('{"cookie":"PRIVATE_COOKIE"}', encoding="utf-8")
            result = subprocess.run([sys.executable, script, "validate", str(exported)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("PRIVATE", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
