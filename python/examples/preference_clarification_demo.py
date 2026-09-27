"""Repeatable Stage A batch 1 demonstration; no API or external service needed."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from preferences import ClarificationManager, ClarificationResult


REFERENCE_DATE = date(2026, 9, 24)


def summarize(example: int, first: ClarificationResult, final: ClarificationResult) -> str:
    return json.dumps(
        {
            "example": example,
            "round1_missing": [field.value for field in first.missing_required_fields],
            "round2_complete": final.is_complete,
            "final": final.draft.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
    )


async def main() -> None:
    manager = ClarificationManager()

    first = await manager.start(
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
        reference_date=REFERENCE_DATE,
    )
    completed = await manager.clarify(
        first,
        "我们一共3人，2026年10月1日出发，舒适游。",
        reference_date=REFERENCE_DATE,
    )
    print(summarize(1, first, completed))

    ambiguous = await manager.start(
        "从北京出发，预算一万到两万，大概两三人，国庆出发，喜欢博物馆。",
        reference_date=REFERENCE_DATE,
    )
    resolved = await manager.clarify(
        ambiguous,
        "预算改成18000元，共2人，2026年10月2日出发，玩四天，文化游。",
        reference_date=REFERENCE_DATE,
    )
    print(summarize(2, ambiguous, resolved))


if __name__ == "__main__":
    asyncio.run(main())
