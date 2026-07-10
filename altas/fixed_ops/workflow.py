"""Daily fixed-operations report over the trusted connector contract."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from altas.fixed_ops.tekion_mock import TekionFixtureConnector

SummaryCallback = Callable[[str], str]


def _money(value: float) -> float:
    return round(float(value), 2)


def _days_open(opened_at: str, business_date: str) -> int:
    opened = datetime.fromisoformat(opened_at).date()
    current = datetime.fromisoformat(business_date).date()
    return max(0, (current - opened).days)


class DailyFixedOpsReport:
    """Compute aggregate KPIs and safe manager-facing exceptions."""

    capability = "fixed_ops.daily_report"
    report_version = "1.0"

    def __init__(
        self,
        *,
        connector: TekionFixtureConnector | None = None,
        summarize: SummaryCallback | None = None,
    ) -> None:
        self.connector = connector or TekionFixtureConnector()
        self.summarize = summarize

    def run(
        self,
        *,
        store_id: str,
        business_date: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.connector.fetch_daily_service_snapshot(
            store_id=store_id,
            business_date=business_date,
        )
        date = str(snapshot["business_date"])
        repair_orders = list(snapshot["repair_orders"])
        closed = [ro for ro in repair_orders if ro["status"] == "closed"]
        opened = [ro for ro in repair_orders if ro["status"] == "open"]

        labor_sales = sum(float(ro["labor_sales"]) for ro in closed)
        parts_sales = sum(float(ro["parts_sales"]) for ro in closed)
        gross_profit = sum(float(ro["gross_profit"]) for ro in closed)
        hours_sold = sum(float(ro["hours_sold"]) for ro in closed)
        declined = sum(float(ro["declined_value"]) for ro in repair_orders)
        total_sales = labor_sales + parts_sales

        advisor_rows: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "advisor_id": "",
                "advisor_name": "",
                "closed_ros": 0,
                "open_ros": 0,
                "sales": 0.0,
                "gross_profit": 0.0,
                "hours_sold": 0.0,
                "declined_value": 0.0,
            }
        )
        for ro in repair_orders:
            row = advisor_rows[str(ro["advisor_id"])]
            row["advisor_id"] = str(ro["advisor_id"])
            row["advisor_name"] = str(ro["advisor_name"])
            row["closed_ros" if ro["status"] == "closed" else "open_ros"] += 1
            if ro["status"] == "closed":
                row["sales"] += float(ro["labor_sales"]) + float(ro["parts_sales"])
                row["gross_profit"] += float(ro["gross_profit"])
                row["hours_sold"] += float(ro["hours_sold"])
            row["declined_value"] += float(ro["declined_value"])

        advisors = []
        for row in advisor_rows.values():
            row["sales"] = _money(row["sales"])
            row["gross_profit"] = _money(row["gross_profit"])
            row["hours_sold"] = round(row["hours_sold"], 1)
            row["declined_value"] = _money(row["declined_value"])
            advisors.append(dict(row))
        advisors.sort(key=lambda row: (-row["sales"], row["advisor_name"]))

        exceptions = []
        for ro in repair_orders:
            reasons: list[str] = []
            age = _days_open(str(ro["opened_at"]), date)
            if ro["status"] == "open" and age >= 2:
                reasons.append(f"open_{age}_days")
            if float(ro["declined_value"]) >= 500:
                reasons.append("high_declined_value")
            if bool(ro["comeback"]):
                reasons.append("comeback")
            if reasons:
                exceptions.append({
                    "ro_id": str(ro["ro_id"]),
                    "advisor_name": str(ro["advisor_name"]),
                    "status": str(ro["status"]),
                    "reasons": reasons,
                    "declined_value": _money(ro["declined_value"]),
                })

        kpis = {
            "total_ros": len(repair_orders),
            "closed_ros": len(closed),
            "open_ros": len(opened),
            "labor_sales": _money(labor_sales),
            "parts_sales": _money(parts_sales),
            "total_sales": _money(total_sales),
            "gross_profit": _money(gross_profit),
            "hours_sold": round(hours_sold, 1),
            "effective_labor_rate": _money(labor_sales / hours_sold)
            if hours_sold
            else 0.0,
            "average_closed_ro": _money(total_sales / len(closed)) if closed else 0.0,
            "declined_value": _money(declined),
            "exception_count": len(exceptions),
        }
        prompt = self._aggregate_summary_prompt(
            store_name=str(snapshot["store_name"]),
            business_date=date,
            kpis=kpis,
            advisors=advisors,
        )
        narrative = (
            self.summarize(prompt) if self.summarize else self._fallback_summary(kpis)
        )

        return {
            "report_version": self.report_version,
            "workflow": self.capability,
            "store_id": store_id,
            "store_name": snapshot["store_name"],
            "business_date": date,
            "source": snapshot["source"],
            "fixture_notice": snapshot["fixture_notice"],
            "kpis": kpis,
            "advisors": advisors,
            "exceptions": exceptions,
            "manager_summary": narrative,
        }

    @staticmethod
    def _aggregate_summary_prompt(
        *,
        store_name: str,
        business_date: str,
        kpis: dict[str, Any],
        advisors: list[dict[str, Any]],
    ) -> str:
        """Build a prompt containing aggregates only—never raw customer data."""

        advisor_lines = "; ".join(
            (
                f"{row['advisor_name']}: {row['closed_ros']} closed ROs, "
                f"${row['sales']:.2f} sales, ${row['declined_value']:.2f} declined"
            )
            for row in advisors
        )
        return (
            "Write a concise fixed-operations manager briefing using only these "
            f"synthetic aggregate metrics for {store_name} on {business_date}. "
            f"Closed ROs: {kpis['closed_ros']}; open ROs: {kpis['open_ros']}; "
            f"sales: ${kpis['total_sales']:.2f}; gross profit: "
            f"${kpis['gross_profit']:.2f}; hours sold: {kpis['hours_sold']:.1f}; "
            f"ELR: ${kpis['effective_labor_rate']:.2f}; declined: "
            f"${kpis['declined_value']:.2f}; exceptions: "
            f"{kpis['exception_count']}. Advisor aggregates: {advisor_lines}. "
            "Call out one win, one risk, and one next action. Do not invent data."
        )

    @staticmethod
    def _fallback_summary(kpis: dict[str, Any]) -> str:
        return (
            f"{kpis['closed_ros']} repair orders closed for "
            f"${kpis['total_sales']:,.2f} in sales. "
            f"${kpis['declined_value']:,.2f} remains declined across "
            f"{kpis['exception_count']} flagged repair orders; review the "
            "exception list with advisors before the next dispatch meeting."
        )
