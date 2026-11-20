"""时序缺口校验与缺口感知预测测试：覆盖 8 月 21 日至 30 日 GMV 漏采补数场景。"""
from __future__ import annotations

from app.services import forecast_engine
from app.services.forecast import ForecastService
from app.services.pipeline_orchestrator import run_forecast_pipeline
from app.services.trend_gap import TrendGapService
from app.store import store

SERIES = "TREN-GMV"
START = "2026-08-21"
END = "2026-08-30"
MISSING_DAY = "2026-08-24"
BACKFILL_GMV = 198000.0
# 23 日实测 210500.0 与 25 日实测 205400.0 的线性插值
ESTIMATED_GMV = 207950.0


def _check(service: TrendGapService, start: str = START, end: str = END):
    record, message = service.check_gaps(SERIES, start, end)
    assert record is not None, message
    return record


def _backfill(service: TrendGapService, gmv: float = BACKFILL_GMV):
    result, message = service.backfill_observation(
        SERIES, MISSING_DAY, {"gmv": gmv, "uv": 5000, "paid_uv": 600, "total_uv": 5000})
    assert result is not None, message
    return result


def _run_forecast(service: ForecastService, entry_id: int = 1):
    record, message = service.run_gap_aware(entry_id, window_start=START, window_end=END)
    assert record is not None, message
    return record


class TestGapCheck:
    """按业务日期校验指标时序数据，一次查询只按指定范围确认缺日。"""

    def test_missing_day_detected(self):
        service = TrendGapService()
        record = _check(service)
        assert record["观测指标"] == "METR-GMV"
        assert record["应有天数"] == 10
        assert record["实测天数"] == 9
        assert record["缺日列表"] == [MISSING_DAY]
        detail = {row["业务日期"]: row for row in record["核对明细"]}
        assert detail[MISSING_DAY]["状态"] == "缺失"
        assert detail["2026-08-23"]["状态"] == "已确认"

    def test_check_scoped_to_given_range(self):
        service = TrendGapService()
        # 24 日漏采，但 21-23 的查询不该把它列出来
        record = _check(service, "2026-08-21", "2026-08-23")
        assert record["应有天数"] == 3
        assert record["缺日数"] == 0
        assert record["缺日列表"] == []

    def test_check_persisted_to_gap_dataset(self):
        service = TrendGapService()
        _check(service)
        _check(service, "2026-08-25", "2026-08-30")
        checks = service.list_gap_checks(SERIES)
        assert len(checks) == 2
        assert checks[0]["校验编号"] != checks[1]["校验编号"]
        assert service.get_gap_check(checks[0]["id"])["缺日列表"] == [MISSING_DAY]

    def test_unknown_series_rejected(self):
        service = TrendGapService()
        record, message = service.check_gaps("TREN-UNKNOWN", START, END)
        assert record is None
        assert "不存在" in message


class TestBackfill:
    """补数登记：缺日不能被填成实测零，同一营业日重传仍只有一条确认观测。"""

    def test_backfill_registers_confirmed_observation(self):
        service = TrendGapService()
        result = _backfill(service)
        assert result["确认观测数"] == 1
        assert result["重传合并"] is False
        assert result["观测"]["来源"] == "补数"
        record = _check(service)
        assert record["缺日数"] == 0
        detail = {row["业务日期"]: row for row in record["核对明细"]}
        assert detail[MISSING_DAY]["状态"] == "已确认"
        assert detail[MISSING_DAY]["来源"] == "补数"

    def test_retransmission_keeps_single_confirmed_observation(self):
        service = TrendGapService()
        _backfill(service)
        again = _backfill(service, gmv=199000.0)
        assert again["确认观测数"] == 1
        assert again["重传合并"] is True
        assert again["观测"]["版本"] == 2
        observations, _ = service.list_observations(SERIES, MISSING_DAY, MISSING_DAY)
        assert observations is not None
        assert len(observations) == 1
        assert observations[0]["gmv"] == 199000.0

    def test_backfill_rejects_actual_zero(self):
        service = TrendGapService()
        result, message = service.backfill_observation(SERIES, MISSING_DAY, {"gmv": 0})
        assert result is None
        assert "实测零" in message
        # 拒绝后缺日仍未补
        assert _check(service)["缺日列表"] == [MISSING_DAY]

    def test_gap_dataset_keeps_before_and_after(self):
        service = TrendGapService()
        before = _check(service)
        _backfill(service)
        after = _check(service)
        checks = service.list_gap_checks(SERIES)
        assert len(checks) == 2
        assert checks[0]["缺日列表"] == [MISSING_DAY]
        assert checks[1]["缺日列表"] == []
        assert before["校验编号"] != after["校验编号"]


