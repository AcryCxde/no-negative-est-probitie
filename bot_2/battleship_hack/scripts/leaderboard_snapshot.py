"""Read public bot statistics for opponent IDs already present in our logs.

No WebSocket connections, authentication, or opponent-journal requests.
The leaderboard itself requires a participant session; statistics expose place.
Usage: .venv/bin/python scripts/leaderboard_snapshot.py
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/bot2/leaderboard-snapshot.json"
BASE = "https://hackathon.no-offence.ru"


def known_ids() -> set[str]:
    ids = set()
    for path in (ROOT / "data/bot2/logs").glob("*.jsonl"):
        with path.open() as lines:
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event["type"] == "BATTLE_STARTED":
                    bot_id = (event["payload"].get("opponent") or {}).get("botId")
                    if bot_id:
                        ids.add(bot_id)
    env = dotenv_values(ROOT / ".env")
    if env.get("BOT_PLATFORM_ID"):
        ids.add(env["BOT_PLATFORM_ID"])
    return ids


async def main() -> None:
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
        async def fetch(bot_id: str, kind: str) -> dict:
            async with semaphore:
                url = f"/api/v1/bots/{bot_id}/statistics?kind={kind}"
                try:
                    response = await client.get(url)
                    body = response.json()
                    return {"bot_id": bot_id, "kind": kind, "url": BASE + url,
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                            "status": response.status_code, "body": body}
                except (httpx.HTTPError, ValueError) as exc:
                    return {"bot_id": bot_id, "kind": kind, "url": BASE + url,
                            "error": type(exc).__name__}
        ids = known_ids()
        entries = await asyncio.gather(*(fetch(bot_id, kind) for bot_id in sorted(ids)
                                       for kind in ("ALL", "RANKING", "TRAINING")))
    ranking = sorted([{"botId": e["bot_id"], **e["body"]["value"]} for e in entries
                      if e["kind"] == "RANKING" and e.get("status") == 200
                      and e["body"].get("value")], key=lambda row: row["place"])
    missing = sorted(set(range(1, 11)) - {row["place"] for row in ranking})
    snapshot = {"fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "Public statistics of bot IDs from local match logs",
                "leaderboard_endpoint_status": 401, "known_bot_count": len(ids),
                "missing_top10_places": missing, "ranking": ranking, "responses": entries}
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUTPUT), "known_bot_count": len(ids),
                      "missing_top10_places": missing,
                      "ranking": [{k: row[k] for k in ("place", "botId", "teamName", "botName",
                                  "rating", "matchCount", "winCount", "drawCount", "lossCount",
                                  "rules", "bonuses")} for row in ranking[:12]]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
