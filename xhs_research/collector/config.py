from dataclasses import dataclass, field, asdict
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[2]
THEMES = {"body_display": "男生身材", "fitness": "男生健身", "outdoor": "男生户外", "daily": "男生日常", "car": "男生汽车", "hobby": "男生爱好"}


@dataclass
class Config:
    study_id: str
    owner_id: str
    source_mode: str = "real"
    provider: str = "replay"
    base_url: str = "http://127.0.0.1:18060"
    credentials_file: str | None = None
    captures: str | None = None
    state_root: str = str(PROJECT / "data/private/collector")
    queries: dict = field(default_factory=lambda: dict(THEMES))
    sort: str = "最多评论"
    seed: str = "collector-v2-1"
    author_cache_hours: float = 24
    profile_cache_hours: float = 168
    unknown_cache_hours: float = 24
    audit_fraction: float = .1
    control_quota: int = 1
    max_candidates: int = 20
    max_requests: int = 300
    max_profile_visits: int = 240
    max_deep_notes: int = 6
    max_seconds: float = 1800
    request_interval: float = 1
    request_timeout: float = 45

    def validate(self):
        for value in (self.study_id, self.owner_id, self.seed):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
                raise ValueError("invalid study/owner/seed identifier")
        if self.source_mode not in {"real", "synthetic"} or self.provider not in {"replay", "xhs-rest"}:
            raise ValueError("unsupported mode/provider")
        if not isinstance(self.queries, dict) or not self.queries or set(self.queries) - set(THEMES):
            raise ValueError("queries require supported themes")
        if any(not isinstance(q, str) or not 0 < len(q) <= 100 for q in self.queries.values()):
            raise ValueError("invalid query")
        for key in ("max_candidates", "max_requests", "max_profile_visits", "max_deep_notes", "control_quota"):
            val = getattr(self, key)
            if type(val) is not int or not 0 <= val <= 100000:
                raise ValueError("invalid integer budget")
        for key in ("max_seconds", "request_interval", "request_timeout", "author_cache_hours", "profile_cache_hours", "unknown_cache_hours", "audit_fraction"):
            val = getattr(self, key)
            if isinstance(val, bool) or not isinstance(val, (float, int)) or not math.isfinite(val) or val < 0:
                raise ValueError("invalid numeric configuration")
        if not self.max_seconds or not self.request_timeout or self.audit_fraction > 1:
            raise ValueError("invalid time/audit budget")
        parts = urlsplit(self.base_url)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"} or parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("REST requires explicit loopback HTTP origin")
        parts.port  # Validate port syntax.
        self.base_url = self.base_url.rstrip("/")
        return self

    @property
    def scope(self):
        return hashlib.sha256(json.dumps([self.owner_id, self.study_id, self.source_mode], separators=(",", ":")).encode()).hexdigest()[:24]

    @property
    def root(self):
        return Path(self.state_root).resolve() / self.scope

    @property
    def database(self):
        return self.root / "research.sqlite"

    def public_manifest(self):
        data = asdict(self)
        for key in ("credentials_file", "captures", "state_root"):
            data.pop(key)
        return data


def load_config(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) - set(Config.__dataclass_fields__):
        raise ValueError("unsupported configuration fields; credentials belong in a private file")
    config = Config(**data).validate()
    private = (PROJECT / "data/private").resolve()
    if not Path(config.state_root).resolve().is_relative_to(private):
        raise ValueError("collector outputs must stay under project data/private")
    return config
