import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from contextlib import contextmanager

from ..core import canonical, import_records, parse_time
from .normalize import clean, now, merge_comment
from .config import PROJECT


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(canonical(clean(value)) + "\n", encoding="utf-8")
    os.replace(temp, path)


@contextmanager
def service_lock(config):
    # OS lock releases on process death; shared by all studies using this origin.
    origin = str(urlsplit(config.base_url).port or 80) if config.provider == "xhs-rest" else str(config.root)
    key = hashlib.sha256(origin.encode()).hexdigest()
    lock_root = PROJECT / "data/private/collector-service-locks" if config.provider == "xhs-rest" else Path(config.state_root).resolve() / "service-locks"
    path = lock_root / (key + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if stream.tell() == 0:
        stream.write(b"0"); stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise ValueError("another collector controls this service") from None
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


class State:
    def __init__(self, config, run_id, create=True):
        self.config, self.run_id = config, run_id
        config.root.mkdir(parents=True, exist_ok=True)
        self.path = config.root / "state.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, config TEXT, created TEXT, elapsed REAL DEFAULT 0, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS tasks(run TEXT, id TEXT, action TEXT, object_id TEXT, state TEXT, data TEXT, PRIMARY KEY(run,id));
        CREATE TABLE IF NOT EXISTS records(entity TEXT, id TEXT, payload TEXT, PRIMARY KEY(entity,id));
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, path TEXT, ack INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS run_batches(run TEXT, id TEXT, path TEXT, ack INTEGER DEFAULT 0, PRIMARY KEY(run,id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, run TEXT, kind TEXT, data TEXT, observed_at TEXT);
        """)
        for row in self.db.execute("SELECT id,path,ack FROM batches").fetchall():
            legacy_run = Path(row[1]).parent.parent.name
            self.db.execute("INSERT OR IGNORE INTO run_batches VALUES(?,?,?,?)", (legacy_run, row[0], row[1], row[2]))
        self.db.commit()
        if "stop_reason" not in {r[1] for r in self.db.execute("PRAGMA table_info(runs)")}:
            self.db.execute("ALTER TABLE runs ADD COLUMN stop_reason TEXT")
            self.db.commit()
        self.run_dir = config.root / "runs" / run_id
        if create:
            if self.db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                self.db.close()
                raise ValueError("run already exists; use resume")
            self.run_dir.mkdir(parents=True, exist_ok=False)
            self.db.execute("INSERT INTO runs(id,config,created) VALUES(?,?,?)", (run_id, canonical(config.public_manifest()), now()))
            self.db.commit()
            atomic_json(self.run_dir / "manifest.json", {"schema_version": 1, "run_id": run_id, "config": config.public_manifest(), "archive_only": config.provider == "replay", "token_usage": None})
        else:
            run = self.db.execute("SELECT config FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                self.db.close()
                raise ValueError("unknown run")
            if json.loads(run[0]) != config.public_manifest():
                self.db.close()
                raise ValueError("resume requires the frozen run configuration")

    def close(self):
        self.db.close()

    def event(self, kind, **data):
        self.db.execute("INSERT INTO events(run,kind,data,observed_at) VALUES(?,?,?,?)", (self.run_id, kind, canonical(clean(data)), now()))
        self.db.commit()

    def events(self, kind=None):
        query = "SELECT kind,data,observed_at FROM events WHERE run=?"
        args = [self.run_id]
        if kind:
            query += " AND kind=?"; args.append(kind)
        return [{"kind": row[0], **json.loads(row[1]), "observed_at": row[2]} for row in self.db.execute(query, args)]

    def add_task(self, action, object_id, **data):
        identifier = hashlib.sha256(canonical([self.config.scope, "collector-v2-1", action, object_id]).encode()).hexdigest()[:32]
        self.db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?)", (self.run_id, identifier, action, object_id, "pending", canonical(clean(data))))
        self.db.commit()
        return identifier

    def tasks(self, action=None):
        rows = self.db.execute("SELECT * FROM tasks WHERE run=? ORDER BY rowid", (self.run_id,)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows if action is None or row["action"] == action]

    def set_task(self, identifier, state, data=None):
        if state not in {"pending", "in_progress", "done", "deferred", "blocked", "failed"}:
            raise ValueError("invalid task state")
        if data is None:
            self.db.execute("UPDATE tasks SET state=? WHERE run=? AND id=?", (state, self.run_id, identifier))
        else:
            self.db.execute("UPDATE tasks SET state=?,data=? WHERE run=? AND id=?", (state, canonical(clean(data)), self.run_id, identifier))
        self.db.commit()

    def get(self, entity, identifier):
        row = self.db.execute("SELECT payload FROM records WHERE entity=? AND id=?", (entity, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    def records(self, entity):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM records WHERE entity=? ORDER BY rowid", (entity,))]

    def put(self, entity, identifier, record):
        self.db.execute("INSERT INTO records VALUES(?,?,?) ON CONFLICT(entity,id) DO UPDATE SET payload=excluded.payload", (entity, identifier, canonical(clean(record))))

    def persist(self, envelope, effects=None, crash=None):
        # Proof file precedes both databases. Replay is safe across every gap.
        envelope = clean(envelope)
        envelope["comments"] = [merge_comment(self.get("comments", row["comment_id"]) or {}, row) for row in envelope.get("comments", [])]
        body = {"schema_version": 1, "envelope": envelope, "effects": clean(effects or {})}
        identifier = hashlib.sha256(canonical(body).encode()).hexdigest()
        path = self.run_dir / "evidence" / (identifier + ".json")
        if not path.exists():
            atomic_json(path, {"batch_id": identifier, **body})
        if crash:
            crash("after_write")
        self.db.execute("INSERT OR IGNORE INTO run_batches(run,id,path) VALUES(?,?,?)", (self.run_id, identifier, str(path)))
        self.db.commit()
        result = self.apply_batch(path, crash)
        return result

    def apply_batch(self, path, crash=None):
        batch = json.loads(Path(path).read_text(encoding="utf-8"))
        identifier = batch.pop("batch_id")
        if hashlib.sha256(canonical(batch).encode()).hexdigest() != identifier:
            raise ValueError("evidence checksum mismatch")
        env = batch["envelope"]
        if [env[k] for k in ("owner_id", "study_id", "source_mode")] != [self.config.owner_id, self.config.study_id, self.config.source_mode]:
            raise ValueError("evidence namespace mismatch")
        result = import_records(str(self.config.database), [env])
        if crash:
            crash("after_import")
        with self.db:
            for entity, id_field in (("creators", "creator_id"), ("notes", "note_id"), ("comments", "comment_id"), ("searches", "search_id")):
                for row in env.get(entity, []):
                    old = self.get(entity, row[id_field])
                    if old and parse_time(row["observed_at"]) < parse_time(old["observed_at"]):
                        continue
                    self.put(entity, row[id_field], merge_comment(old or {}, row) if entity == "comments" else row)
            for profile in batch["effects"].get("profiles", []):
                old = self.get("profiles", profile["user_id"])
                if not old or parse_time(profile["observed_at"]) >= parse_time(old["observed_at"]):
                    self.put("profiles", profile["user_id"], profile)
            for update in batch["effects"].get("tasks", []):
                self.db.execute("UPDATE tasks SET state=?,data=? WHERE run=? AND id=?", (update["state"], canonical(update["data"]), self.run_id, update["id"]))
            self.db.execute("INSERT OR REPLACE INTO run_batches VALUES(?,?,?,1)", (self.run_id, identifier, str(path)))
        if crash:
            crash("after_ack")
        return result

    def recover(self):
        recovered = 0
        for path in sorted((self.run_dir / "evidence").glob("*.json")):
            row = self.db.execute("SELECT ack FROM run_batches WHERE run=? AND id=?", (self.run_id, path.stem)).fetchone()
            if not row or not row[0]:
                self.apply_batch(path); recovered += 1
        self.db.execute("UPDATE tasks SET state='pending' WHERE run=? AND state='in_progress'", (self.run_id,))
        self.db.commit()
        return recovered

    def elapsed(self):
        return self.db.execute("SELECT elapsed FROM runs WHERE id=?", (self.run_id,)).fetchone()[0]

    def stop_reason(self):
        return self.db.execute("SELECT stop_reason FROM runs WHERE id=?", (self.run_id,)).fetchone()[0]

    def finish(self, status, seconds, stop_reason=None):
        self.db.execute("UPDATE runs SET status=?,elapsed=elapsed+?,stop_reason=? WHERE id=?", (status, seconds, stop_reason, self.run_id))
        self.db.commit()
