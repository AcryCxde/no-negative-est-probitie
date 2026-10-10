"""Расстановки, подобранные против встроенного HARD.

HARD стреляет детерминированно (карта плотности, из равных клеток — первая по порядку), поэтому
`tools/search_fleets.py` перебирает флоты точно: HARD тратит на них 89–92 выстрела против 57 на
случайном флоте и 66 на флоте в половине поля. Против других стрелков выигрыш не проверен.
Если менять подсчёт плотности в strategies/density.py, модель HARD и этот набор устареют.
"""

from random import Random

from seabattle.model import Cell, Fleet, Rules, Ship
from seabattle.placement import PlacementError, random_fleet, validate_fleet

# «строка,столбец,длина» и h/v; флоты уже разведены по правилу касания
ENCODED = (
    "6,9,4v 9,2,3h 2,9,3v 2,0,2v 0,8,2h 9,6,2h 7,4,1h 9,0,1h 7,2,1h 7,6,1h",
    "0,0,4v 4,5,3h 2,9,3v 9,0,2h 0,2,2v 0,8,2h 9,4,1h 9,6,1h 2,7,1h 9,8,1h",
    "6,9,4v 9,2,3h 0,0,3h 8,0,2v 9,6,2h 3,9,2v 7,4,1h 6,7,1h 1,9,1h 7,2,1h",
    "0,0,4v 2,9,3v 7,0,3v 4,2,2v 8,6,2v 9,8,2h 5,0,1h 7,2,1h 9,4,1h 7,8,1h",
    "9,6,4h 5,0,3v 2,9,3v 0,8,2h 9,0,2h 9,3,2h 7,6,1h 3,2,1h 7,4,1h 7,8,1h",
    "0,0,4v 0,7,3v 0,9,3v 0,2,2v 9,0,2h 9,3,2h 7,2,1h 9,9,1h 9,6,1h 7,6,1h",
    "9,6,4h 9,2,3h 7,4,3h 7,1,2h 7,8,2h 0,9,2v 5,6,1h 5,2,1h 9,0,1h 5,8,1h",
    "9,6,4h 9,2,3h 7,4,3h 3,9,2v 6,8,2v 2,0,2v 9,0,1h 7,2,1h 1,9,1h 4,7,1h",
    "9,6,4h 0,5,3h 5,9,3v 5,7,2v 9,0,2h 0,9,2v 9,4,1h 7,0,1h 3,9,1h 7,4,1h",
    "0,0,4h 7,0,3v 5,9,3v 5,7,2v 8,6,2v 9,3,2h 1,9,1h 7,2,1h 9,9,1h 5,0,1h",
    "0,9,4v 0,7,3v 1,0,3v 0,2,2v 9,6,2h 2,4,2h 9,4,1h 0,5,1h 7,6,1h 9,9,1h",
    "6,9,4v 1,0,3v 9,5,3h 7,5,2h 3,9,2v 9,0,2h 6,3,1h 9,3,1h 1,9,1h 5,6,1h",
    "6,0,4v 9,2,3h 0,5,3h 3,9,2v 5,7,2v 8,6,2v 7,4,1h 9,9,1h 7,2,1h 6,9,1h",
    "0,0,4v 0,7,3v 9,5,3h 9,0,2h 0,2,2v 0,9,2v 7,0,1h 7,6,1h 9,9,1h 7,8,1h",
    "9,0,4h 7,0,3h 2,9,3v 0,8,2h 9,8,2h 8,6,2v 5,2,1h 2,7,1h 7,8,1h 7,4,1h",
    "0,0,4v 7,0,3v 9,2,3h 0,2,2v 6,4,2v 9,8,2h 1,9,1h 9,6,1h 7,2,1h 4,9,1h",
    "0,0,4v 0,7,3h 2,9,3v 0,2,2v 9,3,2h 9,0,2h 7,2,1h 7,6,1h 9,9,1h 9,6,1h",
    "0,0,4v 5,9,3v 1,9,3v 9,3,2h 8,6,2v 0,2,2v 6,7,1h 9,1,1h 9,9,1h 7,4,1h",
    "0,6,4h 9,7,3h 9,2,3h 7,8,2h 8,0,2v 2,8,2h 7,2,1h 7,4,1h 5,8,1h 7,6,1h",
    "6,9,4v 2,9,3v 4,7,3v 9,3,2h 0,8,2h 8,0,2v 7,4,1h 7,2,1h 2,7,1h 9,6,1h",
    "6,9,4v 9,2,3h 5,0,3v 0,0,2v 3,9,2v 4,2,2v 3,0,1h 0,8,1h 9,7,1h 7,4,1h",
    "9,6,4h 0,5,3h 0,3,3v 7,8,2h 0,9,2v 9,3,2h 7,6,1h 9,1,1h 5,6,1h 7,2,1h",
    "0,6,4h 7,0,3v 7,9,3v 9,6,2h 0,2,2v 1,0,2v 7,6,1h 9,2,1h 5,0,1h 9,4,1h",
    "9,6,4h 9,2,3h 5,9,3v 0,8,2h 6,4,2v 2,9,2v 1,0,1h 7,2,1h 4,3,1h 9,0,1h",
)


def decode(text: str) -> Fleet:
    ships = []
    for token in text.split():
        row, column, tail = token.split(",")
        length, vertical = int(tail[:-1]), tail[-1] == "v"
        step = (1, 0) if vertical else (0, 1)
        ships.append(
            Ship(
                tuple(
                    Cell(int(row) + step[0] * i, int(column) + step[1] * i) for i in range(length)
                )
            )
        )
    return tuple(ships)


FLEETS = tuple(decode(text) for text in ENCODED)


def fits(rules: Rules, fleet: Fleet) -> bool:
    try:
        validate_fleet(rules, fleet)
    except PlacementError:
        return False
    return True


def hard_fleet(rules: Rules, rng: Random) -> Fleet:
    """Набор рассчитан на классику 10×10; при других размерах поля и составе флота — случайная."""
    suitable = [fleet for fleet in FLEETS if fits(rules, fleet)]
    return rng.choice(suitable) if suitable else random_fleet(rules, rng)
