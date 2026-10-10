"""Стратегия bot_1: стрельба по карте плотности, расстановка зависит от соперника.

Набор флотов против HARD (bot_1/hard_fleets.py) бьёт только детерминированного встроенного бота.
Соперники-команды его выучили: в рейтинге доля побед упала с 14/16 до 7/16, а первое попадание
у них приходилось на 1–3-й выстрел. Против команд флот ставится в случайной половине поля.
"""

from random import Random

from seabattle.model import Fleet, Rules
from strategies.compact import compact_fleet
from strategies.density import DensityStrategy

from bot_1.hard_fleets import hard_fleet


class Bot1Strategy(DensityStrategy):
    def __init__(self, tuned_for_hard: bool) -> None:
        self._tuned_for_hard = tuned_for_hard

    def place(self, rules: Rules, rng: Random) -> Fleet:
        if self._tuned_for_hard:
            return hard_fleet(rules, rng)
        return compact_fleet(rules, rng)
