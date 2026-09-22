from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from agent_ipc import RuntimeSnapshot


class AgentHealthState(enum.Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"


@dataclasses.dataclass(frozen=True)
class AgentHealth:
    state: AgentHealthState
    detail: str
    snapshot: RuntimeSnapshot | None = None


def _snapshot_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("snapshot timestamp must include a timezone")
    return parsed


def evaluate_agent_health(
    *,
    startup_enabled: bool,
    snapshot: RuntimeSnapshot | None,
    ipc_ok: bool | None,
    check_interval_seconds: int,
    now: dt.datetime,
    startup_deadline: dt.datetime,
) -> AgentHealth:
    if not startup_enabled:
        return AgentHealth(AgentHealthState.DISABLED, "系统级后台认证未启用")
    if snapshot is None:
        if now <= startup_deadline:
            return AgentHealth(AgentHealthState.STARTING, "等待系统认证代理启动")
        return AgentHealth(AgentHealthState.DEGRADED, "系统认证代理没有生成运行状态")
    try:
        updated_at = _snapshot_time(snapshot.updated_at)
    except (TypeError, ValueError):
        return AgentHealth(AgentHealthState.DEGRADED, "系统认证代理状态时间无效", snapshot)
    freshness_limit = max(3 * check_interval_seconds, 120)
    if (now - updated_at).total_seconds() > freshness_limit:
        return AgentHealth(AgentHealthState.DEGRADED, "系统认证代理状态已过期", snapshot)
    if ipc_ok is True:
        return AgentHealth(AgentHealthState.HEALTHY, snapshot.detail or snapshot.state, snapshot)
    if ipc_ok is False:
        return AgentHealth(AgentHealthState.DEGRADED, "系统认证代理命令通道不可用", snapshot)
    if now <= startup_deadline:
        return AgentHealth(AgentHealthState.STARTING, "正在验证系统认证代理", snapshot)
    return AgentHealth(AgentHealthState.DEGRADED, "系统认证代理未通过健康检查", snapshot)
