# Справочник сообщений

> Снимок страницы <https://hackathon.no-offence.ru/documentation> (`Battleships.mhtml`, 2026-10-10). Платформа может менять правила: источник истины — страница и `MATCH_STARTED` партии.

Каждое сообщение — JSON-объект с типом в поле `type`.

## От бота

### CHANGE_MODE

Бот → платформа

Смена режима бота; действует со следующего матча

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `mode` | string | Имя режима; неизвестное имя отклоняется ответом, а не разрывом соединения |
| `opponent` | string, может быть null | Уровень встроенного бота — соперника в тренировке, имя значения BuiltInBot; читается только при mode = TRAINING, без поля — EASY |

#### Пример

```json
{
  "type": "CHANGE_MODE",
  "mode": "TRAINING",
  "opponent": "MEDIUM"
}
```

### PLACE

Бот → платформа

Расстановка кораблей в идущей партии бота

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `ships` | object[] | Корабли расстановки |
| ↳ `cells` | object[] | Клетки корабля |
| ↳ ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ ↳ `column` | integer | Столбец, отсчёт от 0 |

#### Пример

```json
{
  "type": "PLACE",
  "ships": [
    {
      "cells": [
        {"row": 0, "column": 0},
        {"row": 0, "column": 1},
        {"row": 0, "column": 2},
        {"row": 0, "column": 3}
      ]
    },
    {
      "cells": [
        {"row": 0, "column": 5},
        {"row": 0, "column": 6},
        {"row": 0, "column": 7}
      ]
    },
    {
      "cells": [
        {"row": 2, "column": 0},
        {"row": 2, "column": 1},
        {"row": 2, "column": 2}
      ]
    },
    {"cells": [{"row": 2, "column": 4}, {"row": 2, "column": 5}]},
    {"cells": [{"row": 4, "column": 0}, {"row": 4, "column": 1}]},
    {"cells": [{"row": 6, "column": 0}, {"row": 7, "column": 0}]},
    {"cells": [{"row": 4, "column": 3}]},
    {"cells": [{"row": 4, "column": 5}]},
    {"cells": [{"row": 4, "column": 7}]},
    {"cells": [{"row": 4, "column": 9}]}
  ]
}
```

### SHOOT

Бот → платформа

Выстрел в идущей партии бота

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `cell` | object | Клетка выстрела; любая пара целых чисел, в том числе вне поля |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |

#### Пример

```json
{
  "type": "SHOOT",
  "cell": {"row": 2, "column": 1}
}
```

### USE_BONUS

Бот → платформа

Применение бонуса в свой ход; ответ — BONUS_RESOLVED или BONUS_REJECTED

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `bonus` | Bonus | Бонус |
| `cell` | object, может быть null | Клетка: для DISTANCE и SONAR — клетка поля соперника, для MINE — клетка своего поля; для SALVO и SHIELD не читается, можно не присылать |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |

#### Пример

```json
{
  "type": "USE_BONUS",
  "bonus": "SONAR",
  "cell": {"row": 4, "column": 4}
}
```

### PLACE_BET

Бот → платформа

Ставка «Казино „Рояль“» в ответ на BET_REQUESTED: модификатор, с которым бот хочет играть, и размер ставки

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `modifier` | RuleModifier | Модификатор, на который бот ставит |
| `amount` | number | Размер ставки в очках рейтинга |

#### Пример

```json
{
  "type": "PLACE_BET",
  "modifier": "DUEL",
  "amount": 15
}
```

## От платформы

### MATCH_STARTED

Платформа → бот

