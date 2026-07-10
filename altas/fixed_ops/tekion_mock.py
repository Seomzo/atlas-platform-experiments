"""Fixture-backed connector implementing the future Tekion data boundary."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


class TekionFixtureConnector:
    """Read sanitized, synthetic service data.

    This class intentionally does not accept credentials. Its interface is the
    seam a trusted Tekion connector will implement after partner validation.
    """

    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path or (
            Path(__file__).parent / "fixtures" / "tekion_service_snapshot.json"
        )
        self.calls: list[tuple[str, str | None]] = []

    def fetch_daily_service_snapshot(
        self,
        *,
        store_id: str,
        business_date: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((store_id, business_date))
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        if payload.get("fixture_version") != "1.0":
            raise ValueError("Unsupported fixed-ops fixture version")
        try:
            store = payload["stores"][store_id]
        except KeyError as exc:
            raise LookupError(
                f"No synthetic fixture exists for store {store_id}"
            ) from exc
        if business_date and store["business_date"] != business_date:
            raise LookupError(
                f"No synthetic snapshot for {store_id} on {business_date}"
            )
        result = deepcopy(store)
        result["source"] = "synthetic_tekion_fixture"
        result["fixture_notice"] = payload["notice"]
        return result
