"""事实表管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.factchange import PENDING, TRACKED_FIELDS, fact_change_service
from app.store import store

MODULE = "factdef"
REQUIRED_FIELDS = ["事实编号", "事实名称", "源表名称"]
STATUS_ORDER = ["草稿", "待审批", "已生效", "已废弃"]
ACTION_RULES = {"提交审批": "待审批", "发布事实": "已生效", "废弃事实": "已废弃"}
NEGATIVE_ACTIONS = []


def _text_value(value: Any) -> str:
    return str(value or "").strip()


class FactdefService:
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
            rows = [row for row in rows if keyword in str(row.get("事实编号", ""))]
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
        for field in TRACKED_FIELDS:
            entry[field] = values.get(field) or ""
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["版本号"] = 0
        entry["生效时间"] = ""
        entry["版本状态"] = "未发布"
        entry["处理状态"] = "正常"
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        *,
        effective_time: Any = None,
        version: Any = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """返回 (事实定义, 说明, 是否被未复核变更拦下)。"""
        all_values = {
            field: values[field]
            for field in TRACKED_FIELDS
            if field in (values or {})
        }

        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"事实定义 {entry_id} 不存在或已归档", False
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于事实表管理可执行范围", False
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里", False

            submitted = {
                field: value
                for field, value in all_values.items()
                if _text_value(value) != _text_value(entry.get(field))
            }
            pending_ticket = fact_change_service.pending_for_fact(entry_id)

            # 提交的口径字段与当前生效口径不同时，先走失效检测，不直接放行新版本。
            if submitted and action in ("提交审批", "发布事实"):
                result = fact_change_service.detect_change(
                    entry_id,
                    submitted,
                    effective_time=effective_time,
                    version=version,
                )
                ticket = result.get("ticket") or {}
                if ticket.get("处理状态") == PENDING:
                    return entry, (
                        f"{result.get('message')}；变更 {ticket.get('变更编号')} 尚未复核，"
                        f"v{ticket.get('新版本号')} 不能由事实页单独宣布生效，"
                        f"请在汇总层重跑并核对受影响聚合后再发布"
                    ), True
                if not result.get("activated") and result.get("message"):
                    return entry, str(result.get("message")), False

            if action == "发布事实" and pending_ticket is not None:
                return entry, (
                    f"变更 {pending_ticket.get('变更编号')} 尚未复核，v"
                    f"{pending_ticket.get('新版本号')} 不能由事实页单独宣布生效；"
                    f"请等受影响聚合重跑并核对后再发布"
                ), True

            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            if action == "发布事实" and not entry.get("生效时间"):
                entry["生效时间"] = str(effective_time or "").strip() or entry.get("生效时间") or ""
            return entry, f"事实定义已{action}", False