Партия началась: набор правил, бонусы обеих сторон, соперник и запрос расстановки

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `rules` | object | Набор правил партии |
| ↳ `columns` | integer | Число столбцов поля |
| ↳ `rows` | integer | Число строк поля |
| ↳ `fleet` | object[] | Состав флота: классы кораблей по длине |
| ↳ ↳ `length` | integer | Длина корабля в клетках |
| ↳ ↳ `count` | integer | Число кораблей этой длины |
| ↳ `touchRule` | TouchRule | Правило касания кораблей |
| ↳ `turnAfterHitRule` | TurnAfterHitRule | Правило очерёдности хода после попадания |
| ↳ `matchDurationSeconds` | integer | Длительность партии в секундах |
| ↳ `shotLimitPerPlayer` | integer | Лимит выстрелов каждого игрока |
| ↳ `moveTimeoutSeconds` | integer | Время на ход в целых секундах от запроса хода; не уложился — ход засчитывается промахом |
| ↳ `placementWindowSeconds` | integer | Окно расстановки в секундах от старта партии; без принятой расстановки к его концу — техническое поражение |
| ↳ `modifier` | RuleModifier, может быть null | Модификатор партии; null — обычные правила без модификатора |
| ↳ `shotsPerTurn` | integer | Выстрелов в ходе: сколько выстрелов игрок делает, пока ход не перейдёт к сопернику; тратит ли выстрел хода попадание — turnAfterHitRule |
| `bonuses` | object[] | Бонусы бота в партии с числом использований; бонуса, которого нет в списке, у бота нет; пустой список — бонусов нет |
| ↳ `bonus` | Bonus | Бонус |
| ↳ `uses` | integer | Число использований |
| `opponent` | object | Соперник в партии |
| ↳ `kind` | PlayerKind | Тип соперника |
| ↳ `botId` | string (uuid), может быть null | Идентификатор бота команды соперника; null у встроенного бота |
| ↳ `teamName` | string, может быть null | Имя команды соперника; null у встроенного бота |
| ↳ `builtInBot` | BuiltInBot, может быть null | Уровень встроенного бота; null у бота команды |
| ↳ `bonuses` | object[] | Бонусы соперника в партии с числом использований на старте; пустой список — бонусов нет |
| ↳ ↳ `bonus` | Bonus | Бонус |
| ↳ ↳ `uses` | integer | Число использований |

#### Пример

```json
{
  "type": "MATCH_STARTED",
  "rules": {
    "columns": 10,
    "rows": 10,
    "fleet": [
      {"length": 4, "count": 1},
      {"length": 3, "count": 2},
      {"length": 2, "count": 3},
      {"length": 1, "count": 4}
    ],
    "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY",
    "turnAfterHitRule": "SHOOTER_KEEPS_TURN",
    "matchDurationSeconds": 210,
    "shotLimitPerPlayer": 100,
    "moveTimeoutSeconds": 1,
    "placementWindowSeconds": 10,
    "modifier": null,
    "shotsPerTurn": 1
  },
  "bonuses": [{"bonus": "SONAR", "uses": 2}, {"bonus": "SHIELD", "uses": 1}],
  "opponent": {
    "kind": "REMOTE_BOT",
    "botId": "3f2b8c1e-6a4d-4f0b-9c5e-2d7a1b8e4c90",
    "teamName": "Kraken",
    "builtInBot": null,
    "bonuses": [{"bonus": "SALVO", "uses": 1}]
  }
}
```

### PLACEMENT_ACCEPTED

Платформа → бот

Расстановка принята и окончательна

#### Поля

Полей нет

#### Пример

```json
{
  "type": "PLACEMENT_ACCEPTED"
}
```

### PLACEMENT_REJECTED

Платформа → бот

Расстановка отклонена; можно прислать другую

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `reason` | RejectionReason | Причина отказа |

#### Пример

```json
{
  "type": "PLACEMENT_REJECTED",
  "reason": "SHIPS_TOUCH"
}
```

### TURN_REQUESTED

Платформа → бот

Ход бота: ждём одно действие — SHOOT или USE_BONUS. Следующее действие бот присылает только после ответа на это: каждое следующее действие того же хода запрашивает новый TURN_REQUESTED — после попадания, после BONUS_RESOLVED. Время на ход (moveTimeoutSeconds) отсчитывается от каждого запроса заново

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `moveNumber` | integer | Номер хода в партии, общий для обоих игроков |
| `shotsLeft` | integer | Сколько выстрелов у бота осталось |
| `shotsLeftInTurn` | integer | Сколько выстрелов осталось в текущем ходу; бонус его не меняет — после BONUS_RESOLVED приходит прежнее значение, после SALVO — shotsInTurn залпа |

#### Пример

```json
{
  "type": "TURN_REQUESTED",
  "moveNumber": 7,
  "shotsLeft": 97,
  "shotsLeftInTurn": 1
}
```

### SHOT_RESOLVED

Платформа → бот

Результат выстрела бота

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `cell` | object | Клетка выстрела в том виде, в каком бот её прислал |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |
| `result` | ShotResult | Результат выстрела |

#### Пример

```json
{
  "type": "SHOT_RESOLVED",
  "cell": {"row": 2, "column": 1},
  "result": "HIT"
}
```

### SHOT_REJECTED

