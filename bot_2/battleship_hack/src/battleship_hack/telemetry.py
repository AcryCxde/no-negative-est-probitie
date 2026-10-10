"""Append-only JSONL, replayable DuckDB views, and compact reports."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value, secrets: tuple[str, ...] = ()):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if any(s in k.lower() for s in ("token", "password", "authorization", "cookie", "secret", "joincode")) else redact(v, secrets)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


class Journal:
    def __init__(self, data_dir: Path, seed: int, strategy: str, experiment: str, config: dict, secrets=()):
        self.run_id = str(uuid4())
        self.battle_id = None
        self.sequence = 0
        self.secrets = tuple(secrets)
        self.path = data_dir / "logs" / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a", encoding="utf-8")
        self.write("local", "RUN_STARTED", {"seed": seed, "strategy": strategy, "experiment_id": experiment,
                   "config": config, "version": self.snapshot(data_dir)})
        print(f"run_id={self.run_id}\nlog={self.path}", flush=True)

    def snapshot(self, data_dir: Path) -> dict:
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
            dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", "."], text=True))
        except (OSError, subprocess.CalledProcessError):
            commit, dirty = None, True
        target = data_dir / "versions" / self.run_id
        digest = hashlib.sha256()
        # Snapshot only explicit source/config files; never .env or the working tree at large.
        for file in sorted(list(Path("src").rglob("*.py")) + list(Path("configs").glob("*.toml")) + [Path("pyproject.toml")]):
            if file.is_file():
                content = redact(file.read_text(), self.secrets)
                output = target / file
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content)
                digest.update(str(file).encode() + content.encode())
        return {"commit": commit, "dirty": dirty, "source_sha256": digest.hexdigest(), "snapshot": str(target)}

    def write(self, direction: str, kind: str, payload: dict) -> None:
        self.sequence += 1
        entry = {"schema_version": 1, "event_id": str(uuid4()), "timestamp": utc_now(),
                 "sequence": self.sequence, "bot_id": "bot2", "run_id": self.run_id,
                 "battle_id": self.battle_id, "direction": direction, "type": kind,
                 "payload": redact(payload, self.secrets)}
        self.file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


VIEWS = """
CREATE OR REPLACE VIEW runs AS
SELECT run_id, bot_id, timestamp AS started_at,
 payload->>'strategy' AS strategy, payload->>'experiment_id' AS experiment_id,
 payload->'config' AS config, payload->'version' AS version
FROM events WHERE type = 'RUN_STARTED';
CREATE OR REPLACE VIEW experiments AS
SELECT experiment_id, strategy, count(*) AS run_count FROM runs GROUP BY ALL;
CREATE OR REPLACE VIEW battles AS
WITH starts AS (
 SELECT battle_id, run_id, bot_id, timestamp AS started_at, payload->'rules' AS rules,
 payload->'opponent' AS opponent, payload->>'strategy' AS actual_strategy
 FROM events WHERE type = 'BATTLE_STARTED'
), endings AS (
 SELECT battle_id, timestamp AS finished_at, payload->>'outcome' AS outcome,
 payload->>'endReason' AS end_reason FROM events WHERE type = 'BATTLE_FINISHED'
 QUALIFY row_number() OVER (PARTITION BY battle_id ORDER BY timestamp DESC) = 1
), enrichment AS (
 SELECT battle_id, payload->>'matchId' AS match_id,
 try_cast(payload->>'ratingChange' AS DOUBLE) AS rating_change
 FROM events WHERE type = 'HTTP_MATCH_LINKED'
 QUALIFY row_number() OVER (PARTITION BY battle_id ORDER BY timestamp DESC) = 1
)
SELECT s.*, coalesce(s.actual_strategy, r.strategy) AS strategy,
 r.strategy AS configured_strategy, r.experiment_id, e.finished_at, e.outcome, e.end_reason,
 CASE WHEN e.outcome IS NOT NULL THEN 'FINISHED'
      WHEN EXISTS(SELECT 1 FROM events x WHERE x.battle_id = s.battle_id AND x.type = 'BATTLE_INTERRUPTED') THEN 'INTERRUPTED'
      ELSE 'RUNNING' END AS status,
 h.match_id, h.rating_change
