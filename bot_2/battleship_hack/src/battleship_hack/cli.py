import argparse
import asyncio
import fcntl
import json
import secrets
import sys
import tomllib
import traceback
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values

from .client import run
from .game import STRATEGIES
from .telemetry import Journal, ingest, redact, report


DEFAULT_DATA = Path("data/bot2")


def parser():
    p = argparse.ArgumentParser(description="bot2 client: TRAINING and RANKING matches. Both modes change Elo.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke", help="Two offline smoke checks: local WS exchange and JSONL/DuckDB replay")
    r = sub.add_parser("run", help="Play matches, then disconnect and import telemetry")
    r.add_argument("--bot", choices=["bot2"], default="bot2")
    r.add_argument("--config", type=Path, default=Path("configs/bot2.toml"))
    r.add_argument("--env-file", type=Path, help="Default: .env in project, then ../.env")
    r.add_argument("--strategy", choices=sorted(STRATEGIES))
    r.add_argument("--mode", choices=["TRAINING", "RANKING"])
    r.add_argument("--opponent", choices=["EASY", "MEDIUM", "HARD"])
    r.add_argument("--matches", type=int)
    r.add_argument("--max-seconds", type=int)
    r.add_argument("--seed", type=int)
    r.add_argument("--experiment", default="first-training")
    r.add_argument("--use-bonuses", dest="use_bonuses", action=argparse.BooleanOptionalAction, default=None,
                   help="Use MATCH_STARTED bonuses (default: config/true)")
    for command in ("ingest", "report"):
        q = sub.add_parser(command)
        q.add_argument("--bot", choices=["bot2"], default="bot2")
        q.add_argument("--db", type=Path, default=DEFAULT_DATA / "analytics.duckdb")
        if command == "ingest":
            q.add_argument("--logs", type=Path, default=DEFAULT_DATA / "logs")
    return p


def execute(args):
    if args.command == "smoke":
        from .smoke import smoke
        asyncio.run(smoke())
        return
    if args.command == "ingest":
        count = ingest(args.logs, args.db)
        print(f"Processed {count} records (existing event_id values retained without duplicates)")
        return
    if args.command == "report":
        print(json.dumps(report(args.db), ensure_ascii=False, indent=2, default=str))
        return
    config = tomllib.loads(args.config.read_text())
    config.setdefault("mode", "TRAINING")
    config.setdefault("use_bonuses", True)
    for name in ("mode", "strategy", "opponent", "matches", "max_seconds"):
        value = getattr(args, name)
        if value is not None:
            config[name] = value
    if args.use_bonuses is not None:
        config["use_bonuses"] = args.use_bonuses
    if config["mode"] not in {"TRAINING", "RANKING"}:
        raise ValueError("Unknown mode")
    if config["strategy"] not in STRATEGIES or config["opponent"] not in {"EASY", "MEDIUM", "HARD"}:
        raise ValueError("Unknown strategy or training opponent")
    if config["strategy"] == "hard-edge" and (config["mode"] != "TRAINING" or config["opponent"] != "HARD"):
        raise ValueError(f"{config['strategy']} is an experiment for TRAINING/HARD; use fleet-mc for other opponents")
    if config["strategy"] == "hard-quadrants" and config["mode"] == "TRAINING" and config["opponent"] != "HARD":
        raise ValueError("hard-quadrants requires HARD in TRAINING; RANKING uses the requested experimental prior")
    if config["matches"] < 1 or config["max_seconds"] < 1 or config["reconnect_attempts"] < 0:
        raise ValueError("Invalid run limits")
    env_file = args.env_file or (Path(".env") if Path(".env").is_file() else Path("../.env"))
    if not env_file.is_file():
        raise ValueError(f"Missing .env: create {Path('.env').resolve()} with BOT_TOKEN; see .env.example")
    env = dotenv_values(env_file)
    token = env.get("BOT_TOKEN", "")
    if not token or token.startswith("<"):
        raise ValueError("BOT_TOKEN is empty or a placeholder in .env")
    platform_id = env.get("BOT_PLATFORM_ID") or ""
    if platform_id:
        UUID(platform_id)
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    data = Path(config["data_dir"])
    data.mkdir(parents=True, exist_ok=True)
    # Avoid accidentally replacing our own connection with a second local process.
    with (data / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("bot2 already has a local running process") from None
        j = Journal(data, seed, config["strategy"], args.experiment, config,
                    secrets=tuple(v for k, v in env.items() if v and any(s in k.lower() for s in ("token", "password", "secret"))))
        status = "ERROR"
        try:
            print(f"{config['mode']}/{config['opponent'] if config['mode'] == 'TRAINING' else 'teams'}, strategy={config['strategy']}, matches={config['matches']}, seed={seed}. Matches change Elo.", flush=True)
            asyncio.run(run(config, token, platform_id, j, seed))
            status = "COMPLETED"
        except KeyboardInterrupt:
            status = "CANCELLED"
            print("Stopped by user; partial results saved.", file=sys.stderr)
            raise
        except Exception:
            j.write("local", "ERROR", {"traceback": traceback.format_exc()})
            raise
        finally:
            j.write("local", "RUN_FINISHED", {"status": status})
            j.close()
            ingest(j.path, data / "analytics.duckdb")
            print(json.dumps(report(data / "analytics.duckdb"), ensure_ascii=False, indent=2, default=str), flush=True)


def main():
    args = parser().parse_args()
    try:
        execute(args)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        # Error messages can contain URLs/headers; redact credentials even at CLI boundary.
        env_file = getattr(args, "env_file", None) or (Path(".env") if Path(".env").is_file() else Path("../.env"))
        values = dotenv_values(env_file) if env_file.is_file() else {}
        secret_values = tuple(v for k, v in values.items() if v and any(s in k.lower() for s in ("token", "password", "secret")))
        print(f"Error: {redact(str(exc), secret_values)}", file=sys.stderr)
        raise SystemExit(1) from None