Платформа → бот

Выстрел бота отклонён

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `cell` | object | Клетка выстрела в том виде, в каком бот её прислал |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |
| `reason` | RejectionReason | Причина отказа |

#### Пример

```json
{
  "type": "SHOT_REJECTED",
  "cell": {"row": 2, "column": 1},
  "reason": "NOT_YOUR_TURN"
}
```

### OPPONENT_SHOT

Платформа → бот

Выстрел соперника по полю бота

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `cell` | object | Клетка выстрела в том виде, в каком соперник её прислал |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |
| `result` | ShotResult | Результат выстрела |

#### Пример

```json
{
  "type": "OPPONENT_SHOT",
  "cell": {"row": 5, "column": 6},
  "result": "MISS"
}
```

### MATCH_FINISHED

Платформа → бот

Партия завершена

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `outcome` | PlayerOutcome | Итог партии для бота |
| `endReason` | MatchEndReason | Причина завершения |

#### Пример

```json
{
  "type": "MATCH_FINISHED",
  "outcome": "WIN",
  "endReason": "FLEET_DESTROYED"
}
```

### TURN_TIMED_OUT

Платформа → бот

Ход бота не сделан за время на ход и засчитан промахом

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `moveNumber` | integer | Номер пропущенного хода |

#### Пример

```json
{
  "type": "TURN_TIMED_OUT",
  "moveNumber": 12
}
```

### OPPONENT_TURN_TIMED_OUT

Платформа → бот

Ход соперника не сделан за время на ход и засчитан промахом

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `moveNumber` | integer | Номер пропущенного хода |

#### Пример

```json
{
  "type": "OPPONENT_TURN_TIMED_OUT",
  "moveNumber": 13
}
```

### MATCH_SNAPSHOT

Платформа → бот

Состояние идущей партии; первое сообщение нового соединения бота, у которого идёт партия

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `rules` | object | Набор правил партии |
| ↳ `columns` | integer | Число столбцов поля |
| ↳ `rows` | integer | Число строк поля |
| ↳ `fleet` | object[] | Состав флота: классы кораблей по длине |
| ↳ ↳ `length` | integer | Длина корабля в клетках |
| ↳ ↳ `count` | integer | Число кораблей этой длины |
| ↳ `touchRule` | TouchRule | Правило касания кораблей |
| ↳ `turnAfterHitRule` | TurnAfterHitRule | Правило очерёдности хода после попадания |
| ↳ `matchDurationSeconds` | integer | Длительность партии в секундах |
| ↳ `shotLimitPerPlayer` | integer | Лимит выстрелов каждого игрока |
| ↳ `moveTimeoutSeconds` | integer | Время на ход в целых секундах от запроса хода; не уложился — ход засчитывается промахом |
| ↳ `placementWindowSeconds` | integer | Окно расстановки в секундах от старта партии; без принятой расстановки к его концу — техническое поражение |
| ↳ `modifier` | RuleModifier, может быть null | Модификатор партии; null — обычные правила без модификатора |
| ↳ `shotsPerTurn` | integer | Выстрелов в ходе: сколько выстрелов игрок делает, пока ход не перейдёт к сопернику; тратит ли выстрел хода попадание — turnAfterHitRule |
| `ships` | object[], может быть null | Принятая расстановка бота; null, пока расстановка не принята; в «Спидране» всегда null: флот расставляет платформа |
| ↳ `cells` | object[] | Клетки корабля |
| ↳ ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ ↳ `column` | integer | Столбец, отсчёт от 0 |
| `shots` | object[] | Принятые выстрелы бота по порядку, клетки в том виде, в каком бот их прислал |
| ↳ `cell` | object | Клетка выстрела в том виде, в каком её прислал стрелявший |
| ↳ ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ ↳ `column` | integer | Столбец, отсчёт от 0 |
| ↳ `result` | ShotResult | Результат выстрела |
| `opponentShots` | object[] | Принятые выстрелы соперника по полю бота по порядку |
| ↳ `cell` | object | Клетка выстрела в том виде, в каком её прислал стрелявший |
| ↳ ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ ↳ `column` | integer | Столбец, отсчёт от 0 |
| ↳ `result` | ShotResult | Результат выстрела |
| `shotsLeft` | integer | Сколько выстрелов у бота осталось |
| `placementWindowLeftSeconds` | integer, может быть null | Остаток окна расстановки в целых секундах, округлённый вниз, пока партия в расстановке: 0 — окно ещё открыто, но осталось меньше секунды; null — партия уже не в расстановке |
| `bonuses` | object[] | Остаток использований каждого бонуса, который был у бота на старте партии; пустой список — бонусов не было |
| ↳ `bonus` | Bonus | Бонус |
| ↳ `uses` | integer | Число использований |
| `shieldActive` | boolean | Активен ли щит бота |
| `mines` | object[] | Клетки мин бота, которые ещё не сработали; сработавшая мина видна в opponentShots с результатом MINE |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |

