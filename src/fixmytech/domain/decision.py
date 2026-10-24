"""Financial decision engine: repair-and-keep vs replace vs sell-as-is vs repair-and-resell.

Two comparisons, each on a consistent time basis (never mixed):

* **Ownership over a horizon of H months** (objective ``lowest_cost``)
    repair_and_keep  net cost = repair_cost + expected_extra_repairs - resale(repaired phone at H)
    replace          net cost = replacement_price - proceeds(sell current phone as-is now)
                                - resale(replacement phone at H)
* **Cash now** (objective ``max_cash_now``)
    sell_as_is        proceeds = as_is_value x (1 - fee)
    repair_and_resell proceeds = repaired_value x (1 - fee) - repair_cost

with resale(value at H) = value x (1 - annual_depreciation)^(H/12) x (1 - fee).

Every input that is a range is carried through with interval arithmetic (low/mid/high). The
difference between two options is an interval; if it straddles zero, or the mid difference is under
``CLOSE_CALL_FRACTION`` of the larger cost/proceeds, the options are reported as too close to call.
The interval treats inputs as independent, which is conservative (wider than reality) because
as-is and repaired values are correlated.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

CLOSE_CALL_FRACTION = 0.05

Objective = Literal["lowest_cost", "max_cash_now", "keep_device"]
ValueSource = Literal["flipvalue_live", "flipvalue_mock", "customer_provided"]


class Range(BaseModel):
    low: float = Field(ge=0)
    high: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if self.high < self.low:
            raise ValueError("high must be >= low")
        return self

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @classmethod
    def point(cls, v: float) -> Range:
        return cls(low=v, high=v)


class DecisionInputs(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    objective: Objective = "lowest_cost"
    repair_cost: Range | None = None
    repair_cost_source: Literal["reference_estimate", "customer_quote"] | None = None
    as_is_value: Range | None = None
    repaired_value: Range | None = None
    value_source: ValueSource | None = None
    replacement_price: float | None = Field(default=None, ge=0)
    horizon_months: int = Field(default=24, ge=1, le=72)
    transaction_fee_pct: float = Field(default=0.0, ge=0, le=50)
    old_device_annual_depreciation_pct: float = Field(default=20.0, ge=0, le=90)
    new_device_annual_depreciation_pct: float = Field(default=30.0, ge=0, le=90)
    expected_extra_repairs: float = Field(default=0.0, ge=0)


class ScenarioResult(BaseModel):
    id: str
    label: str
    basis: Literal["horizon_net_cost", "cash_now"]
    available: bool
    missing: list[str] = []
    low: float | None = None
    mid: float | None = None
    high: float | None = None
    formula: str
    breakdown: dict[str, float] = {}


class Comparison(BaseModel):
    basis: Literal["horizon_net_cost", "cash_now"]
    options: list[str]
    winner: str | None
    verdict: Literal["clear", "too_close", "uncertain", "incomplete"]
    difference_mid: float | None = None
    difference_low: float | None = None
    difference_high: float | None = None
    explanation: str
    drivers: list[str] = []


class DecisionResult(BaseModel):
    currency: str
    objective: Objective
    scenarios: list[ScenarioResult]
    comparisons: list[Comparison]
    recommendation: str | None
    recommendation_text: str
    assumptions: list[str]
    missing_inputs: list[str]
    caveats: list[str]
    plain_language: list[str] = []
    input_warnings: list[str] = []


def _resale_factor(annual_dep_pct: float, months: int, fee_pct: float) -> float:
    return (1 - annual_dep_pct / 100) ** (months / 12) * (1 - fee_pct / 100)


def _r(v: float) -> float:
    return round(v, 2)


def _repair_keep(i: DecisionInputs) -> ScenarioResult:
    formula = "repair cost + expected extra repairs - resale value of repaired phone after horizon"
    missing = [n for n, v in (("repair cost", i.repair_cost), ("repaired value", i.repaired_value)) if v is None]
    base: dict[str, Any] = {"id": "repair_and_keep", "label": "Repair and keep", "basis": "horizon_net_cost",
                            "formula": formula}
    if missing:
        return ScenarioResult(**base, available=False, missing=missing)
    assert i.repair_cost and i.repaired_value
    f = _resale_factor(i.old_device_annual_depreciation_pct, i.horizon_months, i.transaction_fee_pct)

    def cost(rc: float, rv: float) -> float:
        return rc + i.expected_extra_repairs - rv * f

    return ScenarioResult(
        **base, available=True,
        low=_r(cost(i.repair_cost.low, i.repaired_value.high)),
        mid=_r(cost(i.repair_cost.mid, i.repaired_value.mid)),
        high=_r(cost(i.repair_cost.high, i.repaired_value.low)),
        breakdown={"repair_cost": _r(i.repair_cost.mid), "expected_extra_repairs": _r(i.expected_extra_repairs),
                   "resale_at_horizon": _r(-i.repaired_value.mid * f)},
    )


def _replace(i: DecisionInputs) -> ScenarioResult:
    formula = ("replacement price - proceeds from selling current phone as-is now "
               "- resale value of replacement after horizon")
    missing = [n for n, v in (("replacement price", i.replacement_price), ("as-is value", i.as_is_value)) if v is None]
    base: dict[str, Any] = {"id": "replace", "label": "Sell as-is and replace", "basis": "horizon_net_cost",
                            "formula": formula}
    if missing:
        return ScenarioResult(**base, available=False, missing=missing)
    assert i.replacement_price is not None and i.as_is_value
    fee = 1 - i.transaction_fee_pct / 100
    new_resale = i.replacement_price * _resale_factor(i.new_device_annual_depreciation_pct, i.horizon_months,
                                                      i.transaction_fee_pct)

    def cost(as_is: float) -> float:
        return i.replacement_price - as_is * fee - new_resale  # type: ignore[operator]

    return ScenarioResult(
        **base, available=True,
        low=_r(cost(i.as_is_value.high)), mid=_r(cost(i.as_is_value.mid)), high=_r(cost(i.as_is_value.low)),
        breakdown={"replacement_price": _r(i.replacement_price), "sale_of_current_phone": _r(-i.as_is_value.mid * fee),
                   "resale_of_replacement_at_horizon": _r(-new_resale)},
    )


def _sell_as_is(i: DecisionInputs) -> ScenarioResult:
    base: dict[str, Any] = {"id": "sell_as_is", "label": "Sell as-is now", "basis": "cash_now",
            "formula": "as-is value x (1 - selling fee)"}
    if i.as_is_value is None:
        return ScenarioResult(**base, available=False, missing=["as-is value"])
    fee = 1 - i.transaction_fee_pct / 100
    return ScenarioResult(**base, available=True, low=_r(i.as_is_value.low * fee), mid=_r(i.as_is_value.mid * fee),
                          high=_r(i.as_is_value.high * fee),
                          breakdown={"as_is_value": _r(i.as_is_value.mid),
                                     "selling_fee": _r(-i.as_is_value.mid * i.transaction_fee_pct / 100)})


def _repair_resell(i: DecisionInputs) -> ScenarioResult:
    base: dict[str, Any] = {"id": "repair_and_resell", "label": "Repair, then sell", "basis": "cash_now",
            "formula": "repaired value x (1 - selling fee) - repair cost"}
    missing = [n for n, v in (("repair cost", i.repair_cost), ("repaired value", i.repaired_value)) if v is None]
    if missing:
        return ScenarioResult(**base, available=False, missing=missing)
    assert i.repair_cost and i.repaired_value
    fee = 1 - i.transaction_fee_pct / 100
    return ScenarioResult(
        **base, available=True,
        low=_r(i.repaired_value.low * fee - i.repair_cost.high),
        mid=_r(i.repaired_value.mid * fee - i.repair_cost.mid),
        high=_r(i.repaired_value.high * fee - i.repair_cost.low),
        breakdown={"repaired_value": _r(i.repaired_value.mid),
                   "selling_fee": _r(-i.repaired_value.mid * i.transaction_fee_pct / 100),
                   "repair_cost": _r(-i.repair_cost.mid)},
    )


def _scenarios(i: DecisionInputs) -> dict[str, ScenarioResult]:
    return {s.id: s for s in (_repair_keep(i), _replace(i), _sell_as_is(i), _repair_resell(i))}


def _winner_mid(i: DecisionInputs, a: str, b: str, lower_is_better: bool) -> str | None:
    s = _scenarios(i)
    if not (s[a].available and s[b].available):
        return None
    ma, mb = s[a].mid, s[b].mid
    assert ma is not None and mb is not None
    if lower_is_better:
        return a if ma <= mb else b
    return a if ma >= mb else b


def _drivers(i: DecisionInputs, a: str, b: str, lower_is_better: bool, winner: str) -> list[str]:
    """One-at-a-time sensitivity: which input, moved across its plausible range, flips the winner?"""
    swings: list[tuple[str, list[DecisionInputs]]] = []
    for name, field in (("repair cost", "repair_cost"), ("as-is value", "as_is_value"),
                        ("repaired value", "repaired_value")):
        rng: Range | None = getattr(i, field)
        if rng is not None and rng.high > rng.low:
            swings.append((name, [i.model_copy(update={field: Range.point(rng.low)}),
                                  i.model_copy(update={field: Range.point(rng.high)})]))
    for name, field, delta in (("old phone depreciation", "old_device_annual_depreciation_pct", 10.0),
                               ("new phone depreciation", "new_device_annual_depreciation_pct", 10.0),
                               ("selling fee", "transaction_fee_pct", 5.0)):
        v = getattr(i, field)
        swings.append((f"{name} (+/-{delta:g} points)",
                       [i.model_copy(update={field: max(0.0, v - delta)}),
                        i.model_copy(update={field: min(90.0, v + delta)})]))
    for months in (12, 36):
        if months != i.horizon_months:
            swings.append((f"ownership horizon ({months} months)", [i.model_copy(update={"horizon_months": months})]))
    drivers = []
    for name, variants in swings:
        if any(_winner_mid(v, a, b, lower_is_better) not in (winner, None) for v in variants):
            drivers.append(name)
    return drivers


def _compare(i: DecisionInputs, s: dict[str, ScenarioResult], a: str, b: str,
             basis: Literal["horizon_net_cost", "cash_now"]) -> Comparison:
    lower_is_better = basis == "horizon_net_cost"
    sa, sb = s[a], s[b]
    if not (sa.available and sb.available):
        missing = sorted(set(sa.missing + sb.missing))
        return Comparison(basis=basis, options=[a, b], winner=None, verdict="incomplete",
                          explanation=f"Cannot compare yet. Missing: {', '.join(missing)}.")
    assert sa.mid is not None and sb.mid is not None and sa.low is not None and sb.low is not None
    assert sa.high is not None and sb.high is not None
    diff_mid = sa.mid - sb.mid
    diff_low, diff_high = sa.low - sb.high, sa.high - sb.low
    if lower_is_better:
        winner = a if diff_mid <= 0 else b
    else:
        winner = a if diff_mid >= 0 else b
    loser = b if winner == a else a
    scale = max(abs(sa.mid), abs(sb.mid), 1.0)
    drivers = _drivers(i, a, b, lower_is_better, winner)
    w, lo = s[winner].label, s[loser].label
    amount = f"{abs(diff_mid):,.0f} {i.currency}"
    verdict: Literal["clear", "too_close", "uncertain"]
    if abs(diff_mid) < CLOSE_CALL_FRACTION * scale:
        verdict = "too_close"
        text = (f"'{w}' and '{lo}' differ by about {amount}, which is within the uncertainty of the estimates. "
                "Treat them as financially equivalent and decide on other grounds.")
    elif diff_low <= 0 <= diff_high:
        verdict = "uncertain"
        text = (f"'{w}' comes out ahead by about {amount} using mid-range values, but across the plausible "
                f"ranges '{lo}' could also come out ahead.")
    else:
        verdict = "clear"
        text = f"'{w}' comes out ahead by about {amount} across the whole plausible range of the inputs."
    return Comparison(basis=basis, options=[a, b], winner=winner, verdict=verdict, difference_mid=_r(diff_mid),
                      difference_low=_r(diff_low), difference_high=_r(diff_high), explanation=text, drivers=drivers)


def decide(i: DecisionInputs) -> DecisionResult:
    s = _scenarios(i)
    horizon = _compare(i, s, "repair_and_keep", "replace", "horizon_net_cost")
    cash = _compare(i, s, "repair_and_resell", "sell_as_is", "cash_now")

    assumptions = [
        f"Ownership horizon: {i.horizon_months} months.",
        f"Selling fee: {i.transaction_fee_pct:g}% of sale price (marketplace or platform fees).",
        f"Repaired phone loses {i.old_device_annual_depreciation_pct:g}% of its value per year; "
        f"a replacement loses {i.new_device_annual_depreciation_pct:g}% per year.",
        f"Expected extra repairs on the current phone during the horizon: "
        f"{i.expected_extra_repairs:,.0f} {i.currency}.",
        "The replacement is assumed to give comparable use for the horizon; differences in features, "
        "warranty and remaining software support are not priced in.",
    ]
    if i.repair_cost_source == "reference_estimate":
        assumptions.append("Repair cost uses a reference estimate range, not an actual quote.")
    caveats = []
    if i.value_source == "flipvalue_mock":
        caveats.append("Resale values come from the MOCK FlipValue service and are not real market estimates.")
    elif i.value_source == "customer_provided":
        caveats.append("Resale values were entered by you and have not been checked by a valuation service.")
    caveats.append("This is decision support, not financial advice. Small differences are not guaranteed gains.")

    missing = sorted({m for sc in s.values() for m in sc.missing})

    rec: str | None = None
    chosen = horizon if i.objective in ("lowest_cost", "keep_device") else cash
    if i.objective == "keep_device":
        if s["repair_and_keep"].available:
            rec = "repair_and_keep"
            extra = ""
            if horizon.winner == "replace" and horizon.verdict == "clear":
                extra = (f" Note: replacing would cost about {abs(horizon.difference_mid or 0):,.0f} {i.currency} "
                         "less over the horizon.")
            text = "You prefer to keep this phone, so repairing it is the option that matches your goal." + extra
        else:
            text = "Add the repair cost and repaired value to evaluate keeping your phone."
    elif chosen.verdict == "incomplete":
        text = chosen.explanation
    elif chosen.verdict == "clear":
        rec = chosen.winner
        text = chosen.explanation
    else:
        rec = None
        text = chosen.explanation + (f" The result is most sensitive to: {', '.join(chosen.drivers)}."
                                     if chosen.drivers else "")

    if cash.winner == "repair_and_resell" and cash.verdict != "incomplete":
        caveats.append("Repairing to resell is not a guaranteed gain: the sale price is an estimate, selling "
                       "takes time, and a repair can uncover further faults.")
    return DecisionResult(currency=i.currency, objective=i.objective, scenarios=list(s.values()),
                          comparisons=[horizon, cash], recommendation=rec, recommendation_text=text,
                          assumptions=assumptions, missing_inputs=missing, caveats=caveats,
                          plain_language=_plain_language(i, s), input_warnings=_input_warnings(i))


def _fmt(v: float | None, cur: str) -> str:
    return "unknown" if v is None else f"{v:,.0f} {cur}"


def _plain_language(i: DecisionInputs, s: dict[str, ScenarioResult]) -> list[str]:
    """Explain each available scenario with mid-range values, in words."""
    c, h = i.currency, i.horizon_months
    out: list[str] = []
    keep, repl = s["repair_and_keep"], s["replace"]
    if keep.available:
        b = keep.breakdown
        out.append(f"Repair and keep: you pay about {_fmt(b['repair_cost'], c)} for the repair"
                   + (f" plus {_fmt(b['expected_extra_repairs'], c)} for expected further repairs"
                      if b["expected_extra_repairs"] else "")
                   + f". After {h} months you could sell the phone for about {_fmt(-b['resale_at_horizon'], c)}, "
                   f"so its net cost over the period is about {_fmt(keep.mid, c)}.")
    if repl.available:
        b = repl.breakdown
        out.append(f"Replace: you pay {_fmt(b['replacement_price'], c)} for a new phone, get about "
                   f"{_fmt(-b['sale_of_current_phone'], c)} by selling your current phone now, and could sell the new "
                   f"one for about {_fmt(-b['resale_of_replacement_at_horizon'], c)} after {h} months: "
                   f"a net cost of about {_fmt(repl.mid, c)}.")
    if keep.available and repl.available and keep.mid is not None and repl.mid is not None:
        cheaper = "repairing" if keep.mid < repl.mid else "replacing"
        out.append(f"Over {h} months, {cheaper} is cheaper by about {_fmt(abs(keep.mid - repl.mid), c)} using "
                   "mid-range estimates.")
    sell, rr = s["sell_as_is"], s["repair_and_resell"]
    if sell.available:
        out.append(f"Sell as-is now: you would receive about {_fmt(sell.mid, c)} after selling fees.")
    if rr.available:
        out.append(f"Repair, then sell: about {_fmt(rr.mid, c)} left after paying for the repair and selling fees"
                   + (f", which is {_fmt(abs((rr.mid or 0) - (sell.mid or 0)), c)} "
                      f"{'more' if (rr.mid or 0) > (sell.mid or 0) else 'less'} than selling as-is"
                      if sell.available else "") + ".")
    return out


def _input_warnings(i: DecisionInputs) -> list[str]:
    w: list[str] = []
    if i.as_is_value and i.repaired_value and i.repaired_value.high < i.as_is_value.low:
        w.append("The repaired value is lower than the as-is value. Check the values; a repair rarely "
                 "lowers a phone's price.")
    if i.repair_cost and i.replacement_price is not None and i.repair_cost.low > i.replacement_price:
        w.append("The repair costs more than the replacement phone.")
    if i.replacement_price is not None and i.as_is_value and i.as_is_value.low > i.replacement_price:
        w.append("Your phone's as-is value is higher than the replacement price. Check that the replacement "
                 "is comparable.")
    if i.horizon_months > 48:
        w.append("Long horizons make depreciation and repair-need assumptions much less reliable.")
    return w