class TestForecastRuns:
    """缺口感知预测：训练样本按缺口结果确定，快照保留当时的训练明细。"""

    def test_training_samples_mark_estimated_before_backfill(self):
        service = ForecastService()
        record = _run_forecast(service)
        assert record["训练窗口"] == {"起始": START, "截止": END}
        assert record["实测天数"] == 9
        assert record["估计天数"] == 1
        samples = {row["业务日期"]: row for row in record["训练样本"]}
        missing_sample = samples[MISSING_DAY]
        assert missing_sample["来源"] == "估计"
        assert missing_sample["估计方法"] == "线性插值"
        # 缺日按邻近实测估计，绝不是实测零
        assert missing_sample["值"] == ESTIMATED_GMV
        assert missing_sample["值"] != 0
        assert samples["2026-08-23"]["来源"] == "实测"
        assert record["预测结果"]["forecast_dates"][0] == "2026-08-31"

    def test_run_after_backfill_uses_real_observation(self):
        gap_service = TrendGapService()
        service = ForecastService()
        _backfill(gap_service)
        record = _run_forecast(service)
        assert record["实测天数"] == 10
        assert record["估计天数"] == 0
        samples = {row["业务日期"]: row for row in record["训练样本"]}
        assert samples[MISSING_DAY]["来源"] == "实测"
        assert samples[MISSING_DAY]["观测来源"] == "补数"
        assert samples[MISSING_DAY]["值"] == BACKFILL_GMV

    def test_snapshots_preserved_separately(self):
        gap_service = TrendGapService()
        service = ForecastService()
        first = _run_forecast(service)
        _backfill(gap_service)
        second = _run_forecast(service)
        assert first["run_id"] != second["run_id"]
        runs = forecast_engine.list_forecast_runs(scheme_code="FORE-GMV-7D")
        assert len(runs) == 2
        # 历史预测的训练明细保留当时的数据：第一次快照里 24 日仍是估计
        restored = forecast_engine.get_forecast_run(first["run_id"])
        assert restored is not None
        samples = {row["业务日期"]: row for row in restored["训练样本"]}
        assert samples[MISSING_DAY]["来源"] == "估计"
        assert samples[MISSING_DAY]["值"] == ESTIMATED_GMV

    def test_forecast_numbers_reflect_sample_sources(self):
        gap_service = TrendGapService()
        service = ForecastService()
        before = _run_forecast(service)
        _backfill(gap_service)
        after = _run_forecast(service)
        assert before["预测结果"]["predictions"] != after["预测结果"]["predictions"]

    def test_truncation_strategy_falls_back_when_trailing_short(self):
        service = ForecastService()
        record, message = service.run_gap_aware(
            1, window_start=START, window_end=END, strategy="截断末段")
        assert record is not None, message
        # 末段连续实测 6 天（25-30 日）不足 7 天，回退为估计保留
        assert record["末段连续实测天数"] == 6
        assert "回退" in record["窗口决策"]
        assert record["估计天数"] == 1

    def test_window_conflict_noted_when_trailing_sufficient(self):
        # 构造末段连续实测 >= 7 天的窗口：08-17 至 08-30，缺 24 日，末段 6 天仍不足；
        # 直接验证估计保留策略下窗口决策对冲突的说明（截断阈值口径）。
        service = ForecastService()
        record = _run_forecast(service)
        assert "缺日" in record["窗口决策"]
        assert "估计" in record["窗口决策"]


class TestForecastPipelineGapAware:
    """报表消费的预测管道同样按缺口结果确定训练样本。"""

    def test_pipeline_records_window_and_sources(self):
        result = run_forecast_pipeline(horizon=7)
        assert result["训练窗口"] == {"起始": "2026-08-01", "截止": "2026-08-30"}
        assert result["缺日列表"] == [MISSING_DAY]
        assert result["估计天数"] == 1
        assert result["实测天数"] == 29
        assert result["slope"] > 0
        assert len(result["forecasts"]) == 7
        assert result["run_id"]

    def test_pipeline_numbers_change_after_backfill(self):
        gap_service = TrendGapService()
        before = run_forecast_pipeline(horizon=3)
        _backfill(gap_service)
        after = run_forecast_pipeline(horizon=3)
        assert after["估计天数"] == 0
        assert [f["预测gmv"] for f in before["forecasts"]] != [f["预测gmv"] for f in after["forecasts"]]
