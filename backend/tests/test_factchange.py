"""事实定义变更失效检测的回归用例。

直接跑标准库 unittest，不额外引入依赖：
    cd backend && .venv/bin/python -m unittest tests.test_factchange -v
"""
from __future__ import annotations

import threading
import unittest

from app.services.ads import AdsService
from app.services.dws import DwsService
from app.services.factchange import FactChangeService
from app.services.factdef import FactdefService
from app.store import Store, store


def fresh_state() -> None:
    """每个用例从示例数据重新开始，避免用例间互相污染。"""
    from app.seed import SEED_ROWS

    store._tables = {
        name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
    }


class FactChangeFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        fresh_state()
        self.change = FactChangeService()
        self.fact = FactdefService()
        self.dws = DwsService()
        self.ads = AdsService()

    def fact_row(self):
        return store.find("factdef", 3)

    def dws_row(self, entry_id=2):
        return store.find("dws", entry_id)

    def test_tracked_fields_trigger_single_ticket(self) -> None:
        for field in ("粒度说明", "关联维度", "度量字段", "分区字段"):
            fresh_state()
            result = self.change.detect_change(3, {field: "变更后的值"})
            self.assertTrue(result["ok"], field)
            self.assertEqual(len(store.rows("factchange")), 1)
            ticket = self.change.list_tickets(factdef_id=3)[0]
            self.assertIn(field, ticket["变更字段"])
            self.assertEqual(ticket["处理状态"], "待复核")
            self.assertEqual(ticket["影响窗口"], ["2026-09-01 ~ 2026-09-30"])

    def test_repeat_detection_is_idempotent(self) -> None:
        first = self.change.detect_change(3, {"度量字段": "含税销售额、订单数、优惠金额"})
        second = self.change.detect_change(3, {"度量字段": "含税销售额、订单数、优惠金额"})
        self.assertTrue(second["merged"])
        self.assertEqual(first["ticket"]["变更编号"], second["ticket"]["变更编号"])
        self.assertEqual(len(store.rows("factchange")), 1)

    def test_concurrent_detection_stores_one_ticket(self) -> None:
        errors: list[Exception] = []

        def worker() -> None:
            try:
                self.change.detect_change(3, {"度量字段": "并发口径"})
            except Exception as error:  # pragma: no cover - 只在失败时记录
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(store.rows("factchange")), 1)

    def test_new_version_not_effective_until_review(self) -> None:
        result = self.change.detect_change(3, {"度量字段": "新度量"})
        self.assertFalse(result["activated"])
        fact = self.fact_row()
        self.assertEqual(fact["版本号"], 1)
        self.assertIn("待生效", fact["版本状态"])

        entry, message, blocked = self.fact.run_action(3, "发布事实")
        self.assertTrue(blocked)
        self.assertIn("尚未复核", message)
        self.assertEqual(fact["版本号"], 1)
        self.assertEqual(fact["度量字段"], "含税销售额、订单数")

        produced, rerun_message = self.dws.run_action(2, "重跑聚合")
        self.assertEqual(produced["复核状态"], "待核对")
        self.assertEqual(self.dws_row()["聚合字段"], "含税销售额、订单数")  # 历史口径未覆盖

        verified, verify_message = self.dws.run_action(produced["id"], "核对聚合")
        self.assertEqual(verified["复核状态"], "已核对")
        self.assertEqual(fact["版本号"], 2)
        self.assertEqual(fact["版本状态"], "v2 已生效")
        self.assertEqual(fact["度量字段"], "新度量")

    def test_history_aggregation_preserved(self) -> None:
        self.change.detect_change(3, {"度量字段": "新度量"})
        produced, _ = self.dws.run_action(2, "重跑聚合")
        self.dws.run_action(produced["id"], "核对聚合")

        original = self.dws_row()
        self.assertEqual(original["复核状态"], "历史口径已留存")
        self.assertEqual(original["聚合字段"], "含税销售额、订单数")
        self.assertTrue(original["历史口径"])

    def test_ads_blocks_output_while_review_pending(self) -> None:
        self.change.detect_change(3, {"度量字段": "新度量"})
        entry, message = self.ads.run_action(2, "启动输出")
        self.assertIsNone(entry)
        self.assertIn("尚未复核", message)

        listed, _ = self.ads.list_entries()
        self.assertEqual(listed[1]["处理状态"], "待复核（暂停输出）")
        self.assertEqual(listed[1]["待复核变更"], self.change.list_tickets(factdef_id=3)[0]["变更编号"])

    def test_fact_and_dws_pages_share_impact_view(self) -> None:
        self.change.detect_change(3, {"度量字段": "新度量"})
        fact_view = self.change.list_tickets(factdef_id=3)[0]
        dws_view = self.dws.review_view(2)["tickets"][0]
        for key in ("影响原因", "旧口径", "新口径", "影响窗口", "处理状态", "复核结论"):
            self.assertEqual(fact_view[key], dws_view[key], key)

    def test_priority_adjudicated_by_version_and_effective_time(self) -> None:
        self.change.detect_change(3, {"度量字段": "低版本"}, version=2, effective_time="2026-10-01 00:00:00")
        result = self.change.detect_change(
            3, {"关联维度": "高版本维度"}, version=7, effective_time="2026-10-09 00:00:00"
        )
        ticket = result["ticket"]
        self.assertEqual(ticket["新版本号"], 7)
        self.assertIn("优先级更高", ticket["裁决说明"])
        self.assertEqual(len(store.rows("factchange")), 1)

    def test_lower_priority_cannot_override_winner(self) -> None:
        self.change.detect_change(3, {"度量字段": "胜出口径"}, version=9, effective_time="2026-10-09 00:00:00")
        result = self.change.detect_change(
            3, {"度量字段": "落败口径"}, version=2, effective_time="2026-10-10 00:00:00"
        )
        self.assertTrue(result["merged"])
        self.assertEqual(result["ticket"]["新口径"]["度量字段"], "胜出口径")

    def test_fact_without_referencing_aggregation_activates_directly(self) -> None:
        result = self.change.detect_change(1, {"度量字段": "独立度量"}, version=1)
        self.assertTrue(result["activated"])
        fact = store.find("factdef", 1)
        self.assertEqual(fact["度量字段"], "独立度量")
        self.assertEqual(len(store.rows("factchange")), 0)


if __name__ == "__main__":
    unittest.main()
