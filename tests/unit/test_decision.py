import pytest

from fixmytech.domain.decision import DecisionInputs, Range, decide


def inputs(**kw):
    base = dict(currency="USD", repair_cost=Range.point(100), repaired_value=Range.point(300),
                as_is_value=Range.point(150), replacement_price=600.0, horizon_months=12,
                transaction_fee_pct=0, old_device_annual_depreciation_pct=20, new_device_annual_depreciation_pct=30)
    base.update(kw)
    return DecisionInputs(**base)


def scen(result, sid):
    return next(s for s in result.scenarios if s.id == sid)


def test_formulas_exact():
    r = decide(inputs())
    # repair & keep: 100 + 0 - 300 * 0.8 = -140 (a net gain: the repaired phone is worth more than the repair)
    assert scen(r, "repair_and_keep").mid == pytest.approx(-140)
    # replace: 600 - 150 - 600 * 0.7 = 30
    assert scen(r, "replace").mid == pytest.approx(30)
    assert scen(r, "sell_as_is").mid == pytest.approx(150)
    assert scen(r, "repair_and_resell").mid == pytest.approx(200)
    assert r.recommendation == "repair_and_keep"


def test_fees_applied_to_every_sale_once():
    r = decide(inputs(transaction_fee_pct=10))
    assert scen(r, "sell_as_is").mid == pytest.approx(135)
    assert scen(r, "repair_and_resell").mid == pytest.approx(300 * 0.9 - 100)
    assert scen(r, "repair_and_keep").mid == pytest.approx(100 - 300 * 0.8 * 0.9)


def test_time_horizons_are_never_mixed():
    r = decide(inputs())
    by_basis = {c.basis: set(c.options) for c in r.comparisons}
    assert by_basis["horizon_net_cost"] == {"repair_and_keep", "replace"}
    assert by_basis["cash_now"] == {"repair_and_resell", "sell_as_is"}


def test_too_close_to_call():
    # repair & keep: 100 - 300*0.8 = -140 ; replace: P - 150 - 0.7P = 0.3P - 150 -> P=33.3 gives -140
    r = decide(inputs(replacement_price=34))
    horizon = r.comparisons[0]
    assert horizon.verdict == "too_close"
    assert r.recommendation is None


def test_uncertain_when_ranges_overlap():
    r = decide(inputs(repair_cost=Range(low=50, high=400), repaired_value=Range(low=150, high=400)))
    horizon = r.comparisons[0]
    assert horizon.verdict in ("uncertain", "too_close")
    assert horizon.difference_low < 0 < horizon.difference_high
    assert "repair cost" in horizon.drivers or "repaired value" in horizon.drivers


def test_incomplete_inputs_are_explained():
    r = decide(DecisionInputs(currency="USD", repair_cost=Range.point(100)))
    assert all(c.verdict == "incomplete" for c in r.comparisons)
    assert r.recommendation is None
    assert "repaired value" in r.missing_inputs and "replacement price" in r.missing_inputs


def test_objective_cash_now():
    r = decide(inputs(objective="max_cash_now"))
    assert r.recommendation == "repair_and_resell"


def test_keep_device_objective_mentions_cost_when_replacing_is_cheaper():
    r = decide(inputs(objective="keep_device", repair_cost=Range.point(500), replacement_price=200,
                      as_is_value=Range.point(150)))
    assert r.recommendation == "repair_and_keep"
    assert "replacing would cost" in r.recommendation_text


def test_mock_values_are_flagged():
    r = decide(inputs(value_source="flipvalue_mock"))
    assert any("MOCK" in c for c in r.caveats)


def test_range_validation():
    with pytest.raises(ValueError):
        Range(low=10, high=5)


def test_plain_language_explains_each_scenario():
    r = decide(inputs())
    text = " ".join(r.plain_language)
    assert "Repair and keep" in text and "Replace:" in text and "Sell as-is now" in text and "Repair, then sell" in text
    assert "cheaper by about" in text


def test_input_warnings_and_no_profit_promise():
    r = decide(inputs(repaired_value=Range.point(100), as_is_value=Range.point(150)))
    assert any("repaired value is lower" in w for w in r.input_warnings)
    r = decide(inputs(objective="max_cash_now"))
    assert r.recommendation == "repair_and_resell"
    assert any("not a guaranteed gain" in c for c in r.caveats)
