import json
from pathlib import Path
import socket
import re
import urllib.error
import urllib.request

from import_browser_captures import load_captures, convert, gender_evidence, age_evidence
from ..core import parse_time, normalize_count
from .normalize import clean, now, rest_note, rest_profile


class ProviderError(Exception):
    def __init__(self, category, status=None):
        super().__init__(category)
        self.category, self.status = category, status

    @property
    def fatal(self):
        return self.category in {"login_required", "access_denied", "rate_limited", "captcha", "schema", "redirect_rejected", "needs_locator"}


def result_metadata(value, source, version, records):
    return {**value, "records": records, "source": value.get("source", source), "provider_version": version,
            "observed_at": value.get("observed_at") or value.get("note", {}).get("observed_at") or now(),
            "completeness": value.get("completeness") or value.get("note", {}).get("coverage", {}).get("status", "unknown"),
            "stop_reason": value.get("note", {}).get("coverage", {}).get("stop_reason"), "next_cursor": value.get("cursor") or None}


class ReplayProvider:
    name = "replay"
    version = "authorized-dom-replay-v1"
    network = False

    def __init__(self, config, captures):
        folder = Path(captures)
        paths = sorted(folder.glob("capture-*.json")) if folder.is_dir() else [folder]
        for path in paths:
            declared = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(declared, dict) and "source_mode" in declared and declared["source_mode"] != config.source_mode:
                raise ValueError("capture source_mode conflicts with configuration")
        rows = load_captures(paths)
        self.envelope, self.audit = convert(rows, config.study_id, config.owner_id)
        self.envelope["source_mode"] = config.source_mode
        # Preserve actual source timestamps rather than the legacy converter's
        # collection-completion stamp, which may include a later author visit.
        for note in self.envelope["notes"]:
            note["observed_at"] = note["source_note_observed_at"]
            note["source"] = "capture_replay"
        for comment in self.envelope["comments"]:
            comment["observed_at"] = comment["source_comment_observed_at"]
        self.envelope = clean(self.envelope)
        self.notes, self.creators, self.profiles = {}, {}, {}
        for note in self.envelope["notes"]:
            old = self.notes.get(note["note_id"])
            if not old or parse_time(note["observed_at"]) >= parse_time(old["observed_at"]):
                self.notes[note["note_id"]] = note
        for creator in self.envelope["creators"]:
            old = self.creators.get(creator["creator_id"])
            if not old or parse_time(creator["observed_at"]) >= parse_time(old["observed_at"]):
                self.creators[creator["creator_id"]] = creator
        for row in rows:
            if row["kind"] != "audience_dom" or not row.get("user_id"):
                continue
            profile = {"user_id": row["user_id"], "observed_at": row["observed_at"], "reviewed": row.get("reviewed") is True,
                       "source": "capture_replay", "audience": {"gender": gender_evidence(row), "age_band": age_evidence(row)}}
            profile["audience"].update({k: v for k, v in (row.get("audience_annotations") or {}).items() if k in {"interests", "presented_style", "preferred_style"}})
            old = self.profiles.get(row["user_id"])
            if not old or parse_time(profile["observed_at"]) >= parse_time(old["observed_at"]):
                self.profiles[row["user_id"]] = clean(profile)

    def probe(self):
        return result_metadata({"provider": self.name, "version": self.version, "network": False, "notes": len(self.notes), "profiles": len(self.profiles), "cursor": False, "new_live_data": False}, self.name, self.version, [])

    def search(self, query, sort, cursor=None):
        if cursor is not None:
            raise ProviderError("schema")
        candidates = [{"note_id": n["note_id"], "creator_id": n["creator_id"], "platform_comments": n.get("counts", {}).get("comments"), "topics": n.get("topics", [])} for n in self.notes.values()]
        return result_metadata({"candidates": candidates, "cursor": None, "observed_at": now(), "source": self.name, "completeness": "captured_archive_only"}, self.name, self.version, candidates)

    def note(self, locator, policy):
        identifier = locator["note_id"]
        if identifier not in self.notes:
            raise ProviderError("unavailable")
        value = {"note": self.notes[identifier], "comments": [c for c in self.envelope["comments"] if c["note_id"] == identifier]}
        return result_metadata(value, self.name, self.version, [value["note"], *value["comments"]])

    def profile(self, locator):
        identifier = locator["user_id"]
        profile = dict(self.profiles.get(identifier, {"user_id": identifier, "observed_at": now(), "reviewed": False, "audience": {}, "source": self.name}))
        if identifier in self.creators:
            profile["creator"] = self.creators[identifier]
            if identifier not in self.profiles:
                profile.update(reviewed=True, observed_at=self.creators[identifier]["observed_at"], audience={"gender": self.creators[identifier].get("gender", [])})
        profile = clean(profile)
        return result_metadata(profile, self.name, self.version, [profile])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("redirect_rejected", code)


