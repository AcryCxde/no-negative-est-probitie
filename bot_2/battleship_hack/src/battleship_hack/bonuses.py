"""Bonus inventory and a conservative USE_BONUS policy.

Bonuses cost Elo after the match, so this prefers a few high-value uses over
spending the whole allotment. Effects from SONAR/DISTANCE update the shooting
model; SALVO/SHIELD/MINE are timing heuristics.
"""
from __future__ import annotations

from collections import Counter

from .game import Cell, cell_json, is_hunger, is_musketeers, is_speedrun, neighbors, parse_cell


class BonusBag:
    def __init__(self, strategy, event: dict | None = None):
        self.strategy = strategy
        self.uses: Counter = Counter()
        self.opponent_uses: Counter = Counter()
        self.shield_active = False
        self.mines: set[Cell] = set()
        self.own_ships: set[Cell] = set()
        self.own_fleet: list[set[Cell]] = []
        self.opponent_shot_cells: set[Cell] = set()
        self.opponent_hit_cells: set[Cell] = set()
        self.opponent_shots = 0
        self.pending: dict | None = None
        self.forced: Cell | None = None
        self.salvo_ready = False
        self.reject_streak = 0
        if event:
            self.load(event)

    @property
    def enabled(self) -> bool:
        if self.strategy.rules.get("modifier") == "OLD_SCHOOL":
            return False
        return any(self.uses.values())

    def load(self, event: dict) -> None:
        self.uses = Counter({entry["bonus"]: entry["uses"] for entry in event.get("bonuses") or []})
        opponent = event.get("opponent") or {}
        self.opponent_uses = Counter({entry["bonus"]: entry["uses"] for entry in opponent.get("bonuses") or []})
        if "shieldActive" in event:
            self.shield_active = bool(event["shieldActive"])
        if "mines" in event:
            self.mines = {parse_cell(cell) if isinstance(cell, dict) else tuple(cell)
                          for cell in event.get("mines") or []}
        if event.get("ships"):
            self.set_ships(event["ships"])
        for shot in event.get("opponentShots") or []:
            self.note_opponent_shot(shot)

    def set_ships(self, ships: list[dict]) -> None:
        self.own_fleet = [{parse_cell(cell) for cell in ship["cells"]} for ship in ships]
        self.own_ships = set().union(*self.own_fleet)

    def note_opponent_shot(self, event: dict) -> None:
        cell = parse_cell(event["cell"])
        self.opponent_shot_cells.add(cell)
        self.opponent_shots += 1
        if event.get("result") in {"HIT", "SUNK"}:
            self.opponent_hit_cells.add(cell)
        if event.get("result") == "MINE":
            self.mines.discard(cell)
        if event.get("result") in {"HIT", "SUNK", "DEFENDED"}:
            # Opponent found our fleet: shield becomes more valuable.
            pass

    def board(self) -> set[Cell]:
        if hasattr(self.strategy, "board"):
            return self.strategy.board
        rules = self.strategy.rules
        return {(r, c) for r in range(rules["rows"]) for c in range(rules["columns"])}

    def choose(self, shots_left: int, shots_left_in_turn: int, move_number: int) -> tuple[str, dict]:
        """Return ('SHOOT'|'USE_BONUS', payload with reason for the journal)."""
        if self.forced and self.forced not in self.strategy.used | self.strategy.empty:
            cell = self.forced
            self.forced = None
            return "SHOOT", {"cell": cell_json(cell), "reason": "bonus forced ship cell"}
        self.forced = None
        if not self.enabled or self.reject_streak >= 2 or shots_left <= 0:
            return self._shoot("bonus skipped")
        # SPEEDRUN scores shots to sink. DISTANCE/SONAR/SHIELD/MINE spend one,
        # SHIELD and MINE protect a fleet we do not have, SALVO does not reduce
        # the number of shots that still have to be fired.
        if is_speedrun(self.strategy.rules):
            return self._shoot("speedrun: a bonus spends a counted shot")
        if is_hunger(self.strategy.rules):
            return self._hunger_choice(shots_left)
        remaining = getattr(self.strategy, "remaining", None)
        ships_left = sum(remaining.values()) if remaining is not None else 10
        # Prefer finishing open hits with SALVO before spending intel bonuses.
        if (self.uses["SALVO"] > 0 and not self.salvo_ready and shots_left_in_turn <= 1
                and shots_left >= 2 and (self.strategy.hits or ships_left >= 3)):
            return self._bonus("SALVO", None, "salvo to finish or accelerate hunt")
        # THREE_MUSKETEERS: hits keep the opponent's turn, DEFENDED ends it at once.
        if (self.uses["SHIELD"] > 0 and not self.shield_active and shots_left > 4
                and (is_musketeers(self.strategy.rules) or self._under_threat()
                     or (move_number >= 6 and self.opponent_uses["SALVO"] > 0))):
            return self._bonus("SHIELD", None, "shield while threatened or facing salvo")
        if self.uses["MINE"] > 0 and self.own_ships and shots_left > 3:
            cell = self._mine_cell()
            if cell is not None:
                return self._bonus("MINE", cell, "mine near own fleet")
        endgame_distance = getattr(self.strategy, "distance_policy", None) == "endgame"
        if (endgame_distance and self.uses["DISTANCE"] > 0 and not self.strategy.hits and shots_left > 1
                and remaining is not None and 0 < ships_left <= 4
                and not any(length > 2 for length in +remaining)):
            cell = self.strategy.distance_probe()
            if cell is not None:
                return self._bonus("DISTANCE", cell, "distance endgame probe")
        if not self.strategy.hits and shots_left > 6:
            if self.uses["SONAR"] > 0:
                cell = self._sonar_cell()
                if cell is not None:
                    return self._bonus("SONAR", cell, "sonar hunt probe")
            if self.uses["DISTANCE"] > 0 and not endgame_distance:
                cell = self._distance_cell()
                if cell is not None:
                    return self._bonus("DISTANCE", cell, "distance hunt probe")
        return self._shoot("shoot after bonus policy")

    def apply_resolved(self, event: dict) -> None:
        bonus = event["bonus"]
        if self.uses[bonus] > 0:
            self.uses[bonus] -= 1
        self.reject_streak = 0
        pending = self.pending if self.pending and self.pending.get("bonus") == bonus else None
        self.pending = None
        if bonus == "SALVO":
            self.salvo_ready = True
        elif bonus == "SHIELD":
            self.shield_active = bool(event.get("shieldActive", True))
        elif bonus == "MINE" and event.get("mineCell"):
            self.mines.add(parse_cell(event["mineCell"]))
        elif bonus == "SONAR" and pending and event.get("count") is not None:
            self._apply_sonar(pending["cell"], int(event["count"]))
        elif bonus == "DISTANCE" and pending and event.get("distance") is not None:
            self._apply_distance(pending["cell"], int(event["distance"]))

    def apply_rejected(self, event: dict) -> None:
        self.reject_streak += 1
        self.pending = None
        if event.get("reason") == "BONUS_ALREADY_ACTIVE" and event.get("bonus") == "SALVO":
            self.salvo_ready = True
        if event.get("reason") == "BONUS_ALREADY_ACTIVE" and event.get("bonus") == "SHIELD":
            self.shield_active = True

    def note_turn_passed(self) -> None:
        # SALVO lasts only for the current turn; a new turn starts without it.
        self.salvo_ready = False

    def _shoot(self, reason: str) -> tuple[str, dict]:
        cell, shoot_reason = self.strategy.choose()
        return "SHOOT", {"cell": cell_json(cell), "reason": f"{reason}; {shoot_reason}"}

    def _bonus(self, bonus: str, cell: Cell | None, reason: str) -> tuple[str, dict]:
        payload = {"bonus": bonus, "reason": reason}
        if cell is not None:
            payload["cell"] = cell_json(cell)
            self.pending = {"bonus": bonus, "cell": cell}
        else:
            self.pending = {"bonus": bonus, "cell": None}
        return "USE_BONUS", payload

    def _hunger_choice(self, shots_left: int) -> tuple[str, dict]:
        # Every bonus but SALVO spends a shot from the small limit, and SALVO adds
        # no shots. SHIELD only while the opponent is finishing a wounded ship of ours.
        min_accuracy = getattr(self.strategy, "shield_min_accuracy", 0.0)
        accuracy = len(self.opponent_hit_cells) / self.opponent_shots if self.opponent_shots else 0.0
        targeting_allowed = getattr(self.strategy, "shield_while_targeting", True) or not self.strategy.hits
        if (self.uses["SHIELD"] > 0 and not self.shield_active and shots_left > 1 and self._open_threat()
                and targeting_allowed
                and (not min_accuracy or self.opponent_shots >= 4 and accuracy > min_accuracy)):
            return self._bonus("SHIELD", None, f"hunger shield: opponent finishing our wounded ship; "
                                               f"accuracy={accuracy:.2f}")
        return self._shoot("hunger: bonuses cost shots")

    def _open_threat(self) -> bool:
        return any(ship & self.opponent_hit_cells and not ship <= self.opponent_hit_cells
                   for ship in self.own_fleet)

    def _under_threat(self) -> bool:
        return any(cell in self.own_ships for cell in self.opponent_shot_cells)

    def _mine_cell(self) -> Cell | None:
        forbidden = self.own_ships | self.opponent_shot_cells | self.mines
        legal = sorted(self.board() - forbidden)
        if not legal:
            return None
        # Prefer empty cells touching the halo of our ships: likely hunt targets.
        halo = set().union(*(neighbors(cell, True) for cell in self.own_ships)) - self.own_ships
        preferred = [cell for cell in legal if cell in halo]
        pool = preferred or legal
        return self.strategy.rng.choice(pool)

    def _sonar_cell(self) -> Cell | None:
        board = self.board()
        legal = board - self.strategy.used - self.strategy.empty
        if not legal:
            return None
        best, score = [], -1
        for cell in sorted(legal):
            window = {(cell[0] + dr, cell[1] + dc)
                      for dr in (-1, 0, 1) for dc in (-1, 0, 1)} & board
            unknown = len(window - self.strategy.used - self.strategy.empty)
            parity = 1 if (cell[0] + cell[1]) % 2 == 0 else 0
            value = unknown * 2 + parity
            if value > score:
                best, score = [cell], value
            elif value == score:
                best.append(cell)
        return self.strategy.rng.choice(best) if best and score > 0 else None

    def _distance_cell(self) -> Cell | None:
        board = self.board()
        legal = sorted(board - self.strategy.used - self.strategy.empty)
        if not legal:
            return None
        mid_r = (self.strategy.rules["rows"] - 1) / 2
        mid_c = (self.strategy.rules["columns"] - 1) / 2
        even = [cell for cell in legal if (cell[0] + cell[1]) % 2 == 0] or legal
        return min(even, key=lambda cell: (abs(cell[0] - mid_r) + abs(cell[1] - mid_c), cell))

    def _apply_sonar(self, center: Cell, count: int) -> None:
        board = self.board()
        window = {(center[0] + dr, center[1] + dc)
                  for dr in (-1, 0, 1) for dc in (-1, 0, 1)} & board
        known_ship = window & self.strategy.hits
        unknown = window - self.strategy.used - self.strategy.empty
        remaining = count - len(known_ship)
        if count == 0 or remaining <= 0:
            self.strategy.empty.update(unknown)
            return
        if remaining >= len(unknown) and unknown:
            self.forced = min(unknown)

    def _apply_distance(self, center: Cell, distance: int) -> None:
        if distance == 0:
            self.forced = center
            return
        for cell in list(self.board() - self.strategy.used - self.strategy.empty):
            if abs(cell[0] - center[0]) + abs(cell[1] - center[1]) < distance:
                self.strategy.empty.add(cell)
