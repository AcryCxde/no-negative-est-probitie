"""Расстановка в одной половине поля.

Стрелок с картой плотности тратит промахи на пустую половину: на 200 флотах ему нужно в среднем
69,7 выстрела против 55,2 при равномерной расстановке, а в дуэли двух таких стрелков компактная
расстановка выигрывает около 91% (tools/bench_placement.py). Сторона выбирается случайно каждый бой,
поэтому выучить её по прошлым партиям нельзя.
"""

from random import Random

from seabattle.model import Cell, Fleet, Rules
from seabattle.placement import PlacementError, random_fleet

SIDES = ("left", "right", "top", "bottom")


def forbidden_half(rules: Rules, side: str) -> set[Cell]:
    columns, rows = rules.columns // 2, rules.rows // 2
    match side:
        case "left":
            return {c for c in rules.cells() if c.column < columns}
        case "right":
            return {c for c in rules.cells() if c.column >= rules.columns - columns}
        case "top":
            return {c for c in rules.cells() if c.row < rows}
        case "bottom":
            return {c for c in rules.cells() if c.row >= rules.rows - rows}
    raise ValueError(side)


def compact_fleet(rules: Rules, rng: Random) -> Fleet:
    """Флот в случайной половине; если в половину не влезает — по всему полю."""
    try:
        return random_fleet(rules, rng, forbidden_half(rules, rng.choice(SIDES)))
    except PlacementError:
        return random_fleet(rules, rng)
