"""How badly a merchant traded outside its hours, not merely that it did.

Both timing detectors used to score a flat 0.5, so every temporal alert in the
queue carried the identical rank and the ordering between them was arbitrary —
one transaction at 03:00 sorted alongside four hundred of them. Rank is what
decides which case an analyst opens first, so a constant makes the whole
ordering meaningless for that alert type.

The feedback names the missing input directly: the volume of off-hours
transactions should drive severity. Depth is folded in alongside it, because
one transaction at an hour a merchant has *never* traded is a different claim
from one at the quiet edge of its evening.
"""

import pytest

from compliance.detection.timedensity import temporal_severity


class TestVolumeDrivesSeverity:
    def test_more_off_hours_transactions_score_higher(self):
        few = temporal_severity(odd_count=1, day_count=100, depth=0.5)
        many = temporal_severity(odd_count=40, day_count=100, depth=0.5)
        assert many > few

    def test_the_whole_day_off_hours_is_worse_than_a_stray_one(self):
        stray = temporal_severity(odd_count=1, day_count=200, depth=0.5)
        whole = temporal_severity(odd_count=200, day_count=200, depth=0.5)
        assert whole > stray

    def test_share_matters_as_well_as_count(self):
        """Ten off-hours transactions out of ten is a merchant operating at
        the wrong time. Ten out of a thousand is a late closer."""
        dominant = temporal_severity(odd_count=10, day_count=10, depth=0.5)
        marginal = temporal_severity(odd_count=10, day_count=1000, depth=0.5)
        assert dominant > marginal


class TestDepthDrivesSeverity:
    def test_an_hour_never_traded_scores_above_a_quiet_one(self):
        edge = temporal_severity(odd_count=5, day_count=50, depth=0.05)
        unheard_of = temporal_severity(odd_count=5, day_count=50, depth=1.0)
        assert unheard_of > edge


class TestTheScoreStaysComparable:
    """Family A, B and C scores are blended and ranked against each other, so
    this has to stay inside the same [0, 1] range as the z-score detectors."""

    @pytest.mark.parametrize(
        "odd,day,depth",
        [
            (0, 0, 0.0),
            (1, 1, 0.0),
            (1, 1, 1.0),
            (10_000, 10_000, 1.0),
            (1, 10_000, 0.0),
            (5, 3, 2.0),  # nonsense inputs must still be bounded
        ],
    )
    def test_is_bounded(self, odd, day, depth):
        assert 0.0 <= temporal_severity(
            odd_count=odd, day_count=day, depth=depth
        ) <= 1.0

    def test_no_off_hours_transactions_scores_nothing(self):
        assert temporal_severity(odd_count=0, day_count=50, depth=0.9) == 0.0

    def test_distinct_inputs_give_distinct_scores(self):
        """The bug being fixed: every temporal alert arriving at 0.5."""
        scores = {
            temporal_severity(odd_count=n, day_count=100, depth=0.4)
            for n in (1, 2, 5, 10, 25, 60)
        }
        assert len(scores) == 6
