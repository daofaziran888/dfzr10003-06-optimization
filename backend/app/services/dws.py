"""汇总层聚合业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.factchange import fact_change_service
from app.store import store

MODULE = "dws"
REQUIRED_FIELDS = ["流水编号", "输入表名", "输出表名"]
STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
ACTION_RULES = {"启动聚合": "草稿", "暂停聚合": "运行中", "归档流水": "已暂停"}
NEGATIVE_ACTIONS = []
REVIEW_ACTIONS = {"重跑聚合", "核对聚合"}


class DwsService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("流水编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        conclusion: str | None = None,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"聚合流水 {entry_id} 不存在或已归档"
        if action in REVIEW_ACTIONS:
            if action == "重跑聚合":
                return fact_change_service.rerun_aggregation(entry_id)
            return fact_change_service.verify_aggregation(
                entry_id,
                conclusion or str((values or {}).get("复核结论") or ""),
            )
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"聚合流水 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于汇总层聚合可执行范围"
            ticket, _entry = fact_change_service.ticket_for_aggregation(entry_id)
            if ticket is not None and action == "归档流水":
                return None, (
                    f"聚合流水 {entry.get('流水编号')} 处于变更 {ticket.get('变更编号')} 的复核窗口，"
                    f"历史口径需原样保留，不能归档覆盖"
                )
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"聚合流水已{action}"

    def review_view(self, entry_id: int | None = None) -> dict[str, Any]:
        """汇总页的影响视图：与事实页展示同一份影响原因、旧口径、新口径与处理状态。"""
        tickets = fact_change_service.list_tickets(dws_id=entry_id)
        current = tickets[0] if tickets else None
        return {
            "module": "dws",
            "entry_id": entry_id,
            "tickets": tickets,
            "current": current,
            "处理状态": (current or {}).get("处理状态", ""),
            "复核结论": (current or {}).get("复核结论", ""),
        }
