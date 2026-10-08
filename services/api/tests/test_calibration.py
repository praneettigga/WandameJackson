import math

import pytest

from roomshift_api.calibration import compute_calibration
from roomshift_api.errors import ApiError


def test_100px_is_2m():
    c = compute_calibration((50, 50), (150, 50), 2.0, 300, 250)
    assert math.isclose(c["metersPerPixel"], 0.02)
    diag = compute_calibration((0, 0), (60, 80), 2.0, 300, 250)  # 100 px diagonal
    assert math.isclose(diag["metersPerPixel"], 0.02)


@pytest.mark.parametrize("a,b,d", [
    ((50, 50), (50, 50), 2.0),          # zero separation
    ((50, 50), (150, 50), 0.0),         # zero distance
    ((50, 50), (150, 50), -1.0),        # negative
    ((50, 50), (150, 50), float("nan")),
    ((50, 50), (150, 50), float("inf")),
    ((-1, 50), (150, 50), 2.0),         # outside image
    ((50, 50), (301, 50), 2.0),
    ((50, float("nan")), (150, 50), 2.0),
])
def test_invalid_references(a, b, d):
    with pytest.raises(ApiError) as e:
        compute_calibration(a, b, d, 300, 250)
    assert e.value.code == "INVALID_CALIBRATION"
