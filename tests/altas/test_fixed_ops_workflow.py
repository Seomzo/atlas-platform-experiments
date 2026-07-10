from __future__ import annotations

from altas.fixed_ops.tekion_mock import TekionFixtureConnector
from altas.fixed_ops.workflow import DailyFixedOpsReport


def test_daily_fixed_ops_report_computes_expected_metrics() -> None:
    report = DailyFixedOpsReport().run(
        store_id="store-sunrise-vw",
        business_date="2026-07-08",
    )

    assert report["source"] == "synthetic_tekion_fixture"
    assert report["kpis"] == {
        "total_ros": 8,
        "closed_ros": 6,
        "open_ros": 2,
        "labor_sales": 4505.0,
        "parts_sales": 3579.0,
        "total_sales": 8084.0,
        "gross_profit": 4654.0,
        "hours_sold": 30.4,
        "effective_labor_rate": 148.19,
        "average_closed_ro": 1347.33,
        "declined_value": 2030.0,
        "exception_count": 3,
    }
    assert {item["ro_id"] for item in report["exceptions"]} == {
        "RO-4104",
        "RO-4105",
        "RO-4107",
    }


def test_model_summary_receives_aggregates_not_raw_repair_order_rows() -> None:
    captured: list[str] = []
    connector = TekionFixtureConnector()
    report = DailyFixedOpsReport(
        connector=connector,
        summarize=lambda prompt: captured.append(prompt) or "Safe summary",
    ).run(store_id="store-sunrise-vw")

    assert report["manager_summary"] == "Safe summary"
    assert len(connector.calls) == 1
    assert len(captured) == 1
    prompt = captured[0]
    assert "RO-4101" not in prompt
    assert "opened_at" not in prompt
    assert "Synthetic" not in prompt
    assert "$8,084" not in prompt
    assert "8084" in prompt


def test_unknown_fixture_store_fails_without_fallback() -> None:
    connector = TekionFixtureConnector()

    try:
        connector.fetch_daily_service_snapshot(store_id="store-not-entitled")
    except LookupError as exc:
        assert "store-not-entitled" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("unknown stores must not fall back to another fixture")
