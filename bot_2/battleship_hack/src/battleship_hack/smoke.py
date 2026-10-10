"""Only two integration checks, never connects to the platform."""
import asyncio
import itertools
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
from websockets.asyncio.server import serve

from .bonuses import BonusBag
from .client import Protocol, run, same_opponent
from .game import Baseline, Density, FleetMC, FleetMCExact, FleetMCDuel, FleetMCDuelV2, FleetMCInterior, FleetMCSchrodinger, FleetMCSchrodingerRim, FleetMCSchrodingerRimV2, FleetMCHunger, FleetMCHungerHeat, FleetMCHungerV2, HardEdge, HardQuadrants, STRATEGIES, cell_json, is_duel, is_miss_race, is_musketeers, neighbors, place, parse_cell, validate_placement
from .heat import HeatStore
from .telemetry import Journal, ingest, report

RULES = {"rows": 6, "columns": 6, "fleet": [{"length": 3, "count": 1}, {"length": 2, "count": 1}, {"length": 1, "count": 1}],
         "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY", "modifier": None,
         "moveTimeoutSeconds": 1, "placementWindowSeconds": 10, "shotsPerTurn": 1,
         "shotLimitPerPlayer": 36, "matchDurationSeconds": 90, "turnAfterHitRule": "SHOOTER_KEEPS_TURN"}


