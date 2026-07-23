import pytest

from compliance.detection.baselines import (
    Baseline,
    DispersionMethod,
    fit_baseline,
    score_value,
)

MIN_OBS = 12


def _repeat(value: float, n: int) -> list[float]:
    return [value] * n


class TestFitBaseline:
    def test_fits_median_and_mad_from_history(self):
        # symmetric around 100, absolute deviations are all 10
        values = [90.0, 110.0] * 10
        b = fit_baseline(values, min_observations=MIN_OBS)
        assert b.usable is True
        assert b.method is DispersionMethod.MAD
        assert b.center == pytest.approx(100.0)
        assert b.dispersion == pytest.approx(10.0)
        assert b.n == 20

    def test_median_is_unmoved_by_a_single_huge_outlier(self):
        """The whole point of robust statistics: one legitimate large sale
        must not drag the baseline the way a mean would."""
        values = [100.0] * 19 + [50_000.0]
        b = fit_baseline(values, min_observations=MIN_OBS)
        assert b.center == pytest.approx(100.0)

    def test_unusable_when_history_too_short(self):
        b = fit_baseline([100.0, 105.0, 95.0], min_observations=MIN_OBS)
        assert b.usable is False
        assert b.method is DispersionMethod.INSUFFICIENT_DATA

    def test_falls_back_to_scaled_iqr_when_mad_is_zero(self):
        """A fixed-price merchant that also sells a few small items: the price
        point is >half the window so MAD collapses to 0, but the cheap tail
        still leaves the lower quartile outside the block, so the IQR can
        measure spread. Must not divide by zero."""
        values = [10.0, 20.0, 30.0, 40.0] + _repeat(100.0, 11)
        b = fit_baseline(values, min_observations=MIN_OBS)
        assert b.method is DispersionMethod.SCALED_IQR
        assert b.dispersion > 0

    def test_constant_history_is_flagged_not_scored(self):
        """A merchant selling one product at one fixed price has zero
        dispersion by every measure. Scoring it would divide by zero and
        flag every transaction."""
        b = fit_baseline(_repeat(250.0, 30), min_observations=MIN_OBS)
        assert b.method is DispersionMethod.CONSTANT
        assert b.dispersion == 0.0
        assert b.usable is False

    def test_empty_history_is_unusable(self):
        b = fit_baseline([], min_observations=MIN_OBS)
        assert b.usable is False


class TestScoreValue:
    def _usable(self) -> Baseline:
        return fit_baseline([90.0, 110.0] * 10, min_observations=MIN_OBS)

    def test_value_at_the_center_scores_zero(self):
        assert score_value(100.0, self._usable()).deviation == pytest.approx(0.0)

    def test_modified_zscore_uses_the_consistency_constant(self):
        # 0.6745 * (130 - 100) / 10 == 2.0235
        assert score_value(130.0, self._usable()).deviation == pytest.approx(2.0235)

    def test_bands_normal_moderate_outlier(self):
        b = self._usable()
        assert score_value(100.0, b).band == "normal"
        # 0.6745 * (145-100)/10 = 3.035 -> moderate
        assert score_value(145.0, b).band == "moderate"
        # 0.6745 * (160-100)/10 = 4.047 -> outlier
        assert score_value(160.0, b).band == "outlier"

    def test_only_outliers_flag(self):
        b = self._usable()
        assert score_value(160.0, b).is_outlier is True
        assert score_value(145.0, b).is_outlier is False

    def test_low_values_are_not_flagged_for_amount(self):
        """Amount risk is one-sided: an unusually small ticket is not an AML
        signal, and flagging it doubles the false-positive load."""
        b = self._usable()
        result = score_value(10.0, b)
        assert result.is_outlier is False

    def test_scoring_against_an_unusable_baseline_raises(self):
        unusable = fit_baseline([1.0], min_observations=MIN_OBS)
        with pytest.raises(ValueError):
            score_value(100.0, unusable)


class TestDeterminism:
    def test_same_input_yields_identical_baseline(self):
        """Audit requirement: re-running must reproduce the same numbers."""
        values = [12.0, 480.0, 33.5, 91.0, 7.25] * 6
        assert fit_baseline(values, min_observations=MIN_OBS) == fit_baseline(
            values, min_observations=MIN_OBS
        )

    def test_input_order_does_not_change_the_baseline(self):
        values = [12.0, 480.0, 33.5, 91.0, 7.25] * 6
        a = fit_baseline(values, min_observations=MIN_OBS)
        b = fit_baseline(list(reversed(values)), min_observations=MIN_OBS)
        assert a == b