#### Пример

```json
{
  "type": "MATCH_SNAPSHOT",
  "rules": {
    "columns": 10,
    "rows": 10,
    "fleet": [
      {"length": 4, "count": 1},
      {"length": 3, "count": 2},
      {"length": 2, "count": 3},
      {"length": 1, "count": 4}
    ],
    "touchRule": "NO_TOUCHING_EVEN_DIAGONALLY",
    "turnAfterHitRule": "SHOOTER_KEEPS_TURN",
    "matchDurationSeconds": 210,
    "shotLimitPerPlayer": 100,
    "moveTimeoutSeconds": 1,
    "placementWindowSeconds": 10,
    "modifier": null,
    "shotsPerTurn": 1
  },
  "ships": [
    {
      "cells": [
        {"row": 0, "column": 0},
        {"row": 0, "column": 1},
        {"row": 0, "column": 2},
        {"row": 0, "column": 3}
      ]
    },
    {
      "cells": [
        {"row": 0, "column": 5},
        {"row": 0, "column": 6},
        {"row": 0, "column": 7}
      ]
    },
    {
      "cells": [
        {"row": 2, "column": 0},
        {"row": 2, "column": 1},
        {"row": 2, "column": 2}
      ]
    },
    {"cells": [{"row": 2, "column": 4}, {"row": 2, "column": 5}]},
    {"cells": [{"row": 4, "column": 0}, {"row": 4, "column": 1}]},
    {"cells": [{"row": 6, "column": 0}, {"row": 7, "column": 0}]},
    {"cells": [{"row": 4, "column": 3}]},
    {"cells": [{"row": 4, "column": 5}]},
    {"cells": [{"row": 4, "column": 7}]},
    {"cells": [{"row": 4, "column": 9}]}
  ],
  "shots": [{"cell": {"row": 4, "column": 4}, "result": "MISS"}],
  "opponentShots": [
    {"cell": {"row": 0, "column": 0}, "result": "HIT"},
    {"cell": {"row": 1, "column": 1}, "result": "MISS"}
  ],
  "shotsLeft": 99,
  "placementWindowLeftSeconds": null,
  "bonuses": [{"bonus": "SONAR", "uses": 1}, {"bonus": "SHIELD", "uses": 0}],
  "shieldActive": true,
  "mines": [{"row": 9, "column": 9}]
}
```

### MODE_CHANGED

Платформа → бот

Режим принят и действует со следующего матча

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `mode` | BotMode | Режим бота |

#### Пример

```json
{
  "type": "MODE_CHANGED",
  "mode": "RANKING"
}
```

### MODE_REJECTED

Платформа → бот

Смена режима отклонена; режим бота прежний

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `mode` | string | Имя режима в том виде, в каком бот его прислал |
| `reason` | BotCommandRejectionReason | Причина отказа |

#### Пример

```json
{
  "type": "MODE_REJECTED",
  "mode": "TOURNAMENT",
  "reason": "UNKNOWN_MODE"
}
```

### COMMAND_REJECTED

Платформа → бот

Команда бота отклонена и не выполнена; соединение остаётся открытым

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `command` | BotCommandType, может быть null | Вид отклонённой команды; null, когда сообщение не читается или его тип неизвестен |
| `reason` | BotCommandRejectionReason | Причина отказа |

#### Пример

```json
{
  "type": "COMMAND_REJECTED",
  "command": "SHOOT",
  "reason": "NO_RUNNING_MATCH"
}
```

### BONUS_RESOLVED

Платформа → бот

