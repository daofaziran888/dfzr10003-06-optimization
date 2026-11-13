"""应用层输出业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.factchange import fact_change_service
from app.store import store

MODULE = "ads"
REQUIRED_FIELDS = ["流水编号", "输入表名", "输出表名"]
STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
ACTION_RULES = {"启动输出": "草稿", "暂停输出": "运行中", "归档流水": "已暂停"}
NEGATIVE_ACTIONS = []


class AdsService:
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
        items = []
        for row in rows[start:start + size]:
            # 输出前按汇总层最新待办同步一次，未闭合的度量变更不能被当成已完成结果。
            overlap = fact_change_service.downstream_state(row)
            if overlap:
                row.update(overlap)
            items.append(row)
        return items, total

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

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            return self._run_action(entry_id, action)

    def _run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"输出流水 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于应用层输出可执行范围"
        overlap = fact_change_service.downstream_state(entry)
        if overlap:
            entry.update(overlap)
            # 待复核期间只允许停流，启动输出/归档都会把未闭环的度量当成已完成结果。
            if action != "暂停输出":
                return None, (
                    f"上游事实定义变更 {overlap.get('待复核变更')} 尚未复核，"
                    f"输出流水 {entry.get('流水编号')} 必须保持待复核，不能作为已完成结果输出"
                )
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"输出流水已{action}"
