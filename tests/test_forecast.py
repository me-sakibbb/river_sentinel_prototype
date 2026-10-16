"""Checks a reviewer can run without Earth Engine: `python -m pytest tests`.

The most important one is test_no_future_leakage: the forecast's inputs for
year t must not change, at all, when every map after year t is replaced.
"""
import numpy as np
import pytest

from detector import forecast as fc

NODATA = fc.NODATA


def synthetic_river(years=12, h=240, w=160, seed=0):
    """A river band that drifts east a little every year, with cloud gaps."""
    rng = np.random.default_rng(seed)
    water, ndvi = [], []
    cols = np.arange(w)
    for i in range(years):
        centre = 50 + 4 * i + 6 * np.sin(np.arange(h) / 25 + i)
        wat = (np.abs(cols[None, :] - centre[:, None]) < 18).astype(np.uint8)
        nd = np.where(wat == 1, 90, 150).astype(np.uint8)       # water NDVI < 0, fields ~0.5
        gap = rng.random((h, w)) < 0.03                          # clouds
        if i < 8:
            gap[100:140, 40:120] = True                          # a gap that lasts for years
        wat[gap], nd[gap] = NODATA, NODATA
        water.append(wat)
        ndvi.append(nd)
    return np.stack(water), np.stack(ndvi)


def test_fill_forward_never_uses_later_years():
    stack = np.array([[[NODATA]], [[1]], [[NODATA]]], np.uint8)
    out = fc.fill_forward(stack)
    # Year 0 was never observed before: it must NOT be filled from year 1.
    assert out[0, 0, 0] == 0
    assert out[2, 0, 0] == 1  # carried forward from year 1


def test_shift_does_not_wrap_around():
    a = np.arange(16, dtype=np.float32).reshape(4, 4)
    up = fc.shift_from(a, -1, 0)           # value one row north
    assert np.isnan(up[0]).all()           # top row has nothing north of it
    assert (up[1:] == a[:-1]).all()
    left = fc.shift_from(a, 0, -2)
    assert np.isnan(left[:, :2]).all() and (left[:, 2:] == a[:, :-2]).all()


def test_no_future_leakage():
    water, ndvi = synthetic_river()
    years = list(range(2000, 2000 + len(water)))
    t = 2007
    k = years.index(t)
    scrambled_w, scrambled_n = synthetic_river(seed=99)
    w2, n2 = water.copy(), ndvi.copy()
    w2[k + 1:], n2[k + 1:] = scrambled_w[k + 1:], scrambled_n[k + 1:]
    a = fc.build_table(years, fc.clean_water(water, ndvi), ndvi, "jamuna")
    b = fc.build_table(years, fc.clean_water(w2, n2), n2, "jamuna")
    for y in range(years[fc.HISTORY_YEARS], t + 1):
        sa, sb = a["year"] == y, b["year"] == y
        assert sa.sum() > 0
        assert sa.sum() == sb.sum(), f"which cells are scored in {y} depends on later years"
        assert (a["row"][sa] == b["row"][sb]).all() and (a["col"][sa] == b["col"][sb]).all()
        np.testing.assert_array_equal(a["X"][sa], b["X"][sb], err_msg=f"inputs for {y} depend on later years")
    # ...and the future does matter for the label, so the test isn't vacuous.
    assert not np.array_equal(a["loss_ha"][a["year"] == t], b["loss_ha"][b["year"] == t])


def test_last_year_has_no_label():
    water, ndvi = synthetic_river()
    years = list(range(2000, 2000 + len(water)))
    tab = fc.build_table(years, fc.clean_water(water, ndvi), ndvi, "jamuna")
    assert np.isnan(tab["loss_ha"][tab["year"] == years[-1]]).all()
    assert not np.isnan(tab["loss_ha"][tab["year"] == years[-2]]).any()


def test_isolated_pond_is_not_erosion():
    """Label counts land that joins the RIVER, not a field that floods on its own."""
    h, w = 240, 160
    water = np.zeros((7, h, w), np.uint8)
    water[:, :, 60:100] = 1                    # river
    ndvi = np.where(water == 1, 90, 150).astype(np.uint8)
    water[6, 20:26, 10:16] = 1                 # a 36 px pond far from the river in the last year
    ndvi[6, 20:26, 10:16] = 90
    tab = fc.build_table(list(range(2000, 2007)), water, ndvi, "jamuna")
    assert np.nansum(tab["loss_ha"]) == 0


def test_capture():
    score = np.array([0.9, 0.8, 0.1, 0.0])
    loss = np.array([3.0, 0.0, 1.0, 0.0])
    assert fc.capture(score, loss, 0.25) == pytest.approx(0.75)
    assert fc.capture(score, loss, 1.0) == pytest.approx(1.0)
