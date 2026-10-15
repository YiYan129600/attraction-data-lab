import hashlib
import json
from pathlib import Path
import time
import html
from collections import Counter
from datetime import datetime, timezone

from ..core import canonical, parse_time, normalize_count, init_db, validate_db, analyze
from .normalize import envelope, now, gender, metrics, classify, external_users
from .providers import ProviderError, ReplayProvider
from .state import atomic_json


class BudgetStop(Exception):
    pass


class Runner:
    def __init__(self, config, state, provider, clock=time.monotonic, sleep=time.sleep, retry_blocked=False):
        self.config, self.state, self.provider = config, state, provider
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.previous_elapsed = state.elapsed()
        self.stop_reason = None
        self.last_call = None
        self.retry_blocked = retry_blocked
        baseline_path = state.run_dir / "baseline.json"
        if not baseline_path.exists():
            previous = state.db.execute("SELECT object_id,data FROM tasks WHERE action='deep' AND state='done' AND run<>?", (state.run_id,)).fetchall()
            sufficient = []
            for row in previous:
                sample = json.loads(row[1]).get("sample", {})
                if sample.get("N", 0) >= 8 and (sample.get("K_over_N") or 0) >= .6 and state.get("notes", row[0]):
                    sufficient.append(row[0])
            atomic_json(baseline_path, {"previous_sufficient_deep_notes": sorted(set(sufficient))})

    def remaining(self):
        return self.config.max_seconds - self.previous_elapsed - (self.clock() - self.started)

    def request(self, action, *args):
        for attempt in range(2):
            events = self.state.events("provider_call")
            if len(events) >= self.config.max_requests:
                raise BudgetStop("max_requests")
            if action == "profile" and sum(e["action"] == "profile" for e in events) >= self.config.max_profile_visits:
                raise BudgetStop("max_profile_visits")
            if self.remaining() <= 0:
                raise BudgetStop("max_seconds")
            if self.provider.network and self.last_call is not None:
                delay = max(0, self.config.request_interval - (self.clock() - self.last_call))
                if delay >= self.remaining():
                    raise BudgetStop("max_seconds")
                self.sleep(delay)
            self.provider.timeout = min(self.config.request_timeout, self.remaining())
            begin = self.clock()
            self.last_call = begin
            # Count the attempt durably before it reaches the provider.
            self.state.event("provider_call", action=action, attempt=attempt + 1, live=self.provider.network)
            try:
                result = getattr(self.provider, action)(*args)
                self.state.event("provider_result", action=action, seconds=self.clock() - begin, status="ok")
                return result
            except ProviderError as exc:
                self.state.event("provider_result", action=action, seconds=self.clock() - begin, status=exc.category, http_status=exc.status)
                if exc.category != "transient" or attempt:
                    raise
                if self.remaining() <= 1:
                    raise BudgetStop("max_seconds")
                self.sleep(1)

    def fresh(self, profile, author=False):
        if not profile:
            return False
        hours = self.config.author_cache_hours if author else self.config.profile_cache_hours if gender(profile.get("audience", {}).get("gender")) != "unknown" else self.config.unknown_cache_hours
        age = (datetime.now(timezone.utc) - parse_time(profile["observed_at"])).total_seconds()
        return profile.get("reviewed") is True and 0 <= age <= hours * 3600

    def comments(self, identifier):
        return [r for r in self.state.records("comments") if r["note_id"] == identifier]

    def persist(self, data, effects=None):
        new_ids = {entity: sum(self.state.get(entity, identifier) is None for identifier in {r[id_field] for r in data.get(entity, [])}) for entity, id_field in (("notes", "note_id"), ("comments", "comment_id"), ("creators", "creator_id"))}
        result = self.state.persist(data, effects)
        self.state.event("import", inserted=result["inserted"], duplicates=result["duplicates"], new_unique=new_ids, live=self.provider.network)
        return result

    def mark(self, task, state="done", **extra):
        self.state.set_task(task["id"], state, {**task["data"], **extra})

    def profile(self, uid, note_id=None, author=False):
        if self.remaining() <= 0:
            raise BudgetStop("max_seconds")
        previous = self.state.get("profiles", uid)
        if self.fresh(previous, author) and (not author or previous.get("creator")):
            self.state.event("cache_hit", entity="author" if author else "audience")
            profile = previous
        else:
            # A failed visit in this run stays failed; never substitutes another user.
            failures = [e for e in self.state.events("profile_unavailable") if e.get("user_id") == uid]
            if failures:
                profile = previous or {"user_id": uid, "observed_at": now(), "reviewed": False, "audience": {}}
            else:
                try:
                    profile = self.request("profile", {"user_id": uid})
                    profile = {k: v for k, v in profile.items() if k not in {"records", "next_cursor", "completeness", "stop_reason"}}
                    if profile.get("user_id") != uid:
                        raise ProviderError("schema")
                    if previous and parse_time(profile["observed_at"]) < parse_time(previous["observed_at"]):
                        profile = previous
                        self.state.event("older_profile_response_ignored", user_id=uid)
                except ProviderError as exc:
                    if exc.category not in {"unavailable", "needs_locator"}:
                        raise
                    profile = {"user_id": uid, "observed_at": now(), "reviewed": False, "audience": {}, "failure": exc.category}
                    self.state.event("profile_unavailable", user_id=uid, category=exc.category)
            if previous and profile.get("reviewed"):
                for dimension, entries in previous.get("audience", {}).items():
                    combined = entries + profile.setdefault("audience", {}).get(dimension, [])
                    profile["audience"][dimension] = list({canonical(e): e for e in combined}.values())
        updates = []
        if note_id:
            for comment in self.comments(note_id):
                if comment.get("user_id") == uid and comment.get("level") == 1 and comment.get("is_author") is False:
                    updated = {**comment, "profile_sampled": profile.get("reviewed") is True,
                                    "audience": profile.get("audience", {}), "profile_checked_at": profile["observed_at"],
                                    "profile_check_status": "reviewed" if profile.get("reviewed") else "unavailable",
                                    "profile_sampling_rule": "frozen_first8_then_up_to30_v2"}
                    if canonical(updated) != canonical(comment):
                        updates.append(updated)
        creators = [profile["creator"]] if profile.get("creator") else []
        creators = [c for c in creators if canonical(c) != canonical(self.state.get("creators", c["creator_id"]))]
        changed = canonical(profile) != canonical(previous)
        if updates or creators or changed:
            self.persist(envelope(self.config, comments=updates, creators=creators), {"profiles": [profile] if changed else []})
        return profile

    def ensure_locator(self, note_id):
        if not self.provider.network or note_id in getattr(self.provider, "locators", {}):
            return
        task = next((t for t in self.state.tasks("note") if t["object_id"] == note_id), None)
        if not task or not task["data"].get("query"):
            raise ProviderError("needs_locator")
        query = task["data"]["query"]
        if any(e.get("query") == query for e in self.state.events("locator_refresh")):
            raise ProviderError("needs_locator")
        result = self.request("search", query, self.config.sort, None)
        self.persist(envelope(self.config, searches=[{"search_id": hashlib.sha256(canonical([query, result["observed_at"], "locator_refresh"]).encode()).hexdigest(),
                                                    "query": query, "sort": self.config.sort, "observed_at": result["observed_at"], "source": self.provider.name,
                                                    "candidates": result["candidates"], "status": "locator_refresh"}]))
        self.state.event("locator_refresh", query=query)
        if note_id not in getattr(self.provider, "locators", {}):
            raise ProviderError("needs_locator")

    def seed(self):
        if self.state.tasks():
            return
        # Empty study still validates and can report an access failure honestly.
        self.persist(envelope(self.config))
        for theme, query in self.config.queries.items():
            self.state.add_task("search", theme, theme=theme, query=query, sort=self.config.sort)

    def search(self, task):
        result = self.request("search", task["data"]["query"], task["data"]["sort"], None)
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            raise ProviderError("schema")
        known = {t["object_id"] for t in self.state.tasks("note")}
        new = []
        themes = list(self.config.queries)
        theme_index = themes.index(task["data"]["theme"])
        quota = self.config.max_candidates // len(themes) + (theme_index < self.config.max_candidates % len(themes))
        admitted = sum(t["data"].get("discovery_theme") == task["data"]["theme"] for t in self.state.tasks("note"))
        for candidate in candidates:
            uid = candidate.get("note_id")
            if not isinstance(uid, str) or not uid:
                raise ProviderError("schema")
            if uid in known:
                continue
            if len(known) >= self.config.max_candidates or admitted >= quota:
                break
            theme = task["data"]["theme"]
            if isinstance(self.provider, ReplayProvider):
                topics = candidate.get("topics", [])
                theme = topics[0] if len(topics) == 1 and topics[0] in self.config.queries else "unknown"
            identifier = self.state.add_task("note", uid, theme=theme, discovery_theme=task["data"]["theme"], query=task["data"]["query"], creator_id=candidate.get("creator_id"))
            count = candidate.get("platform_comments") or {}
            if count.get("precision") == "exact" and count.get("value") is not None and count["value"] < 50:
                task_data = next(t for t in self.state.tasks("note") if t["id"] == identifier)["data"]
                self.state.set_task(identifier, "done", {**task_data, "gate": "excluded", "reason": "search_comments_below_50"})
            admitted += 1
            known.add(uid); new.append(candidate)
        search = {"search_id": task["id"], "query": task["data"]["query"], "sort": task["data"]["sort"],
                  "observed_at": result.get("observed_at", now()), "source": result.get("source", self.provider.name),
                  "candidate_count": len(candidates), "candidates": candidates, "admitted_count": len(new), "status": "observed", "sampling_version": "collector-v2-1"}
        self.persist(envelope(self.config, searches=[search]), {"tasks": [{"id": task["id"], "state": "done", "data": task["data"]}]})

    def note(self, task):
        nid, data = task["object_id"], task["data"]
        old = self.state.get("notes", nid)
        self.ensure_locator(nid)
        cid = data.get("creator_id")
        if cid:
            author = self.profile(cid, author=True)
        else:
            author = None
        result = self.request("note", {"note_id": nid, "theme": data["theme"]}, {"expand": False, "root_limit": 10, "replies": False})
        note = result["note"]
        if note["note_id"] != nid:
            raise ProviderError("schema")
        if cid and note["creator_id"] != cid:
            raise ProviderError("schema")
        cid = note["creator_id"]
        if author is None:
            author = self.profile(cid, author=True)
            if self.provider.network and author and author.get("creator"):
                # An actual second read, not an invented newer observation stamp.
                result = self.request("note", {"note_id": nid, "theme": data["theme"]}, {"expand": False, "root_limit": 10, "replies": False})
                note = result["note"]
        creator = (author or {}).get("creator") or self.state.get("creators", cid)
        if not creator:
            creator = {"creator_id": cid, "observed_at": note["observed_at"], "fans": normalize_count(), "gender": [], "source": "unavailable_author_reference"}
        self.persist(envelope(self.config, notes=[note], comments=result["comments"], creators=[creator]))
        fans = creator.get("fans", {})
        count = note.get("counts", {}).get("comments", {})
        reason = None
        if fans.get("precision") == "exact" and fans.get("value") is not None and fans["value"] > 5000:
            reason = "fans_above_5000"
        elif gender(creator.get("gender")) in {"female", "other"}:
            reason = "author_not_male"
        elif count.get("precision") == "exact" and count.get("value") is not None and count["value"] < 50:
            reason = "platform_comments_below_50"
        if reason:
            self.mark(task, gate="excluded", reason=reason); return
        missing = []
        if fans.get("precision") != "exact" or fans.get("value") is None:
            missing.append("fans_unverified")
        if gender(creator.get("gender")) != "male":
            missing.append("author_gender_unverified")
        if count.get("precision") != "exact" or count.get("value") is None:
            missing.append("platform_comments_unverified")
        if parse_time(creator["observed_at"]) > parse_time(note["observed_at"]):
            missing.append("author_snapshot_after_note")
        if missing:
            self.mark(task, "deferred", gate="manual_review", reason=missing); return
        self.mark(task, gate="qualified", creator_id=cid)
        self.state.add_task("screen", nid, theme=data["theme"], creator_id=cid)

    def sample(self, task, deep=False):
        nid, data = task["object_id"], task["data"]
        if deep and not data.get("expanded"):
            self.ensure_locator(nid)
            result = self.request("note", {"note_id": nid, "theme": data["theme"]}, {"expand": True, "root_limit": 100, "replies": True})
            self.persist(envelope(self.config, notes=[result["note"]], comments=result["comments"]))
            data["expanded"] = True
            self.state.set_task(task["id"], "in_progress", data)
        if "selected" not in data:
            users = external_users(self.comments(nid))
            if deep:
                screen = next(t for t in self.state.tasks("screen") if t["object_id"] == nid)
                first = screen["data"]["selected"]
                users = first + [u for u in users if u not in first]
            data["selected"] = users[:30 if deep else 8]
            data["frozen_at"] = now()
            self.state.set_task(task["id"], "in_progress", data)
        for uid in data["selected"]:
            self.profile(uid, nid)
        sample = metrics(self.comments(nid), set(data["selected"]))
        self.mark({**task, "data": data}, sample=sample, cohort=classify(sample))

    def schedule_deep(self):
        if self.state.events("deep_schedule"):
            return
        screens = [t for t in self.state.tasks("screen") if t["state"] == "done"]
        picked, per_author = [], Counter()
        audits = [t for t in screens if t["data"]["cohort"] != "positive" and int(hashlib.sha256(canonical([self.config.seed, t["object_id"]]).encode()).hexdigest(), 16) / 2**256 < self.config.audit_fraction]
        reserved = 1 if audits and self.config.max_deep_notes else 0
        capacity = self.config.max_deep_notes - reserved
        def choose(task, reason):
            if len(picked) >= capacity or per_author[task["data"]["creator_id"]] >= 3:
                return False
            picked.append((task, reason)); per_author[task["data"]["creator_id"]] += 1
            return True
        # Round-robin themes and reserve control slots before spending all on positives.
        themes = sorted({t["data"]["theme"] for t in screens})
        for theme in themes:
            controls = [t for t in screens if t["data"]["theme"] == theme and t["data"]["cohort"] == "counterexample"]
            positives = [t for t in screens if t["data"]["theme"] == theme and t["data"]["cohort"] == "positive"]
            def fans(task):
                return (self.state.get("creators", task["data"]["creator_id"]) or {}).get("fans", {}).get("value", 0)
            controls.sort(key=lambda t: (min((abs(fans(t) - fans(p)) for p in positives), default=0), t["object_id"]))
            for task in controls[:self.config.control_quota]:
                choose(task, "matched_control")
        buckets = {theme: [t for t in screens if t["data"]["theme"] == theme and t["data"]["cohort"] == "positive"] for theme in themes}
        while any(buckets.values()):
            for theme in themes:
                if buckets[theme]:
                    choose(buckets[theme].pop(0), "positive")
        chosen_ids = {t["id"] for t, _ in picked}
        capacity = self.config.max_deep_notes
        for task in audits:
            if task["id"] not in chosen_ids:
                choose(task, "fixed_audit")
        chosen_ids = {t["id"] for t, _ in picked}
        for task in screens:
            if task["id"] not in chosen_ids and task["data"]["cohort"] == "positive":
                choose(task, "positive")
        for task, reason in picked:
            self.state.add_task("deep", task["object_id"], theme=task["data"]["theme"], creator_id=task["data"]["creator_id"], selection_reason=reason, initial_cohort=task["data"]["cohort"])
        chosen_ids = {t["id"] for t, _ in picked}
        audit_ids = {t["id"] for t in audits}
        for task in screens:
            self.mark(task, deep_status="selected" if task["id"] in chosen_ids else "deferred_quota", audit_selected=task["id"] in audit_ids)
        self.state.event("deep_schedule", selected=[t["object_id"] for t, _ in picked], audit_selected=[t["object_id"] for t in audits], audit_fraction=self.config.audit_fraction)

    def run(self):
        self.state.recover()
        self.seed()
        try:
            capabilities = self.request("probe")
            atomic_json(self.state.run_dir / "capabilities.json", capabilities)
            if self.retry_blocked:
                for task in self.state.tasks():
                    if task["state"] == "blocked" or (task["state"] == "deferred" and task["data"].get("reason") == "needs_locator"):
                        self.state.set_task(task["id"], "pending")
            while True:
                if self.remaining() <= 0:
                    raise BudgetStop("max_seconds")
                pending = [t for t in self.state.tasks() if t["state"] == "pending"]
                if not pending:
                    self.schedule_deep()
                    pending = [t for t in self.state.tasks() if t["state"] == "pending"]
                if not pending:
                    break
                priority = {"search": 0, "note": 1, "screen": 2, "deep": 3}
                task = min(pending, key=lambda t: priority[t["action"]])
                self.state.set_task(task["id"], "in_progress")
                try:
                    if task["action"] == "search":
                        self.search(task)
                    elif task["action"] == "note":
                        self.note(task)
                    else:
                        self.sample(task, task["action"] == "deep")
                except ProviderError as exc:
                    self.mark(task, "deferred" if exc.category in {"unavailable", "needs_locator"} else "blocked", reason=exc.category)
                    if exc.category not in {"unavailable", "needs_locator"}:
                        raise
        except BudgetStop as exc:
            self.stop_reason = str(exc)
        except ProviderError as exc:
            self.stop_reason = exc.category
        except Exception:
            self.stop_reason = "local_execution_failed"
            raise
        finally:
            status = "stopped" if self.stop_reason else "finished"
            self.state.finish(status, self.clock() - self.started, self.stop_reason)
        return report(self.config, self.state, self.stop_reason)


