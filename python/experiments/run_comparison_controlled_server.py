"""Start an isolated C3 acceptance server with a fixed injected failure/status fixture.

This never changes the normal application configuration or exposes a demo route.
It exists only so special business/error states can be exercised over real HTTP.
"""

from __future__ import annotations

import argparse

import uvicorn

from api import app as api_module
from orchestrator.comparison_service import PlanComparisonApplicationService
from tests.fixtures.multi_plan import DESTINATION_B
from tests.fixtures.plan_comparison import c2_orchestrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=["only_feasible", "agent_failure"],
    )
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    options = (
        {}
        if args.scenario == "only_feasible"
        else {"failing_flight_city": DESTINATION_B.city}
    )
    api_module._comparison_service = PlanComparisonApplicationService(
        orchestrator_factory=lambda: c2_orchestrator(**options)
    )
    uvicorn.run(
        api_module.app,
        host="127.0.0.1",
        port=args.port,
        log_level="error",
        access_log=False,
    )


if __name__ == "__main__":
    main()