Бонус применён; ход он не передаёт. Следом приходит новый TURN_REQUESTED того же хода с прежним shotsLeftInTurn (после SALVO — shotsInTurn), и время на ход отсчитывается от него заново. Если бонус потратил последний выстрел лимита, ход переходит к сопернику: TURN_REQUESTED получает соперник. Заполнено только поле применённого бонуса, остальные null

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `bonus` | Bonus | Применённый бонус |
| `distance` | integer, может быть null | DISTANCE: расстояние от клетки до ближайшей клетки непотопленного корабля соперника, \|Δrow\| + \|Δcolumn\|; 0 — в клетке корабль |
| `count` | integer, может быть null | SONAR: число клеток кораблей соперника, подбитые тоже, в квадрате 3 × 3 с центром в клетке, без части за краем поля |
| `shotsInTurn` | integer, может быть null | SALVO: сколько выстрелов в ходе после залпа |
| `shieldActive` | boolean, может быть null | SHIELD: щит активен — true |
| `mineCell` | object, может быть null | MINE: клетка поставленной мины |
| ↳ `row` | integer | Строка, отсчёт от 0 |
| ↳ `column` | integer | Столбец, отсчёт от 0 |

#### Пример

```json
{
  "type": "BONUS_RESOLVED",
  "bonus": "SONAR",
  "distance": null,
  "count": 2,
  "shotsInTurn": null,
  "shieldActive": null,
  "mineCell": null
}
```

### OPPONENT_BONUS_USED

Платформа → бот

Соперник применил бонус; клетка и результат не раскрываются. В «Птичьем коробе» не приходит

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `bonus` | Bonus | Применённый соперником бонус |

#### Пример

```json
{
  "type": "OPPONENT_BONUS_USED",
  "bonus": "SHIELD"
}
```

### BONUS_REJECTED

Платформа → бот

Бонус не применён, партия не изменилась: использования, выстрелы, ход и его время прежние, соперник ничего не получает. Нового TURN_REQUESTED нет: пока не истекло время хода, бот может прислать другое действие

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `bonus` | Bonus | Бонус в том виде, в каком бот его прислал |
| `reason` | BonusRejectionReason | Причина отказа |

#### Пример

```json
{
  "type": "BONUS_REJECTED",
  "bonus": "SONAR",
  "reason": "BONUS_NOT_AVAILABLE"
}
```

### BET_REQUESTED

Платформа → бот

Приглашение к ставке «Казино „Рояль“» перед партией: бот ставит рейтинг на модификатор, с которым хочет играть. Ставки закрыты, выигрывает бо́льшая; итог — в BETTING_FINISHED, затем MATCH_STARTED. Бот, который ещё не поставил и переподключился до конца торгов, получает приглашение снова — с оставшимся временем

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `modifiers` | RuleModifier[] | Модификаторы, на которые можно ставить |
| `maxAmount` | number | Наибольшая ставка бота в очках рейтинга |
| `betWindowSeconds` | integer | Время на ставку в целых секундах от приглашения |

#### Пример

```json
{
  "type": "BET_REQUESTED",
  "modifiers": ["DUEL", "THREE_MUSKETEERS", "SPEEDRUN"],
  "maxAmount": 40.5,
  "betWindowSeconds": 5
}
```

### BET_ACCEPTED

Платформа → бот

Ставка принята. Бот, переподключившийся во время торгов после принятой ставки, получает его снова: ставка в силе

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `modifier` | RuleModifier | Модификатор ставки |
| `amount` | number | Размер ставки в очках рейтинга |

#### Пример

```json
{
  "type": "BET_ACCEPTED",
  "modifier": "DUEL",
  "amount": 15
}
```

### BET_REJECTED

Платформа → бот

Ставка отклонена и не учтена; соединение остаётся открытым

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `modifier` | RuleModifier | Модификатор в том виде, в каком бот его прислал |
| `amount` | number | Размер ставки в том виде, в каком бот его прислал |
| `reason` | BetRejectionReason | Причина отказа |

#### Пример

```json
{
  "type": "BET_REJECTED",
  "modifier": "DUEL",
  "amount": 15,
  "reason": "BETTING_CLOSED"
}
```

### BETTING_FINISHED

Платформа → бот

Торги «Казино „Рояль“» завершены; следом приходит MATCH_STARTED, в наборе правил которого выигравший модификатор. Ставка соперника не раскрывается

#### Поля

| Поле | Тип | Описание |
|---|---|---|
| `modifier` | RuleModifier, может быть null | Выигравший модификатор; null — торги не выбрали ни одного |
| `betWon` | boolean | Выиграла ли ставка бота; false и тогда, когда бот не ставил |

#### Пример

```json
{
  "type": "BETTING_FINISHED",
  "modifier": "DUEL",
  "betWon": true
}
```