class RestProvider:
    name = "xhs-rest"
    version = "xpzouying-rest-v1-bound"
    network = True
    routes = {"/health", "/api/v1/login/status", "/api/v1/feeds/search", "/api/v1/feeds/detail", "/api/v1/user/profile"}

    def __init__(self, config, live=False):
        if not live:
            raise ValueError("REST requires --live")
        config.validate()
        if config.source_mode != "real":
            raise ValueError("live REST requires real source_mode")
        self.config, self.timeout = config, config.request_timeout
        self.locators = {}
        self.headers = {}
        if config.credentials_file:
            settings = json.loads(Path(config.credentials_file).read_text(encoding="utf-8-sig"))
            if settings.get("base_url", config.base_url).rstrip("/") != config.base_url:
                raise ValueError("credential origin mismatch")
            secret = settings.get("token")
            if not isinstance(secret, str) or not secret or "\n" in secret or "\r" in secret:
                raise ValueError("invalid private credential file")
            self.headers["Authorization"] = "Bearer " + secret
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, route, payload=None):
        if route not in self.routes:
            raise ValueError("read routes only")
        headers = {**self.headers, "Content-Type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.config.base_url + route, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ProviderError("schema")
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            category = {401: "login_required", 403: "access_denied", 429: "rate_limited", 404: "unavailable"}.get(exc.code, "transient" if exc.code >= 500 else "schema")
            raise ProviderError(category, exc.code) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
            raise ProviderError("service_unavailable" if code in {10061, 111, 61} else "transient") from None
        except (TimeoutError, socket.timeout, ConnectionError):
            raise ProviderError("transient") from None
        except (json.JSONDecodeError, UnicodeError):
            raise ProviderError("schema") from None
        if not isinstance(result, dict):
            raise ProviderError("schema")
        if result.get("success") is False:
            # Inspect error only in memory; never echo or persist arbitrary body.
            message = str(result.get("message", "")) + str(result.get("error", ""))
            if any(word in message.lower() for word in ("captcha", "验证码")):
                raise ProviderError("captcha")
            if any(word in message.lower() for word in ("login", "登录")):
                raise ProviderError("login_required")
            raise ProviderError("schema")
        return result

    def probe(self):
        # Runner accounts for each request separately; doctor uses request too.
        result = self.request("/api/v1/login/status")
        data = result.get("data")
        if not isinstance(data, dict) or type(data.get("is_logged_in")) is not bool:
            raise ProviderError("schema")
        if not data["is_logged_in"]:
            raise ProviderError("login_required")
        value = {"provider": self.name, "version": self.version, "logged_in": True, "cursor": False,
                "gender_integer_accepted": False, "all_comment_completeness_supported": False, "installed_version_verified": False,
                "standard_user_locator_tokens_available": False}
        return result_metadata(value, self.name, self.version, [])

    def search(self, query, sort, cursor=None):
        if cursor is not None:
            raise ProviderError("schema")
        result = self.request("/api/v1/feeds/search", {"keyword": query, "filters": {"sort_by": sort}})
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("feeds"), list):
            raise ProviderError("schema")
        candidates = []
        for card in data["feeds"]:
            if not isinstance(card, dict) or not isinstance(card.get("id"), str) or not re.fullmatch(r"[0-9a-fA-F]{1,64}", card["id"]):
                raise ProviderError("schema")
            identifier = card["id"]
            signed = card.get("xsecToken")
            if isinstance(signed, str) and signed:
                self.locators[identifier] = signed
            note = card.get("noteCard") or {}
            user = note.get("user") or {}
            if user.get("userId") and user.get("xsecToken"):
                self.locators[user["userId"]] = user["xsecToken"]
            candidates.append({"note_id": identifier, "creator_id": user.get("userId"), "title": note.get("displayTitle"), "platform_comments": normalize_count(raw=(note.get("interactInfo") or {}).get("commentCount"))})
        return result_metadata({"candidates": candidates, "observed_at": now(), "source": self.name, "completeness": "visible_search_page", "cursor": None}, self.name, self.version, candidates)

    def note(self, locator, policy):
        identifier = locator["note_id"]
        signed = self.locators.get(identifier)
        if not signed:
            raise ProviderError("needs_locator")
        payload = {"feed_id": identifier, "xsec_token": signed, "load_all_comments": policy.get("expand", False),
                   "comment_config": {"max_comment_items": policy.get("root_limit", 10), "click_more_replies": policy.get("replies", False), "max_replies_threshold": 100, "scroll_speed": "normal"}}
        result = self.request("/api/v1/feeds/detail", payload)
        try:
            value = rest_note(result, identifier, now(), locator.get("theme", "unknown"))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("schema") from None
        # Only source-provided user locators; a feed token is not a user token.
        def remember(obj):
            if isinstance(obj, dict):
                if obj.get("userId") and obj.get("xsecToken"):
                    self.locators[obj["userId"]] = obj["xsecToken"]
                for child in obj.values():
                    remember(child)
            elif isinstance(obj, list):
                for child in obj:
                    remember(child)
        remember(result)
        return result_metadata(value, self.name, self.version, [value["note"], *value["comments"]])

    def profile(self, locator):
        identifier = locator["user_id"]
        signed = self.locators.get(identifier)
        if not signed:
            raise ProviderError("needs_locator")
        result = self.request("/api/v1/user/profile", {"user_id": identifier, "xsec_token": signed})
        try:
            profile = rest_profile(result, identifier, now())
            return result_metadata(profile, self.name, self.version, [profile])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("schema") from None