def report(config, state, stop_reason=None):
    report_started = time.monotonic()
    from reporting import render_report
    from community import export_analysis, merge
    stop_reason = stop_reason or state.stop_reason()
    quality = validate_db(str(config.database), config.study_id, config.source_mode)
    if not quality["valid"]:
        raise ValueError("collector study validation failed")
    analysis = analyze(str(config.database), config.study_id, config.source_mode, config.owner_id, "high_comment_female_case_study", 50)
    directory = state.run_dir
    atomic_json(directory / "analysis.json", analysis)
    (directory / "content-report.html").write_text(render_report(analysis), encoding="utf-8")
    package = export_analysis(analysis, f"collector-{config.scope}-{state.run_id}")
    atomic_json(directory / "contribution.json", package)
    merge([directory / "contribution.json"], directory / "community.sqlite", directory / "all-cases.html")
    events, tasks = state.events(), state.tasks()
    rows = []
    for note in analysis["notes"]:
        metric = note["metrics"]
        screen = next((t for t in tasks if t["action"] == "screen" and t["object_id"] == note["note_id"]), None)
        deep = next((t for t in tasks if t["action"] == "deep" and t["object_id"] == note["note_id"]), None)
        comments = [c for c in state.records("comments") if c["note_id"] == note["note_id"]]
        roots = sum(c.get("level") == 1 and not c.get("parent_id") for c in comments)
        replies = sum(c.get("level") == 2 or bool(c.get("parent_id")) for c in comments)
        rows.append({"note_id": note["note_id"], "platform_comments": note["platform_counts"].get("comments"), "captured": metric["observed_comment_count"],
                     "roots": roots, "replies": replies, "unclassified_level": len(comments) - roots - replies,
                     "author_replies": sum(c.get("is_author") is True and (c.get("level") == 2 or bool(c.get("parent_id"))) for c in comments),
                     "author_comments_total": metric["author_reply_count"],
                     "adult_target": metric["adult_target"], "coverage": note["coverage"]["status"], "initial_cohort": screen["data"].get("cohort") if screen else None,
                     "deep_cohort": deep["data"].get("cohort") if deep else None, "selected_screen": len(screen["data"].get("selected", [])) if screen else 0,
                     "selected_deep": len(deep["data"].get("selected", [])) if deep else 0})
    calls = [e for e in events if e["kind"] == "provider_call"]
    summary = {"run_id": state.run_id, "source_mode": config.source_mode, "provider": config.provider, "archive_only": config.provider == "replay", "new_live_data": any(e["kind"] == "provider_result" and e["action"] == "note" and e["status"] == "ok" and config.provider == "xhs-rest" for e in events),
               "stop_reason": stop_reason, "elapsed_seconds": state.elapsed(), "token_usage": None, "collector_model_calls": 0,
               "provider_calls": len(calls), "actual_live_provider_calls": sum(e["live"] for e in calls), "actual_profile_visits": sum(e["action"] == "profile" and e["live"] for e in calls),
               "cache_hits": sum(e["kind"] == "cache_hit" for e in events), "new_unique": {entity: sum(e.get("new_unique", {}).get(entity, 0) for e in events if e["kind"] == "import") for entity in ("notes", "comments", "creators")}, "candidates": len(state.tasks("note")), "authors": len({n["creator"]["creator_id"] for n in analysis["notes"]}),
               "task_states": dict(Counter(t["state"] for t in tasks)), "gates": dict(Counter(t["data"].get("gate", "pending") for t in state.tasks("note"))),
               "screen_completed": sum(t["state"] == "done" for t in state.tasks("screen")), "deep_completed": sum(t["state"] == "done" for t in state.tasks("deep")),
               "cohorts": dict(Counter(t["data"].get("cohort", "pending") for t in state.tasks("screen"))), "profile_failures": [e for e in events if e["kind"] == "profile_unavailable"],
               "actions": [e for e in events if e["kind"] == "provider_result"], "notes": rows, "validation": quality,
               "limitations": ["partial comments do not represent all audience", "unknown age is not adult confirmation", "no comparable throughput baseline", "replay is not new live collection"]}
    atomic_json(directory / "run-summary.json", summary)
    baseline_path = directory / "baseline.json"
    previous = set(json.loads(baseline_path.read_text(encoding="utf-8"))["previous_sufficient_deep_notes"]) if baseline_path.exists() else None
    sufficient = [t for t in state.tasks("deep") if t["state"] == "done" and t["data"].get("sample", {}).get("N", 0) >= 8 and (t["data"].get("sample", {}).get("K_over_N") or 0) >= .6]
    new_deep = sum(t["object_id"] not in previous for t in sufficient) if previous is not None else None
    summary.update(new_sufficient_deep_notes=new_deep, visits_per_new_sufficient_deep_note=summary["actual_profile_visits"] / new_deep if new_deep else None,
                   collector_model_calls_per_new_sufficient_deep_note=0 if new_deep else None,
                   analysis_scope="whole owner/study/source_mode; candidates and tasks are current-run only")
    manual = [{"note_id": t["object_id"], "reason": t["data"].get("reason"), "action": t["action"]} for t in tasks if t["state"] in {"deferred", "blocked"}]
    manual += [{"note_id": note["note_id"], "reason": "visual_annotation_missing", "action": "visual_review"} for note in analysis["notes"] if not note.get("visual_labels")]
    atomic_json(directory / "manual-tasks.json", manual)
    escape = lambda val: html.escape(str(val), quote=True)
    funnel = '<section><h1>采集批次与全部候选漏斗</h1><p>此报告含初筛排除和待核对候选；回放不算新增实采，年龄未知不等于成年。</p><p>下方社区表的screen仅描述已查样本F/K；男性小账号研究的资格以本批门槛表为准。排除和待核对案例不能作为效果推荐。</p>'
    funnel += '<p>' + ' · '.join(escape(k) + ': ' + escape(summary[k]) for k in ("run_id", "provider", "archive_only", "stop_reason", "candidates", "screen_completed", "deep_completed", "provider_calls", "actual_profile_visits", "cache_hits")) + '</p>'
    funnel += '<table><tr><th>候选原生ID</th><th>状态</th><th>门槛</th><th>原因</th></tr>'
    for task in state.tasks("note"):
        funnel += '<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in (task["object_id"], task["state"], task["data"].get("gate", "pending"), task["data"].get("reason", ""))) + '</tr>'
    funnel += '</table></section>'
    page = (directory / "all-cases.html").read_text(encoding="utf-8")
    (directory / "run-report.html").write_text(page.replace('<h1>', funnel + '<h1>', 1), encoding="utf-8")
    summary["report_elapsed_seconds"] = time.monotonic() - report_started
    summary["timing_boundary"] = "elapsed_seconds measures collection execution; provider initialization and report rendering are not station throughput"
    atomic_json(directory / "run-summary.json", summary)
    return {k: summary[k] for k in ("run_id", "provider", "stop_reason", "provider_calls", "actual_profile_visits", "cache_hits", "candidates", "screen_completed", "deep_completed")}
