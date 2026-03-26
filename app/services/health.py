from __future__ import annotations

import inspect
from dataclasses import dataclass
from time import perf_counter
from typing import Awaitable, Callable, Literal

HealthStatus = Literal["ok", "degraded", "down"]
ProbeCallable = Callable[[], HealthStatus | None | Awaitable[HealthStatus | None]]


@dataclass(frozen=True)
class HealthProbe:
    name: str
    check: ProbeCallable


@dataclass(frozen=True)
class HealthCheckResult:
    name: str
    status: HealthStatus
    latency_ms: float
    error: str | None = None


@dataclass(frozen=True)
class HealthReport:
    status: HealthStatus
    service: str
    trace_id: str
    checks: list[HealthCheckResult]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "service": self.service,
            "trace_id": self.trace_id,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "latency_ms": check.latency_ms,
                    "error": check.error,
                }
                for check in self.checks
            ],
        }


class HealthService:
    def __init__(self, probes: list[HealthProbe] | None = None, service_name: str = "keagent") -> None:
        self.probes = probes or [HealthProbe(name="self", check=self._default_probe)]
        self.service_name = service_name

    @staticmethod
    def _default_probe() -> HealthStatus:
        return "ok"

    async def _run_probe(self, probe: HealthProbe) -> HealthCheckResult:
        started = perf_counter()
        try:
            probe_result = probe.check()
            if inspect.isawaitable(probe_result):
                probe_result = await probe_result
            status: HealthStatus = "ok" if probe_result is None else probe_result
            if status not in ("ok", "degraded", "down"):
                status = "down"
                error = f"invalid status from probe {probe.name}"
            else:
                error = None
        except Exception as exc:  # pragma: no cover - directly covered by failure test
            status = "down"
            error = str(exc)

        latency_ms = round((perf_counter() - started) * 1000, 3)
        return HealthCheckResult(name=probe.name, status=status, latency_ms=latency_ms, error=error)

    @staticmethod
    def _merge_status(checks: list[HealthCheckResult]) -> HealthStatus:
        has_down = any(check.status == "down" for check in checks)
        if has_down:
            return "down"
        has_degraded = any(check.status == "degraded" for check in checks)
        if has_degraded:
            return "degraded"
        return "ok"

    async def run(self, trace_id: str) -> HealthReport:
        checks: list[HealthCheckResult] = []
        for probe in self.probes:
            checks.append(await self._run_probe(probe))
        status = self._merge_status(checks)
        return HealthReport(status=status, service=self.service_name, trace_id=trace_id, checks=checks)
