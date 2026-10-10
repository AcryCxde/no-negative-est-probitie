"""Training and ranking WebSocket runner. Decisions are driven by TURN_REQUESTED."""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .bonuses import BonusBag
from .game import STRATEGIES, cell_json, is_hunger, is_miss_race, is_schrodinger, is_speedrun, parse_cell
from .heat import CalibrationStore, HeatStore, hunt_prediction
from .telemetry import utc_now

LOW_EXPECTATIONS = "Низкие ожидания"


class Protocol:
    def __init__(self, config: dict, journal, seed: int):
        self.config, self.journal, self.seed = config, journal, seed
        self.strategy = None
        self.bonuses = None
        self.strategy_name = config["strategy"]
        self.finished = 0
        self.battles = []
        self.active = False
        self.placement_attempts = 0
        self.mode_confirmed = False
        self.last_send = 0.0
        self.own_team_name = None
        self.opponent_team = None
        self.modifier = None
        self.opponent_shots = []
        self.heat = None
        self.calibration = None
        self.hunt_records, self.pending_prediction = [], None
        self.last_move_number = None
        self.pending_speedrun_shot = None

    def interrupted(self, reason: str):
        if self.active:
            self.journal.write("local", "BATTLE_INTERRUPTED", {"reason": reason})
            self.active = False

    def start(self, event: dict):
        opponent = event.get("opponent", {})
        if self.own_team_name and opponent.get("teamName") == self.own_team_name:
            raise RuntimeError("Own team opponent assigned; stopping this session")
        # Explicit ranking experiments use the requested prior for known opponents.
        # Unknown snapshots and ranking assignments during TRAINING stay universal.
        selected_ranking = self.config.get("mode") == "RANKING" and opponent.get("kind") is not None
        self.strategy_name = (self.config["strategy"] if opponent.get("kind") == "BUILT_IN_BOT" or selected_ranking else "fleet-mc")
        if self.strategy_name == "ranking-lowexp":
            self.strategy_name = ("fleet-mc-lowexp" if opponent.get("teamName") == LOW_EXPECTATIONS
                                  and event["rules"].get("modifier") in (None, "DUEL", "THREE_MUSKETEERS")
                                  else "fleet-mc")
        if self.strategy_name == "ranking-auto":
            self.strategy_name = "fleet-mc"
        if self.config.get("mode") == "RANKING" and self.strategy_name == "fleet-mc" and is_miss_race(event["rules"]):
            self.strategy_name = "fleet-mc-duel-v2"
        # Other fleet-mc policies never close a ship without SUNK; baseline/density stay as explicit controls.
        if (is_schrodinger(event["rules"]) and self.strategy_name not in {"baseline", "density"}
                and not self.strategy_name.startswith("fleet-mc-schrodinger")):
            self.strategy_name = "fleet-mc-schrodinger"
        if (is_hunger(event["rules"]) and self.strategy_name not in {"baseline", "density"}
                and not self.strategy_name.startswith("fleet-mc-hunger")):
            self.strategy_name = "fleet-mc-hunger"
        # HARD placement prior. baseline/density stay explicit controls.
        if (is_speedrun(event["rules"]) and self.strategy_name not in {"baseline", "density"}
                and not self.strategy_name.startswith("fleet-mc-speedrun")):
            self.strategy_name = "fleet-mc-speedrun-v2"
        self.interrupted("new match started before prior result was received")
        self.journal.battle_id = str(uuid4())
        self.opponent_team = opponent.get("teamName")
        self.modifier = event["rules"].get("modifier")
        self.opponent_shots = []
        self.hunt_records, self.pending_prediction = [], None
        # Once per process; later matches of this run are added on MATCH_FINISHED.
        if getattr(STRATEGIES[self.strategy_name], "uses_heat", False) and self.heat is None:
            self.heat = HeatStore.from_logs(Path(self.config["data_dir"]) / "logs")
        if getattr(STRATEGIES[self.strategy_name], "uses_calibration", False) and self.calibration is None:
            self.calibration = CalibrationStore.from_logs(Path(self.config["data_dir"]) / "logs")
        self.strategy = self.make_strategy(event["rules"], self.seed + len(self.battles))
        self.bonuses = BonusBag(self.strategy, event) if self.config.get("use_bonuses", True) else BonusBag(self.strategy)
        self.active = True
        self.placement_attempts = 0
        self.last_move_number = None
        self.pending_speedrun_shot = None
        info = {"battle_id": self.journal.battle_id, "started_at": utc_now(), "opponent": opponent, "finished": False}
        self.battles.append(info)
        bonus_uses = dict(self.bonuses.uses)
        self.journal.write("local", "BATTLE_STARTED", {"rules": event["rules"], "opponent": opponent,
                                                      "strategy": self.strategy_name, "bonuses": bonus_uses})
        print(f"Battle: {opponent.get('builtInBot') or opponent.get('teamName') or 'unknown opponent'} "
              f"strategy={self.strategy_name} modifier={event['rules'].get('modifier')} bonuses={bonus_uses}", flush=True)

    def make_strategy(self, rules: dict, seed: int):
        strategy = STRATEGIES[self.strategy_name](rules, seed)
        strategy.opponent_team = self.opponent_team
        if getattr(strategy, "uses_heat", False) and self.heat is not None:
            strategy.heat = self.heat.heat_for(self.opponent_team)
        if getattr(strategy, "uses_calibration", False) and self.calibration is not None:
            strategy.zone_factors = self.calibration.factors(self.opponent_team)
        return strategy

    async def send(self, ws, message: dict):
        encoded = json.dumps(message, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("Outgoing message exceeds 8 KiB")
        # At most 50 actions/sec, including SPEEDRUN.
        await asyncio.sleep(max(0, self.last_send + 0.02 - time.monotonic()))
        await ws.send(encoded)
        self.last_send = time.monotonic()
        self.journal.write("out", message["type"], message)

    async def placement(self, ws):
        if self.strategy.rules.get("modifier") == "SPEEDRUN":
            return
        self.placement_attempts += 1
        if self.placement_attempts > 3:
            raise RuntimeError("Three placement rejections; stop and inspect protocol")
        started = time.monotonic()
        ships = self.strategy.place_fleet()
        if self.bonuses:
            self.bonuses.set_ships(ships)
        await self.send(ws, {"type": "PLACE", "ships": ships})
        self.journal.write("local", "PLACEMENT_DECISION", {"elapsed_ms": (time.monotonic()-started)*1000,
            "note": getattr(self.strategy, "placement_note", None),
            "heat_matches": self.heat.matches_for(self.opponent_team) if self.heat is not None else None})

    async def act(self, ws, event: dict):
        started = time.monotonic()
        if event["shotsLeft"] <= 0:
            return
        # A delayed SHOOT may be accepted after the server's timeout. Sending
        # another one before its result arrives creates duplicate counted MISSes
        # and a queue of actions based on stale state. Keep one shot in flight.
        if is_speedrun(self.strategy.rules) and self.pending_speedrun_shot is not None:
            self.journal.write("local", "ACTION_DEFERRED", {
                "reason": "speedrun: awaiting the previous shot result",
                "cell": cell_json(self.pending_speedrun_shot),
                "moveNumber": event["moveNumber"], "shotsLeft": event["shotsLeft"]})
            return
        move_number = event["moveNumber"]
        if self.bonuses and self.last_move_number is not None and move_number != self.last_move_number:
            self.bonuses.note_turn_passed()
        self.last_move_number = move_number
        self.strategy.shots_left = event["shotsLeft"]
        if self.bonuses and self.config.get("use_bonuses", True):
            kind, payload = self.bonuses.choose(event["shotsLeft"], event["shotsLeftInTurn"], move_number)
        else:
            cell, reason = self.strategy.choose()
            kind, payload = "SHOOT", {"cell": cell_json(cell), "reason": reason}
        reason = payload.pop("reason", "")
        message = {"type": kind, **{key: value for key, value in payload.items() if key != "reason"}}
        predicted = hunt_prediction(reason) if kind == "SHOOT" else None
        self.pending_prediction = (parse_cell(payload["cell"]), predicted) if predicted is not None else None
        await self.send(ws, message)
        if kind == "SHOOT" and is_speedrun(self.strategy.rules):
            self.pending_speedrun_shot = parse_cell(payload["cell"])
        self.journal.write("local", "DECISION", {**payload, "action": kind, "reason": reason,
            "elapsed_ms": (time.monotonic()-started)*1000, "moveNumber": move_number,
            "shotsLeft": event["shotsLeft"], "shotsLeftInTurn": event["shotsLeftInTurn"],
            "bonus_uses": dict(self.bonuses.uses) if self.bonuses else {}})

    async def handle(self, ws, event: dict):
        kind = event.get("type")
        if kind == "MODE_CHANGED":
            if event["mode"] != self.config.get("mode", "TRAINING"):
                raise RuntimeError("Server did not confirm requested mode")
            self.mode_confirmed = True
            print(f"{event['mode']} confirmed", flush=True)
        elif kind == "MODE_REJECTED":
            raise RuntimeError(f"Mode rejected: {event.get('reason')}")
        elif kind == "MATCH_STARTED":
            self.start(event)
            await self.placement(ws)
        elif kind == "MATCH_SNAPSHOT":
            # Snapshots omit opponent identity: resume with the universal policy
            # in a fresh process; known matches retain their selected strategy.
            if not self.active or self.strategy is None:
                self.start({"rules": event["rules"], "opponent": {}, "bonuses": event.get("bonuses") or []})
                self.journal.write("local", "MATCH_RESUMED", {"opponent_unknown": True})
            self.strategy = self.make_strategy(event["rules"], self.seed + len(self.battles)-1)
            self.pending_speedrun_shot = None
            self.bonuses = BonusBag(self.strategy, event) if self.config.get("use_bonuses", True) else BonusBag(self.strategy)
            for shot in event["shots"]:
                self.strategy.observe(shot)
            self.journal.write("local", "STATE_RESTORED", {"shots": len(event["shots"]), "shotsLeft": event["shotsLeft"],
                                                           "bonuses": dict(self.bonuses.uses)})
            if event.get("ships") is None and event.get("placementWindowLeftSeconds") is not None:
                await self.placement(ws)
        elif kind == "PLACEMENT_REJECTED":
            await self.placement(ws)
        elif kind == "TURN_REQUESTED":
            if not self.active:
                raise RuntimeError("Turn without active match")
            await self.act(ws, event)
        elif kind == "SHOT_RESOLVED" and self.active:
            if self.pending_speedrun_shot == parse_cell(event["cell"]):
                self.pending_speedrun_shot = None
            if self.pending_prediction and self.pending_prediction[0] == parse_cell(event["cell"]):
                self.hunt_records.append((*self.pending_prediction, event["result"] in ("HIT", "SUNK")))
                self.pending_prediction = None
            self.strategy.observe(event)
        elif kind == "BONUS_RESOLVED" and self.active and self.bonuses:
            self.bonuses.apply_resolved(event)
            self.journal.write("local", "BONUS_APPLIED", {"bonus": event.get("bonus"),
                                                          "uses": dict(self.bonuses.uses),
                                                          "forced": cell_json(self.bonuses.forced) if self.bonuses.forced else None})
        elif kind == "BONUS_REJECTED" and self.active:
            print(f"Rejected: BONUS_REJECTED {event.get('bonus')} {event.get('reason')}", flush=True)
            if self.bonuses:
                self.bonuses.apply_rejected(event)
            # No new TURN_REQUESTED: spend the same turn window on a shot.
            if event.get("reason") != "NOT_YOUR_TURN":
                cell, reason = self.strategy.choose()
                await self.send(ws, {"type": "SHOOT", "cell": cell_json(cell)})
                self.journal.write("local", "DECISION", {"action": "SHOOT", "cell": cell_json(cell),
                    "reason": f"bonus rejected ({event.get('reason')}); {reason}",
                    "bonus": event.get("bonus")})
        elif kind == "OPPONENT_SHOT" and self.active:
            self.opponent_shots.append((parse_cell(event["cell"]), event.get("result")))
            if self.bonuses:
                self.bonuses.note_opponent_shot(event)
        elif kind == "OPPONENT_BONUS_USED" and self.active and self.bonuses:
            bonus = event.get("bonus")
            if bonus and self.bonuses.opponent_uses[bonus] > 0:
                self.bonuses.opponent_uses[bonus] -= 1
            self.journal.write("local", "OPPONENT_BONUS", {"bonus": bonus})
        elif kind == "MATCH_FINISHED" and self.active:
            self.journal.write("local", "BATTLE_FINISHED", event)
            self.battles[-1].update(finished=True, outcome=event["outcome"])
            if self.heat is not None:
                self.heat.add(self.opponent_team, self.opponent_shots, self.modifier)
            if self.calibration is not None and self.modifier == "HUNGER_GAMES":
                rules = self.strategy.rules
                self.calibration.add(self.opponent_team, self.hunt_records, rules["rows"], rules["columns"])
            self.active = False
            self.pending_speedrun_shot = None
            self.finished += 1
            print(f"Battle {self.finished}: {event['outcome']} ({event['endReason']}); shots={self.strategy.shots}", flush=True)
            self.journal.battle_id = None
        elif kind in {"SHOT_REJECTED", "COMMAND_REJECTED"}:
            # Avoid retry floods; the next valid TURN_REQUESTED drives the next action.
            self.pending_speedrun_shot = None
            print(f"Rejected: {kind} {event.get('reason')}", flush=True)
        elif kind == "BET_REQUESTED":
            self.journal.write("local", "BET_SKIPPED", {"reason": "baseline does not spend rating on bets"})
        elif kind == "TURN_TIMED_OUT":
            print(f"TURN_TIMED_OUT move={event.get('moveNumber')}", flush=True)
            if self.bonuses:
                self.bonuses.note_turn_passed()
        elif kind not in {"PLACEMENT_ACCEPTED", "OPPONENT_SHOT", "OPPONENT_TURN_TIMED_OUT",
                          "OPPONENT_BONUS_USED", "BET_ACCEPTED", "BET_REJECTED", "BETTING_FINISHED"}:
            self.journal.write("local", "UNKNOWN_EVENT", {"type": kind})


async def http_value(client, path: str):
    response = await client.get(path)
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise ValueError("HTTP API returned an error")
    return body["value"]


async def statistics(config, journal, platform_id: str, phase: str):
    if not platform_id:
        return
    try:
        async with httpx.AsyncClient(base_url=config["api_url"], timeout=8) as client:
            value = await http_value(client, f"/api/v1/bots/{platform_id}/statistics?kind={config.get('mode', 'TRAINING')}")
            journal.write("http", "HTTP_STATISTICS", {"phase": phase, **value})
            return value
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        journal.write("local", "HTTP_ENRICHMENT_FAILED", {"phase": phase, "error": type(exc).__name__})
        print(f"HTTP statistics unavailable ({type(exc).__name__})", flush=True)


def same_opponent(ws_opponent: dict, http_opponent: dict) -> bool:
    if ws_opponent.get("kind") != http_opponent.get("kind"):
        return False
    if ws_opponent.get("kind") == "BUILT_IN_BOT":
        return ws_opponent.get("builtInBot") == http_opponent.get("builtInBot")
    if ws_opponent.get("kind") == "REMOTE_BOT":
        # Public HTTP journals identify the remote team by name, omitting botId.
        if not ws_opponent.get("teamName") or ws_opponent["teamName"] != http_opponent.get("name"):
            return False
        return not http_opponent.get("botId") or ws_opponent.get("botId") == http_opponent["botId"]
    return False


async def enrich(config, journal, platform_id: str, battles: list):
    if not platform_id:
        journal.write("local", "HTTP_ENRICHMENT_SKIPPED", {"reason": "BOT_PLATFORM_ID missing; matchId/rating remain NULL"})
        return
    await statistics(config, journal, platform_id, "after")
    try:
        async with httpx.AsyncClient(base_url=config["api_url"], timeout=8) as client:
            value = await http_value(client, f"/api/v1/bots/{platform_id}/journal?page=1")
            journal.write("http", "HTTP_BOT_JOURNAL", value)
            entries = [e for e in value["entries"] if e.get("matchRecord") and e.get("match")]
            for battle in battles:
                start = datetime.fromisoformat(battle["started_at"])
                candidates = [e for e in entries if abs((datetime.fromisoformat(e["occurredAt"].replace("Z", "+00:00"))-start).total_seconds()) < 5
                    and battle["opponent"].get("kind") is not None
                    and same_opponent(battle["opponent"], e["match"]["opponent"])
                    and (not battle["finished"] or e["matchRecord"].get("outcome") == battle.get("outcome"))]
                journal.battle_id = battle["battle_id"]
                if len(candidates) == 1:
                    entry = candidates[0]
                    journal.write("http", "HTTP_MATCH_LINKED", {"matchId": entry["match"]["matchId"],
                        "ratingChange": entry["matchRecord"].get("ratingChange"), "correlation": "unique time/opponent kind and team name or built-in level/outcome candidate", "record": entry["matchRecord"]})
                else:
                    journal.write("local", "HTTP_MATCH_UNRESOLVED", {"candidates": len(candidates)})
            journal.battle_id = None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        journal.write("local", "HTTP_ENRICHMENT_FAILED", {"error": type(exc).__name__})
        print(f"HTTP journal unavailable ({type(exc).__name__})", flush=True)


async def run(config: dict, token: str, platform_id: str, journal, seed: int) -> None:
    protocol = Protocol(config, journal, seed)
    before = await statistics(config, journal, platform_id, "before")
    if before:
        protocol.own_team_name = before.get("teamName")
    try:
        async with asyncio.timeout(config["max_seconds"]):
            for attempt in range(config["reconnect_attempts"] + 1):
                try:
                    async with connect(config["url"], additional_headers={"Authorization": f"Bearer {token}"},
                                       open_timeout=10, close_timeout=2, ping_interval=20, ping_timeout=20) as ws:
                        journal.write("local", "CONNECTED", {"attempt": attempt})
                        print("WebSocket connected", flush=True)
                        protocol.mode_confirmed = False
                        # First command, before reading any game messages.
                        mode_request = {"type": "CHANGE_MODE", "mode": config.get("mode", "TRAINING")}
                        if mode_request["mode"] == "TRAINING":
                            mode_request["opponent"] = config["opponent"]
                        await protocol.send(ws, mode_request)
                        while protocol.finished < config["matches"]:
                            raw = await ws.recv()
                            try:
                                event = json.loads(raw)
                                if not isinstance(event, dict):
                                    raise ValueError("JSON message is not an object")
                            except (ValueError, UnicodeDecodeError) as exc:
                                journal.write("local", "PROTOCOL_ERROR", {"error": type(exc).__name__, "raw": str(raw)})
                                continue
                            journal.write("in", str(event.get("type", "UNKNOWN")), event)
                            await protocol.handle(ws, event)
                        journal.write("local", "SESSION_COMPLETED", {"matches": protocol.finished})
                        return
                except InvalidStatus as exc:
                    journal.write("local", "CONNECTION_FAILED", {"status": exc.response.status_code})
                    if exc.response.status_code not in {500, 502, 503, 504}:
                        raise RuntimeError(f"WebSocket handshake HTTP {exc.response.status_code}") from None
                except ConnectionClosed as exc:
                    code = exc.rcvd.code if exc.rcvd else None
                    journal.write("local", "DISCONNECTED", {"code": code})
                    print(f"Disconnected: code={code}", flush=True)
                    if code in {4001, 4002, 4004, 4005, 1009}:
                        raise RuntimeError(f"Connection closed with terminal code {code}; inspect credentials/client") from None
                except (OSError, TimeoutError) as exc:
                    journal.write("local", "CONNECTION_FAILED", {"error": type(exc).__name__})
                if attempt == config["reconnect_attempts"]:
                    raise RuntimeError("Reconnect limit reached")
                await asyncio.sleep(min(2**attempt, 8))
    finally:
        protocol.interrupted("session ended before MATCH_FINISHED; server outcome unknown")
        # No HTTP work while the socket is open; one-battle run closes immediately.
        await enrich(config, journal, platform_id, protocol.battles)