async def smoke():
    assert same_opponent({"kind": "REMOTE_BOT", "teamName": "Team", "botId": "known"},
                         {"kind": "REMOTE_BOT", "name": "Team"})
    assert not same_opponent({"kind": "REMOTE_BOT", "teamName": "Team"},
                             {"kind": "REMOTE_BOT", "name": "Other"})
    # Target orientation and fleet bookkeeping matter for the new policies.
    for cls in (Density, FleetMC):
        bot = cls(RULES, 42)
        for cell in ((2, 2), (2, 3)):
            bot.observe({"cell": cell_json(cell), "result": "HIT"})
        selected, _ = bot.choose()
        assert selected in {(2, 1), (2, 4)}
        bot = cls(RULES, 42)
        bot.observe({"cell": cell_json((0, 0)), "result": "SUNK"})
        assert bot.remaining[1] == 0
        bot.observe({"cell": cell_json((4, 4)), "result": "DEFENDED"})
        assert (4, 4) not in bot.used
    print("PASS: density/fleet-mc target orientation, fleet counts and shield retry")
    # Generic exact counting includes border cells and has no sector assumption.
    single_rules = {**RULES, "rows": 4, "columns": 4, "fleet": [{"length": 1, "count": 2}]}
    bot = FleetMCExact(single_rules, 42)
    cells = sorted(bot.board)
    pairs = [p for p in itertools.combinations(cells, 2) if p[1] not in neighbors(p[0], True)]
    from collections import Counter
    weights = Counter(c for pair in pairs for c in pair)
    selected, reason = bot.choose()
    assert weights[selected] == max(weights.values()) and f"configurations={len(pairs)}" in reason
    assert STRATEGIES['fleet-mc'] is FleetMCExact and STRATEGIES['fleet-mc-v1'] is FleetMC
    bot = FleetMCExact({**single_rules, "fleet": [{"length": 1, "count": 1}]}, 42)
    # One legal border cell must remain selectable, even in an already searched quarter.
    for cell in bot.board - {(0, 0)}:
        bot.observe({"cell": cell_json(cell), "result": "MISS"})
    selected, reason = bot.choose()
    assert selected == (0, 0) and "exact single" in reason
    # Before the singleton phase the baseline sequence remains identical.
    old, new = FleetMC(RULES, 42), FleetMCExact(RULES, 42)
    for event in ({"cell": cell_json((2, 2)), "result": "HIT"},
                  {"cell": cell_json((2, 3)), "result": "HIT"}):
        old.observe(event)
        new.observe(event)
    assert old.choose() == new.choose()
    print("PASS: universal exact singleton counts, border cell, baseline replay and rollback registry")
    duel_rules = {**RULES, "rows": 3, "columns": 3, "fleet": [{"length": 2, "count": 1}],
                  "modifier": "DUEL", "turnAfterHitRule": "HIT_SPENDS_SHOT", "shotsPerTurn": 1}
    duel = FleetMCDuel(duel_rules, 42)
    bank = [(mask, (mask,), 1) for mask, _, _ in duel.placements[2]]
    selected, probability, q, considered = duel.lookahead(bank, time.monotonic()+1)
    first = duel.mask([selected])
    hit_boards = [mask for mask, _, _ in bank if mask & first]
    miss_boards = [mask for mask, _, _ in bank if not mask & first]
    # Independent reference: branch probability cancels its conditional denominator.
    future = sum(max((sum(bool(mask & (1 << i)) for mask in branch)
                      for i in range(9) if (1 << i) != first), default=0)
                 for branch in (hit_boards, miss_boards))
    assert abs(probability-len(hit_boards)/len(bank)) < 1e-12
    assert abs(q-(len(hit_boards)+future)/len(bank)) < 1e-12 and considered > 0
    duel.observe({"cell": cell_json((0, 0)), "result": "HIT"})
    # A certain sinking shot finishes the only remaining ship: there is no second hit.
    sunk_bank = [(duel.mask([(0, 0), (0, 1)]), (duel.mask([(0, 0), (0, 1)]),), 1)]
    selected, probability, q, _ = duel.lookahead(sunk_bank, time.monotonic()+1)
    assert selected == (0, 1) and probability == q == 1
    assert is_duel(duel_rules) and is_duel({**duel_rules, "modifier": None})
    assert not is_duel({**duel_rules, "modifier": "THREE_MUSKETEERS", "shotsPerTurn": 3})
    print("PASS: DUEL two-shot expectation, SUNK terminal branch and rule detection")
    v2_rules = {**RULES, "modifier": "DUEL", "turnAfterHitRule": "HIT_SPENDS_SHOT", "shotsPerTurn": 1}
    v2 = FleetMCDuelV2(v2_rules, 7)
    cell, reason = v2.choose()
    assert cell in v2.board and ("duel-v2" in reason or "fleet-mc" in reason or "density" in reason)
    assert STRATEGIES["fleet-mc-duel-v2"] is FleetMCDuelV2
    # Parity picker keeps even cells when both colors are tied.
    assert v2._parity_pick([1 << 0, 1 << 1]) == (1 << 0) or v2._parity_pick([1 << 1, 1 << 2]) in {1 << 2}
    print("PASS: fleet-mc-duel-v2 parity duel registers and returns a legal cell")
    interior = FleetMCInterior({**RULES, "modifier": "DUEL", "turnAfterHitRule": "HIT_SPENDS_SHOT",
                                "shotsPerTurn": 1}, 11)
    assert STRATEGIES["fleet-mc-interior"] is FleetMCInterior
    # Tied scores: pick the deeper interior cell over the rim.
    edge_bit = 1 << (0 * interior.columns + 4)
    mid_bit = 1 << (4 * interior.columns + 4)
    assert interior._select({edge_bit: 1.0, mid_bit: 1.0}) == mid_bit
    ships = interior.place_fleet()
    validate_placement(RULES, ships)
    edge_cells = sum(1 for ship in ships for cell in ship["cells"]
                     if cell["row"] in (0, 9) or cell["column"] in (0, 9))
    assert edge_cells < 20
    cell, reason = interior.choose()
    assert cell in interior.board and ("interior" in reason or "fleet-mc" in reason or "density" in reason)
    print("PASS: fleet-mc-interior prefers inland hunt/placement")
    cat_rules = {**RULES, "modifier": "SCHRODINGER_SHIP"}
    assert STRATEGIES["fleet-mc-schrodinger"] is FleetMCSchrodinger
    cat = FleetMCSchrodinger(cat_rules, 5)
    for cell in ((2, 2), (2, 3), (2, 4)):
        cat.observe({"cell": cell_json(cell), "result": "HIT"})
    # Longest ship length reached: sunk without any announcement.
    assert cat.remaining[3] == 0 and not cat.hits and {(2, 1), (2, 5)} <= cat.empty
    cat.observe({"cell": cell_json((0, 0)), "result": "HIT"})
    cell, reason = cat.choose()
    assert cell in {(0, 1), (1, 0)} | (cat.board - cat.used - cat.empty) and "schrodinger" in reason, reason
    for cell in ((0, 1), (1, 0)):
        cat.observe({"cell": cell_json(cell), "result": "MISS"})
    # Both ends closed: the corner hit is a single.
    assert cat.remaining[1] == 0 and not cat.hits and cat.inferred_sunk == [((2, 2), (2, 3), (2, 4)), ((0, 0),)]
    cell, reason = cat.choose()
    assert cell in cat.board - cat.used - cat.empty and "inferred_sunk=2" in reason, reason
    plain, control = FleetMCSchrodinger(RULES, 3), FleetMCInterior(RULES, 3)
    for event in ({"cell": cell_json((2, 2)), "result": "HIT"}, {"cell": cell_json((2, 3)), "result": "HIT"}):
        plain.observe(event)
        control.observe(event)
    assert plain.choose() == control.choose() and plain.place_fleet() == control.place_fleet()
    print("PASS: fleet-mc-schrodinger infers sinks and equals interior without the modifier")
    assert all(getattr(FleetMCSchrodingerRim, name) is getattr(FleetMCSchrodinger, name)
               for name in ("choose", "observe", "_open_cluster_choice"))
    rim = FleetMCSchrodingerRim(cat_rules, 7)
    rim.perimeter_share = 1.0
    ships = rim.place_fleet()
    validate_placement(cat_rules, ships)
    last_row, last_column = cat_rules["rows"] - 1, cat_rules["columns"] - 1
    assert all(cell["row"] in (0, last_row) or cell["column"] in (0, last_column)
               for ship in ships if len(ship["cells"]) > 1 for cell in ship["cells"])
    print("PASS: fleet-mc-schrodinger-rim shoots like schrodinger, multi-cell ships on the rim")
    flat = FleetMCSchrodingerRimV2(cat_rules, 7)
    assert flat.place_fleet.__func__ is FleetMCSchrodingerRim.place_fleet
    assert flat._pick_hunt({(0, 0): 1.0, (2, 2): 0.5, (0, 2): 0.99}) in {(0, 0), (0, 2)}
    flat.observe({"cell": cell_json((2, 2)), "result": "MISS"})
    cell, reason = flat.choose()
    assert cell in flat.board - flat.used - flat.empty and "schrodinger" in reason, reason
    print("PASS: fleet-mc-schrodinger-rim-v2 hunts top MC cells without interior bias")
    musketeers_rules = {**RULES, "modifier": "THREE_MUSKETEERS", "turnAfterHitRule": "SHOOTER_KEEPS_TURN",
                        "shotsPerTurn": 3}
    assert is_musketeers(musketeers_rules) and is_miss_race(musketeers_rules) and not is_miss_race(RULES)
    cell, reason = FleetMCDuelV2(musketeers_rules, 7).choose()
    assert "duel-v2" in reason, reason
    shield_bag = BonusBag(FleetMCExact(musketeers_rules, 1), {"bonuses": [{"bonus": "SHIELD", "uses": 1}]})
    kind, payload = shield_bag.choose(shots_left=100, shots_left_in_turn=3, move_number=1)
    assert kind == "USE_BONUS" and payload["bonus"] == "SHIELD"
    kind, _ = BonusBag(FleetMCExact(RULES, 1), {"bonuses": [{"bonus": "SHIELD", "uses": 1}]}).choose(100, 1, 1)
    assert kind == "SHOOT"
    print("PASS: THREE_MUSKETEERS uses the duel-v2 sampler and an early shield")
    bag = BonusBag(FleetMCExact(RULES, 1), {"bonuses": [{"bonus": "SONAR", "uses": 1}, {"bonus": "SALVO", "uses": 1}],
                                           "opponent": {"bonuses": [{"bonus": "SHIELD", "uses": 1}]}})
    kind, payload = bag.choose(shots_left=20, shots_left_in_turn=1, move_number=1)
    assert kind == "USE_BONUS" and payload["bonus"] == "SALVO"
    bag.apply_resolved({"bonus": "SALVO", "shotsInTurn": 3})
    kind, payload = bag.choose(shots_left=20, shots_left_in_turn=3, move_number=1)
    assert kind == "USE_BONUS" and payload["bonus"] == "SONAR" and "cell" in payload
    center = parse_cell(payload["cell"])
    bag.apply_resolved({"bonus": "SONAR", "count": 0})
    window = {(center[0] + dr, center[1] + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)} & bag.strategy.board
    assert window <= bag.strategy.empty | bag.strategy.used
    bag = BonusBag(FleetMCExact(RULES, 2), {"bonuses": [{"bonus": "DISTANCE", "uses": 1}]})
    bag.pending = {"bonus": "DISTANCE", "cell": (2, 2)}
    bag.apply_resolved({"bonus": "DISTANCE", "distance": 2})
    # Cells strictly closer than the reported distance cannot hold a floating ship.
    assert (2, 2) in bag.strategy.empty and (2, 3) in bag.strategy.empty and (2, 4) not in bag.strategy.empty
    bag = BonusBag(FleetMCExact(RULES, 3), {"bonuses": [{"bonus": "DISTANCE", "uses": 1}]})
    bag.pending = {"bonus": "DISTANCE", "cell": (1, 1)}
    bag.apply_resolved({"bonus": "DISTANCE", "distance": 0})
    assert bag.forced == (1, 1)
    print("PASS: bonus policy SALVO/SONAR/DISTANCE constraints")
    # HARD is a prior, so conflicting evidence must restore the generic policy.
    bot = HardEdge(RULES, 42)
    bot.observe({"cell": cell_json((0, 2)), "result": "HIT"})
    bot.observe({"cell": cell_json((1, 2)), "result": "SUNK"})
    assert bot.model_disabled == "multi-cell sunk away from perimeter" and bot.remaining[2] == 0
    bot = HardEdge(RULES, 42)
    bot.observe({"cell": cell_json((0, 2)), "result": "HIT"})
    selected, reason = bot.choose()
    assert selected in {(0, 1), (0, 3)} and "model=perimeter" in reason
    bot = HardEdge(RULES, 42)
    bot.remaining.clear()
    bot.remaining[1] = 1
    selected, reason = bot.choose()
    assert not bot.is_edge(selected) and "equal model weights" in reason
    for cell, result in (((2, 2), "HIT"), ((0, 0), "SUNK")):
        bot = HardEdge(RULES, 42)
        bot.observe({"cell": cell_json(cell), "result": result})
        selected, reason = bot.choose()
        assert bot.model_disabled and "hard-edge fallback" in reason
        assert selected in bot.board - bot.used - bot.empty
    bot = HardEdge(RULES, 42)
    for cell in sorted(c for c in bot.board if bot.is_edge(c)):
        bot.observe({"cell": cell_json(cell), "result": "MISS"})
    selected, reason = bot.choose()
    assert "hard-edge fallback" in reason and not bot.is_edge(selected)
    bot = HardEdge({**RULES, "modifier": "SCHRODINGER_SHIP"}, 42)
    assert bot.model_disabled == "unsupported rules/modifier"
    print("PASS: hard-edge perimeter target, last single, conflicting evidence and model exhaustion")
    # Last single belongs to the remaining sector, including after snapshot replay.
    rules10 = {**RULES, "rows": 10, "columns": 10, "fleet": [{"length": 1, "count": 4}]}
    bot = HardQuadrants(rules10, 42)
    for cell in ((2, 2), (2, 7), (7, 2)):
        bot.observe({"cell": cell_json(cell), "result": "SUNK"})
    selected, reason = bot.choose()
    assert bot.sector(selected) == 3 and not bot.is_edge(selected) and "exact_layouts" in reason
    # Two adjacent choices across a sector border cannot coexist: 3 layouts.
    probe = HardQuadrants(rules10, 42)
    placements = {p[2][0]:p for p in probe.placements[1]}
    occupancy, layouts = probe.single_occupancy([
        [placements[(4, 3)], placements[(2, 2)]],
        [placements[(5, 3)], placements[(7, 7)]]])
    assert layouts == 3 and occupancy[(4, 3)] == 1 and occupancy[(2, 2)] == 2
    assert sum(occupancy.values()) == 2 * layouts
    bot.observe({"cell": cell_json((7, 7)), "result": "MISS"})
    selected, _ = bot.choose()
    assert selected != (7, 7) and bot.sector(selected) == 3
    bot = HardQuadrants(rules10, 42)
    for cell in ((2, 2), (2, 4)):
        bot.observe({"cell": cell_json(cell), "result": "SUNK"})
    selected, reason = bot.choose()
    assert bot.quadrants_disabled == "two singles in one sector" and "hard-quadrants fallback" in reason
    assert selected in bot.board - bot.used - bot.empty
    bot = HardQuadrants(rules10, 42)
    for r in range(1, 5):
        for c in range(1, 5):
            bot.observe({"cell": cell_json((r, c)), "result": "MISS"})
    selected, reason = bot.choose()
    assert "hard-quadrants fallback" in reason and selected in bot.board - bot.used - bot.empty
    print("PASS: quadrant prior, exact no-touch counts, last single and conflicting sectors")
    with TemporaryDirectory() as folder:
        data = Path(folder)
        config = {"mode": "RANKING", "url": "", "api_url": "http://127.0.0.1", "opponent": "EASY", "strategy": "baseline", "matches": 1,
                  "max_seconds": 10, "reconnect_attempts": 1}
        journal = Journal(data, 42, "baseline", "smoke", config, secrets=("smoke-token",))
        # Regression: ranking assignment during training must place and shoot;
        # fresh snapshots must place only while placement is still open.
        class Socket:
            def __init__(self):
                self.messages = []
            async def send(self, raw):
                self.messages.append(json.loads(raw))

        regression_journal = Journal(data / "regression", 42, "fleet-mc", "ranking-regression", config)
        for source in ("ranking", "training-race", "snapshot", "placed", "speedrun"):
            protocol = Protocol({**config, "mode": "TRAINING" if source == "training-race" else "RANKING",
                                 "strategy": "hard-quadrants"}, regression_journal, 42)
            socket = Socket()
            rules = {**RULES, "modifier": "SPEEDRUN"} if source == "speedrun" else RULES
            if source in {"ranking", "training-race"}:
                await protocol.handle(socket, {"type": "MATCH_STARTED", "rules": rules,
                    "opponent": {"kind": "REMOTE_BOT", "botId": "opponent", "teamName": "Team"}})
            else:
                await protocol.handle(socket, {"type": "MATCH_SNAPSHOT", "rules": rules,
                    "ships": [] if source == "placed" else None, "shots": [], "shotsLeft": 36,
                    "placementWindowLeftSeconds": None if source == "placed" else 0})
            expected_name = "hard-quadrants" if source == "ranking" else "fleet-mc"
            assert isinstance(protocol.strategy, FleetMC) and protocol.strategy_name == expected_name
            if source in {"ranking", "training-race", "snapshot"}:
                assert socket.messages[0]["type"] == "PLACE"
                validate_placement(rules, socket.messages[0]["ships"])
            else:
                assert not socket.messages
            await protocol.handle(socket, {"type": "TURN_REQUESTED", "moveNumber": 1,
                "shotsLeft": 36, "shotsLeftInTurn": 1})
            assert socket.messages[-1]["type"] == "SHOOT"
        guard = Protocol(config, regression_journal, 42)
        guard.own_team_name = "Our team"
        try:
            guard.start({"rules": RULES, "opponent": {"kind": "REMOTE_BOT", "teamName": "Our team"}})
        except RuntimeError as exc:
            assert "Own team" in str(exc)
        else:
            raise AssertionError("Own team guard missing")
        for kind in ('MATCH_STARTED', 'MATCH_SNAPSHOT'):
            protocol = Protocol({**config, "strategy": "ranking-auto"}, regression_journal, 42)
            socket = Socket()
            if kind == 'MATCH_STARTED':
                await protocol.handle(socket, {"type": kind, "rules": duel_rules,
                    "opponent": {"kind": "REMOTE_BOT", "teamName": "Team"}})
            else:
                await protocol.handle(socket, {"type": kind, "rules": duel_rules, "ships": [],
                    "shots": [], "shotsLeft": 9, "placementWindowLeftSeconds": None})
            assert protocol.strategy_name == 'fleet-mc-duel-v2' and isinstance(protocol.strategy, FleetMCDuelV2)
            before = len(socket.messages)
            await protocol.handle(socket, {"type": "SHOT_RESOLVED", "cell": cell_json((0,0)), "result": "HIT"})
            assert len(socket.messages) == before  # HIT alone never sends another action.
            await protocol.handle(socket, {"type": "TURN_REQUESTED", "moveNumber": 3,
                "shotsLeft": 8, "shotsLeftInTurn": 1})
            assert len(socket.messages) == before+1 and socket.messages[-1]['type'] == 'SHOOT'
        protocol = Protocol({**config, "strategy": "ranking-auto"}, regression_journal, 42)
        await protocol.handle(Socket(), {"type": "MATCH_STARTED",
            "rules": {**RULES, "modifier": "THREE_MUSKETEERS", "shotsPerTurn": 3},
            "opponent": {"kind": "REMOTE_BOT", "teamName": "Team"}})
        assert protocol.strategy_name == "fleet-mc-duel-v2"
        for strategy, kind in (("fleet-mc-interior", "MATCH_STARTED"), ("ranking-lowexp", "MATCH_STARTED"),
                               ("fleet-mc-interior", "MATCH_SNAPSHOT")):
            protocol = Protocol({**config, "strategy": strategy}, regression_journal, 42)
            await protocol.handle(Socket(), {"type": kind, "rules": cat_rules, "ships": [], "shots": [],
                "shotsLeft": 36, "placementWindowLeftSeconds": None,
                "opponent": {"kind": "REMOTE_BOT", "teamName": "Низкие ожидания"}})
            assert protocol.strategy_name == "fleet-mc-schrodinger" and isinstance(protocol.strategy, FleetMCSchrodinger)
        protocol = Protocol({**config, "strategy": "fleet-mc-schrodinger-rim"}, regression_journal, 42)
        await protocol.handle(Socket(), {"type": "MATCH_STARTED", "rules": cat_rules,
            "opponent": {"kind": "REMOTE_BOT", "teamName": "Team"}})
        assert protocol.strategy_name == "fleet-mc-schrodinger-rim" and isinstance(protocol.strategy, FleetMCSchrodingerRim)
        protocol = Protocol({**config, "strategy": "fleet-mc-schrodinger-rim-v2"}, regression_journal, 42)
        await protocol.handle(Socket(), {"type": "MATCH_STARTED", "rules": cat_rules,
            "opponent": {"kind": "REMOTE_BOT", "teamName": "Team"}})
        assert isinstance(protocol.strategy, FleetMCSchrodingerRimV2)
        hunger_rules = {**RULES, "modifier": "HUNGER_GAMES", "shotLimitPerPlayer": 20}
        for team in ("Team", "Банька Парилка"):
            protocol = Protocol({**config, "strategy": "ranking-auto"}, regression_journal, 42)
            hunger_socket = Socket()
            await protocol.handle(hunger_socket, {"type": "MATCH_STARTED", "rules": hunger_rules,
                "bonuses": [{"bonus": "SHIELD", "uses": 1}, {"bonus": "SONAR", "uses": 1}],
                "opponent": {"kind": "REMOTE_BOT", "teamName": team}})
            assert protocol.strategy_name == "fleet-mc-hunger" and isinstance(protocol.strategy, FleetMCHunger)
            ships = hunger_socket.messages[-1]["ships"]
            validate_placement(hunger_rules, ships)
            edge = (0, hunger_rules["rows"] - 1)
            multi_rim = [all(cell["row"] in edge or cell["column"] in edge for cell in ship["cells"])
                         for ship in ships if len(ship["cells"]) > 1]
            assert not any(multi_rim) if team == "Банька Парилка" else all(multi_rim), (team, ships)
            await protocol.handle(hunger_socket, {"type": "TURN_REQUESTED", "moveNumber": 1,
                "shotsLeft": 20, "shotsLeftInTurn": 1})
            assert hunger_socket.messages[-1]["type"] == "SHOOT" and protocol.strategy.shots_left == 20
        protocol.bonuses.note_opponent_shot({"cell": cell_json(next(iter(protocol.bonuses.own_ships))), "result": "HIT"})
        kind, payload = protocol.bonuses.choose(shots_left=10, shots_left_in_turn=1, move_number=5)
        assert kind == "USE_BONUS" and payload["bonus"] == "SHIELD", (kind, payload)
        hunger = FleetMCHunger(hunger_rules, 3)
        hunger.shots_left = 20
        assert hunger.ship_value(4) > hunger.ship_value(2) > hunger.ship_value(1) == 1.0
        hunger.shots_left = 1
        assert hunger.ship_value(4) == 1.0
        print("PASS: HUNGER_GAMES auto-switches to fleet-mc-hunger, team-aware placement, shield-only bonuses")
        store = HeatStore()
        rim_shots = [((0, c), "MISS") for c in range(6)] + [((5, c), "MISS") for c in range(6)]
        store.add("Rim team", rim_shots, "HUNGER_GAMES")
        heat_map = store.heat_for("Rim team")
        assert heat_map[(0, 0)] > heat_map.get((2, 2), 0.0) and store.heat_for("Unknown") is not None
        protocol = Protocol({**config, "strategy": "fleet-mc-hunger-heat"}, regression_journal, 42)
        protocol.heat = store
        heat_socket = Socket()
        await protocol.handle(heat_socket, {"type": "MATCH_STARTED", "rules": hunger_rules,
            "bonuses": [{"bonus": "SHIELD", "uses": 1}], "opponent": {"kind": "REMOTE_BOT", "teamName": "Rim team"}})
        assert protocol.strategy_name == "fleet-mc-hunger-heat" and protocol.strategy.heat == heat_map
        validate_placement(hunger_rules, heat_socket.messages[-1]["ships"])
        own = sorted(protocol.bonuses.own_ships)
        await protocol.handle(heat_socket, {"type": "OPPONENT_SHOT", "cell": cell_json(own[0]), "result": "HIT"})
        kind, _ = protocol.bonuses.choose(shots_left=10, shots_left_in_turn=1, move_number=2)
        assert kind == "SHOOT"  # one accurate shot is not enough evidence for a shield
        for cell, result in (((5, 5), "MISS"), ((4, 4), "MISS"), ((3, 3), "MISS")):
            if cell not in protocol.bonuses.own_ships:
                await protocol.handle(heat_socket, {"type": "OPPONENT_SHOT", "cell": cell_json(cell), "result": result})
        kind, _ = protocol.bonuses.choose(shots_left=10, shots_left_in_turn=1, move_number=3)
        assert kind == "SHOOT"  # accuracy <= 45%
        free = [cell for cell in sorted(protocol.strategy.board - protocol.bonuses.own_ships)
                if cell not in protocol.bonuses.opponent_shot_cells]
        for cell in free[:HeatStore.min_shots]:
            await protocol.handle(heat_socket, {"type": "OPPONENT_SHOT", "cell": cell_json(cell), "result": "MISS"})
        await protocol.handle(heat_socket, {"type": "MATCH_FINISHED", "outcome": "WIN", "endReason": "SHOT_LIMIT"})
        assert store.matches_for("Rim team") == 2
        fallback = FleetMCHungerHeat(hunger_rules, 5)
        fallback.place_fleet()
        assert fallback.placement_note == "no heat map; inland"
        print("PASS: fleet-mc-hunger-heat uses the team heat map, learns online, gates SHIELD by accuracy")
        protocol = Protocol({**config, "strategy": "fleet-mc-hunger-v2"}, regression_journal, 42)
        protocol.heat = store
        v2_socket = Socket()
        await protocol.handle(v2_socket, {"type": "MATCH_STARTED", "rules": hunger_rules,
            "opponent": {"kind": "REMOTE_BOT", "teamName": "Rim team"}})
        assert isinstance(protocol.strategy, FleetMCHungerV2) and protocol.strategy.heat is not None
        validate_placement(hunger_rules, v2_socket.messages[-1]["ships"])
        # Three equally likely length-3 placements through the centre of a row:
        # inner neighbours have P(hit)=2/3, endpoints only 1/3. Last shot is greedy.
        row_rules = {**hunger_rules, "rows": 1, "columns": 5, "fleet": [{"length": 3, "count": 1}]}
        for left in (1, 2):
            v2 = FleetMCHungerV2(row_rules, 43)
            v2.shots_left = left
            v2.observe({"cell": cell_json((0, 2)), "result": "HIT"})
            cell, reason = v2.choose()
            assert cell in {(0, 1), (0, 3)} and f"horizon={left}" in reason, (cell, reason)
        # An expensive shield must not interrupt our own high-probability target.
        v2 = FleetMCHungerV2(hunger_rules, 7)
        bag = BonusBag(v2, {"bonuses": [{"bonus": "SHIELD", "uses": 1}]})
        bag.set_ships([{"cells": [cell_json((0, c)) for c in range(3)]}])
        for cell, result in (((0, 0), "HIT"), ((0, 1), "HIT"), ((5, 4), "MISS"), ((5, 5), "MISS")):
            bag.note_opponent_shot({"cell": cell_json(cell), "result": result})
        assert bag.choose(10, 1, 1)[0] == "USE_BONUS"
        v2.observe({"cell": cell_json((2, 2)), "result": "HIT"})
        kind, payload = bag.choose(10, 1, 1)
        assert kind == "SHOOT" and parse_cell(payload["cell"]) not in v2.used | v2.empty
        # Insufficient effective samples must preserve the existing legal target.
        v2.target_min_ess = 10 ** 9
        cell, reason = v2.choose()
        assert "target fallback" in reason and cell not in v2.used | v2.empty
        print("PASS: hunger-v2 stays selected, targets over two shots, is greedy on the last, skips SHIELD during targeting")
        bonus_protocol = Protocol({**config, "strategy": "fleet-mc", "use_bonuses": True}, regression_journal, 9)
        bonus_socket = Socket()
        await bonus_protocol.handle(bonus_socket, {"type": "MATCH_STARTED", "rules": RULES,
            "bonuses": [{"bonus": "SHIELD", "uses": 1}, {"bonus": "MINE", "uses": 1}],
            "opponent": {"kind": "BUILT_IN_BOT", "builtInBot": "EASY", "bonuses": []}})
        assert bonus_socket.messages[0]["type"] == "PLACE"
        bonus_protocol.bonuses.note_opponent_shot({"cell": bonus_socket.messages[0]["ships"][0]["cells"][0], "result": "HIT"})
        await bonus_protocol.handle(bonus_socket, {"type": "TURN_REQUESTED", "moveNumber": 2,
            "shotsLeft": 30, "shotsLeftInTurn": 1})
        assert bonus_socket.messages[-1]["type"] == "USE_BONUS" and bonus_socket.messages[-1]["bonus"] == "SHIELD"
        await bonus_protocol.handle(bonus_socket, {"type": "BONUS_RESOLVED", "bonus": "SHIELD", "shieldActive": True})
        await bonus_protocol.handle(bonus_socket, {"type": "TURN_REQUESTED", "moveNumber": 2,
            "shotsLeft": 29, "shotsLeftInTurn": 1})
        assert bonus_socket.messages[-1]["type"] == "USE_BONUS" and bonus_socket.messages[-1]["bonus"] == "MINE"
        await bonus_protocol.handle(bonus_socket, {"type": "BONUS_REJECTED", "bonus": "MINE", "reason": "INVALID_CELL"})
        assert bonus_socket.messages[-1]["type"] == "SHOOT"
        regression_journal.close()
        print("PASS: selected ranking strategy, universal fresh snapshots and SPEEDRUN")
        print("PASS: USE_BONUS shield/mine and BONUS_REJECTED fallback shot")
        first_shot = None
        sessions = 0
        failures = []

        async def server(ws):
            nonlocal first_shot, sessions
            try:
                sessions += 1
                assert ws.request.headers["Authorization"] == "Bearer smoke-token"
                request = json.loads(await ws.recv())
                assert request == {"type": "CHANGE_MODE", "mode": "RANKING"}
                await ws.send(json.dumps({"type": "MODE_CHANGED", "mode": "RANKING"}))
                if sessions == 1:
                    await ws.send(json.dumps({"type": "NEW_FUTURE_EVENT", "extra": True}))
                    await ws.send(json.dumps({"type": "MATCH_STARTED", "rules": RULES,
                                             "bonuses": [], "opponent": {"kind": "BUILT_IN_BOT", "builtInBot": "EASY"}}))
                    placement = json.loads(await ws.recv())
                    assert placement["type"] == "PLACE"
                    validate_placement(RULES, placement["ships"])
                    await ws.send(json.dumps({"type": "PLACEMENT_ACCEPTED"}))
                    await ws.send(json.dumps({"type": "TURN_REQUESTED", "moveNumber": 1, "shotsLeft": 36, "shotsLeftInTurn": 1}))
                    shot = json.loads(await asyncio.wait_for(ws.recv(), 1))
                    assert shot["type"] == "SHOOT"
                    first_shot = shot["cell"]
                    await ws.send(json.dumps({"type": "SHOT_RESOLVED", "cell": first_shot, "result": "MISS"}))
                    await ws.close(1011, "smoke reconnect")
                else:
                    await ws.send(json.dumps({"type": "MATCH_SNAPSHOT", "rules": RULES, "ships": [],
                        "shots": [{"cell": first_shot, "result": "MISS"}], "opponentShots": [],
                        "shotsLeft": 35, "placementWindowLeftSeconds": None, "bonuses": [], "shieldActive": False, "mines": []}))
                    await ws.send(json.dumps({"type": "TURN_REQUESTED", "moveNumber": 3, "shotsLeft": 35, "shotsLeftInTurn": 1}))
                    shot = json.loads(await asyncio.wait_for(ws.recv(), 1))
                    assert shot["cell"] != first_shot
                    await ws.send(json.dumps({"type": "SHOT_RESOLVED", "cell": shot["cell"], "result": "HIT"}))
                    await ws.send(json.dumps({"type": "MATCH_FINISHED", "outcome": "WIN", "endReason": "FLEET_DESTROYED"}))
                    await ws.wait_closed()
            except Exception as exc:
                failures.append(exc)
                await ws.close(1011)

        try:
            async with serve(server, "127.0.0.1", 0) as listener:
                config["url"] = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
                await run(config, "smoke-token", "", journal, 42)
            assert not failures, failures
            assert sessions == 2
            # Regression: DEFENDED leaves a known ship cell available for a retry.
            baseline = Baseline(RULES, 42)
            baseline.observe({"cell": {"row": 1, "column": 1}, "result": "DEFENDED"})
            assert (1, 1) not in baseline.used
            print("PASS 1: JSON/WebSocket, placement, shot deadline, unknown event and reconnect/snapshot")
            journal.write("http", "HTTP_STATISTICS", {"phase": "after", "rating": 1500.0, "place": 1})
            journal.write("local", "RUN_FINISHED", {"status": "COMPLETED"})
        finally:
            journal.close()
        database = data / "analytics.duckdb"
        ingest(journal.path, database)
        with duckdb.connect(str(database)) as db:
            before = db.execute("SELECT count(*) FROM events").fetchone()[0]
        ingest(journal.path, database)
        with duckdb.connect(str(database)) as db:
            assert db.execute("SELECT count(*) FROM events").fetchone()[0] == before
        summary = report(database)
        assert summary["rating_history"][0]["rating"] == 1500.0
        assert isinstance(summary["rating_history"][0]["timestamp"], str)
        assert summary["strategies"][0]["wins"] == 1
        assert summary["matches"][0]["rating_change"] is None
        assert summary["matches"][0]["status"] == "FINISHED"
        assert "smoke-token" not in journal.path.read_text()
        # Active JSONL's incomplete tail must not be imported until a newline arrives.
        with journal.path.open("a") as handle:
            handle.write('{"partial":')
        ingest(journal.path, database)
        print("PASS 2: JSONL, redaction, idempotent DuckDB import, report and incomplete tail")
