"""Change classification on synthetic water maps (no Earth Engine needed)."""
import numpy as np
import pytest

from detector import change


@pytest.fixture
def scene():
    grid = change.Grid([89.70, 24.00, 89.72, 24.01])          # ~10 m pixels
    h, w = grid.height, grid.width
    before = np.zeros((h, w), bool)
    before[:, :50] = True                                     # the river, west of column 50
    inside = np.ones((h, w), bool)
    return grid, before, inside


def ha(grid, px):
    return px * grid.px_area_m2 / 1e4


def test_bank_retreat_on_established_land_is_erosion(scene):
    grid, before, inside = scene
    after = before.copy()
    after[:, 50:70] = True                                     # bank moved 200 m east
    stats, _, classes = change.analyse(before, after, inside, grid, established=np.ones_like(before))
    assert stats["bank_erosion_ha"] == pytest.approx(ha(grid, 20 * grid.height), rel=0.01)
    assert stats["flooded_low_land_ha"] == 0
    assert (classes[:, 50:70] == 1).all()


def test_flooded_sandbar_is_not_erosion(scene):
    grid, before, inside = scene
    after = before.copy()
    after[:, 50:70] = True
    established = np.ones_like(before)
    established[:, 50:70] = False                              # a low sandbar, recently water
    stats, _, classes = change.analyse(before, after, inside, grid, established=established)
    assert stats["bank_erosion_ha"] == 0
    assert stats["flooded_low_land_ha"] == pytest.approx(ha(grid, 20 * grid.height), rel=0.01)
    assert (classes[:, 50:70] == 4).all()


def test_field_behind_a_thin_sand_strip_still_counts(scene):
    grid, before, inside = scene
    after = before.copy()
    after[:, 50:70] = True
    established = np.ones_like(before)
    established[:, 50:52] = False                              # 20 m of bare sand at the bank
    stats, _, _ = change.analyse(before, after, inside, grid, established=established)
    assert stats["bank_erosion_ha"] == pytest.approx(ha(grid, 18 * grid.height), rel=0.01)


def test_isolated_flooded_field_is_not_erosion(scene):
    grid, before, inside = scene
    after = before.copy()
    after[10:40, 150:190] = True                               # a field far from the river floods
    stats, patches, _ = change.analyse(before, after, inside, grid, established=np.ones_like(before))
    assert stats["bank_erosion_ha"] == 0
    assert stats["isolated_new_water_ha"] > 0
    assert all(p["kind"] == "isolated_new_water" for p in patches)


def test_small_bank_change_is_not_counted(scene):
    grid, before, inside = scene
    after = before.copy()
    after[0:20, 50:52] = True                                  # 0.04 ha: pixel noise at the waterline
    stats, _, _ = change.analyse(before, after, inside, grid, established=np.ones_like(before))
    assert stats["bank_erosion_ha"] == 0
