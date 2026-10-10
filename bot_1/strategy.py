"""Стратегия bot_1: стрельба по карте плотности, расстановка из набора против HARD."""

from random import Random

from seabattle.model import Fleet, Rules
from strategies.density import DensityStrategy

from bot_1.hard_fleets import hard_fleet


class Bot1Strategy(DensityStrategy):
    def place(self, rules: Rules, rng: Random) -> Fleet:
        return hard_fleet(rules, rng)