FROM starts s LEFT JOIN endings e USING (battle_id)
LEFT JOIN enrichment h USING (battle_id) LEFT JOIN runs r USING (run_id, bot_id);
"""


def ingest(logs: Path, database: Path) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with duckdb.connect(str(database)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            event_id VARCHAR PRIMARY KEY, schema_version INTEGER, timestamp TIMESTAMPTZ,
            sequence BIGINT, bot_id VARCHAR, run_id VARCHAR, battle_id VARCHAR,
            direction VARCHAR, type VARCHAR, payload JSON)""")
        db.execute("BEGIN")
        try:
            files = [logs] if logs.is_file() else sorted(logs.glob("*.jsonl"))
            for file in files:
                with file.open(encoding="utf-8") as handle:
                    for number, line in enumerate(handle, 1):
                        if not line.endswith("\n"):
                            print(f"Pending incomplete line: {file}:{number}", file=sys.stderr)
                            break
                        try:
                            e = json.loads(line)
                            row = [e[k] for k in ("event_id", "schema_version", "timestamp", "sequence", "bot_id", "run_id", "battle_id", "direction", "type")]
                            row.append(json.dumps(e["payload"]))
                        except (ValueError, KeyError, TypeError) as exc:
                            print(f"Invalid journal record: {file}:{number} ({type(exc).__name__})", file=sys.stderr)
                            continue
                        db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", row)
                        count += 1
            db.execute(VIEWS)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return count


def report(database: Path) -> dict:
    with duckdb.connect(str(database), read_only=True) as db:
        def rows(sql):
            cursor = db.execute(sql)
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, r)) for r in cursor.fetchall()]
        return {
            "strategies": rows("""SELECT strategy, count(*) AS battles,
                count(*) FILTER (WHERE status='FINISHED') AS finished,
                count(*) FILTER (WHERE status='INTERRUPTED') AS interrupted,
                count(*) FILTER (WHERE outcome='WIN') AS wins,
                count(*) FILTER (WHERE outcome='LOSS') AS losses,
                count(*) FILTER (WHERE outcome='DRAW') AS draws,
                count(*) FILTER (WHERE outcome='WIN') * 1.0 / nullif(count(*) FILTER (WHERE status='FINISHED'),0) AS win_rate,
                count(rating_change) AS battles_with_known_rating_change,
                sum(rating_change) AS rating_change FROM battles GROUP BY strategy"""),
            "decisions": rows("""SELECT count(*) AS count,
                avg(try_cast(payload->>'elapsed_ms' AS DOUBLE)) AS mean_ms,
                quantile_cont(try_cast(payload->>'elapsed_ms' AS DOUBLE),0.95) AS p95_ms
                FROM events WHERE type='DECISION'"""),
            "diagnostics": rows("""SELECT type, count(*) AS count FROM events
                WHERE type IN ('ERROR','PROTOCOL_ERROR','TURN_TIMED_OUT','COMMAND_REJECTED',
                'SHOT_REJECTED','PLACEMENT_REJECTED','DISCONNECTED','CONNECTION_FAILED','HTTP_ENRICHMENT_FAILED') GROUP BY type"""),
            "rating_history": rows("""SELECT CAST(timestamp AS VARCHAR) AS timestamp,
                payload->>'phase' AS phase, try_cast(payload->>'rating' AS DOUBLE) AS rating,
                try_cast(payload->>'place' AS INTEGER) AS place FROM events
                WHERE type='HTTP_STATISTICS' ORDER BY timestamp"""),
            "matches": rows("SELECT battle_id, match_id, status, outcome, end_reason, rating_change FROM battles ORDER BY started_at"),
        }
