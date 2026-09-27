"""确定性 Mock 的稳定种子和候选 ID 工具。"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_rng(namespace: str, payload: Any, dataset_version: str) -> random.Random:
    """为单次查询创建局部 RNG，不读写进程级随机状态。"""
    material = _canonical_json(
        {
            "namespace": namespace,
            "dataset_version": dataset_version,
            "payload": payload,
        }
    ).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return random.Random(int.from_bytes(digest[:16], byteorder="big", signed=False))


def stable_candidate_id(
    namespace: str,
    identity: Any,
    dataset_version: str,
) -> str:
    """生成跨进程稳定且不依赖 Python hash() 的候选标识。"""
    material = _canonical_json(
        {
            "namespace": namespace,
            "dataset_version": dataset_version,
            "identity": identity,
        }
    ).encode("utf-8")
    return f"{namespace}_{hashlib.sha256(material).hexdigest()[:16]}"
