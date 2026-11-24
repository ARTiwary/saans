"""The deterministic exposure/impact model: numbers must be sane, monotonic and validated."""
import pytest

from saans.impact import compute_impact

BASE = {"pm25": 150, "best_pm25": 90, "outdoor_hours": 2, "commute_km": 8, "mode": "two_wheeler"}


def impact(**over):
    d = dict(BASE)
    d.update(over)
    return compute_impact(d)


def test_no_scenario_means_no_change():
    r = impact()
    assert r["baseline_pm25"] == r["scenario_pm25"] and r["reduction_pct"] == 0


def test_every_protective_choice_lowers_exposure():
    base = impact()["baseline_pm25"]
    for sc in ({"windows_closed": True}, {"purifier": True}, {"mask": True}, {"shift_outdoor": True}):
        assert impact(scenario=sc)["scenario_pm25"] < base, sc


def test_choices_stack():
    one = impact(scenario={"windows_closed": True})["reduction_pct"]
    two = impact(scenario={"windows_closed": True, "purifier": True})["reduction_pct"]
    three = impact(scenario={"windows_closed": True, "purifier": True, "mask": True})["reduction_pct"]
    assert one < two < three <= 100


def test_exposure_scales_with_ambient_pm25():
    assert impact(pm25=300, best_pm25=180)["baseline_pm25"] > impact(pm25=60, best_pm25=36)["baseline_pm25"]


def test_cigarette_equivalence_uses_22_rule():
    r = impact()
    assert r["cigs_baseline"] == pytest.approx(r["baseline_pm25"] / 22, abs=0.01)


def test_switching_to_clean_transport_saves_co2():
    r = impact(mode="car", scenario={"new_mode": "metro"})
    assert r["co2_saved_kg_year"] > 0
    assert impact(mode="walk")["co2_saved_kg_year"] == 0


def test_work_from_home_has_no_commute_or_co2():
    r = impact(mode="wfh")
    assert r["co2_saved_kg_year"] == 0
    assert not any("Commute by" in lv["label"] for lv in r["levers"])


def test_levers_sorted_and_capped():
    levers = impact()["levers"]
    pcts = [lv["reduction_pct"] for lv in levers]
    assert pcts == sorted(pcts, reverse=True) and len(levers) <= 5


def test_already_protected_households_do_not_get_redundant_levers():
    labels = " ".join(lv["label"] for lv in impact(baseline_windows="closed", baseline_purifier=True)["levers"])
    assert "windows shut" not in labels and "purifier" not in labels


def test_unknown_mode_falls_back_safely():
    assert impact(mode="teleporter")["baseline_pm25"] > 0


@pytest.mark.parametrize("field", ["pm25", "outdoor_hours", "commute_km"])
def test_non_numeric_input_is_rejected(field):
    with pytest.raises(ValueError):
        impact(**{field: "lots"})


def test_extreme_values_are_clamped():
    r = impact(pm25=10 ** 9, outdoor_hours=500, commute_km=10 ** 6)
    assert r["baseline_pm25"] <= 1000


def test_assumptions_are_disclosed():
    assert len(impact()["assumptions"]) >= 4
