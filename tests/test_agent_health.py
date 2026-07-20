import datetime as dt
import unittest

from agent_health import AgentHealthState, evaluate_agent_health
from agent_ipc import RuntimeSnapshot


NOW = dt.datetime(2026, 7, 20, 22, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def snapshot(updated_at: str) -> RuntimeSnapshot:
    return RuntimeSnapshot("boot", "online_campus", detail="already authenticated", updated_at=updated_at)


class AgentHealthTests(unittest.TestCase):
    def test_missing_tray_task_is_disabled(self):
        health = evaluate_agent_health(
            startup_enabled=False,
            snapshot=None,
            ipc_ok=None,
            check_interval_seconds=30,
            now=NOW,
            startup_deadline=NOW,
        )
        self.assertIs(health.state, AgentHealthState.DISABLED)

    def test_missing_snapshot_inside_startup_window_is_starting(self):
        health = evaluate_agent_health(
            startup_enabled=True,
            snapshot=None,
            ipc_ok=None,
            check_interval_seconds=30,
            now=NOW,
            startup_deadline=NOW + dt.timedelta(seconds=60),
        )
        self.assertIs(health.state, AgentHealthState.STARTING)

    def test_fresh_snapshot_and_successful_ipc_is_healthy(self):
        health = evaluate_agent_health(
            startup_enabled=True,
            snapshot=snapshot(NOW.isoformat()),
            ipc_ok=True,
            check_interval_seconds=30,
            now=NOW,
            startup_deadline=NOW,
        )
        self.assertIs(health.state, AgentHealthState.HEALTHY)

    def test_stale_snapshot_is_degraded_even_before_ipc_result(self):
        health = evaluate_agent_health(
            startup_enabled=True,
            snapshot=snapshot((NOW - dt.timedelta(seconds=121)).isoformat()),
            ipc_ok=None,
            check_interval_seconds=30,
            now=NOW,
            startup_deadline=NOW + dt.timedelta(seconds=60),
        )
        self.assertIs(health.state, AgentHealthState.DEGRADED)
        self.assertIn("状态已过期", health.detail)

    def test_fresh_snapshot_with_failed_ipc_is_degraded(self):
        health = evaluate_agent_health(
            startup_enabled=True,
            snapshot=snapshot(NOW.isoformat()),
            ipc_ok=False,
            check_interval_seconds=30,
            now=NOW,
            startup_deadline=NOW + dt.timedelta(seconds=60),
        )
        self.assertIs(health.state, AgentHealthState.DEGRADED)
        self.assertIn("命令通道不可用", health.detail)

    def test_naive_or_invalid_timestamp_is_degraded(self):
        for value in ("2026-07-20T22:30:00", "not-a-time"):
            with self.subTest(value=value):
                health = evaluate_agent_health(
                    startup_enabled=True,
                    snapshot=snapshot(value),
                    ipc_ok=True,
                    check_interval_seconds=30,
                    now=NOW,
                    startup_deadline=NOW,
                )
                self.assertIs(health.state, AgentHealthState.DEGRADED)


if __name__ == "__main__":
    unittest.main()
