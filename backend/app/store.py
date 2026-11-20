"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.fixtures import build_trend_observations
from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._tables.setdefault("_pipeline_results", [])
        # 时序缺口能力依赖的三张内部表：
        # _trend_observations 已确认观测（同一营业日只保留一条真实观测）、
        # _trend_gap_checks 时序缺口数据集（补数前后都能核对）、
        # _forecast_runs 预测训练快照（历史训练明细保留当时的数据）。
        self._tables.setdefault(
            "_trend_observations",
            build_trend_observations(self._tables.get("trend_series", [])),
        )
        self._tables.setdefault("_trend_gap_checks", [])
        self._tables.setdefault("_forecast_runs", [])

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()


def reset_store() -> None:
    """重新初始化全局 store，用于测试隔离。

    各服务模块以 `from app.store import store` 持有引用，必须原地重置，
    重新赋值只会让旧对象继续累积状态。
    """
    fresh = Store()
    store._tables.clear()
    store._tables.update(fresh._tables)
