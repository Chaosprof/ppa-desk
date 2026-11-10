import numpy as np
import pandas as pd
import pytest

from ppa import forwards, sources

ROW = """<tr class="odd"><td width="20%"><strong><span class="chart-popup"><div class="popup"></div></span>
FDB {name}</strong></td>
<td class="divider"> </td><td width="">n.a.</td><td width="">n.a.</td><td width="">0</td><td class="divider"> </td>
<td width="">n.a.</td><td width="">n.a.</td><td width="">n.a.</td><td class="divider"> </td><td width="">0</td>
<td width="">0</td><td width="">0</td><td class="divider"> </td><td width="">{d}</td><td width="">{d1}</td></tr>"""


def test_omip_page_is_parsed_into_dashboard_contract_names():
    page = "".join(ROW.format(name=n, d=d, d1=d1) for n, d, d1 in [
        ("M Nov-25", "99.96", "99.77"), ("Q1-26", "96.95", "97.65"), ("YR-27", "84.27", "84.60"),
        ("D Fr31Oct-25", "93.24", "91.83"), ("YR-33", "n.a.", "70.00"),
    ])
    assert sources.parse_omip(page) == {"Nov-25": 99.96, "Q1-26": 96.95, "Cal-27": 84.27}


@pytest.fixture(scope="module")
def history():
    """One-factor curve: every calendar contract moves with the same daily shock, 30% a year."""
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2019-01-01", "2023-12-29")
    level = np.exp(np.cumsum(0.30 / np.sqrt(252) * rng.standard_normal(len(dates))))
    rows = []
    for year in range(2020, 2027):
        live = dates < pd.Timestamp(year, 1, 1)
        rows += [{"date": d, "contract": f"Cal-{year % 100}", "price": 50 * x}
                 for d, x in zip(dates[live], level[live], strict=True)]
    frame = pd.DataFrame(rows)
    holiday = frame[frame["date"] == "2019-05-24"].assign(date=pd.Timestamp("2019-05-25"))  # Friday re-published
    return pd.concat([frame, holiday], ignore_index=True)


def test_republished_days_are_dropped(history):
    assert pd.Timestamp("2019-05-25") not in set(forwards.clean(history)["date"])


def test_realised_vol_recovers_the_true_vol(history):
    rets = forwards.returns(history)
    assert forwards.vol(rets["ret"]) == pytest.approx(0.30, rel=0.1)
    assert all(b["vol"] == pytest.approx(0.30, rel=0.15) for b in forwards.term_structure(rets))


def test_one_factor_curve_has_perfect_correlation(history):
    rets = forwards.returns(history)
    assert forwards.curve_correlation(rets, pd.Timestamp("2023-12-29"), 24) == pytest.approx(1.0, abs=1e-9)


def test_next_calendar_rolls_each_january(history):
    cal = forwards.next_calendar(history)
    price = history.set_index(["date", "contract"])["price"]
    assert cal.loc["2021-06-01"] == price[(pd.Timestamp("2021-06-01"), "Cal-22")]
    assert cal.loc["2022-01-03"] == price[(pd.Timestamp("2022-01-03"), "Cal-23")]


def test_vol_from_monthly_averages_recovers_the_daily_vol():
    """Averaging a month of prices removes a third of the variance of its change; the estimate puts it back."""
    rng = np.random.default_rng(4)
    dates = pd.bdate_range("1990-01-01", "2025-12-31")
    price = pd.Series(30 * np.exp(np.cumsum(0.5 / np.sqrt(252) * rng.standard_normal(len(dates)))), index=dates)
    monthly = price.groupby(price.index.to_period("M")).mean()
    assert forwards.averaged_vol(monthly) == pytest.approx(0.5, rel=0.08)
    assert np.log(monthly).diff().std() * np.sqrt(12) < 0.45  # uncorrected, it reads low


def test_spark_inputs_measure_vols_and_correlations_over_the_window():
    """Front-month power, gas and carbon driven by one shock: the vols come back and the correlations are high."""
    rng = np.random.default_rng(6)
    dates = pd.bdate_range("2022-01-03", "2025-12-31")
    shock = rng.standard_normal(len(dates))
    walk = lambda vol, own: np.exp(np.cumsum(vol / np.sqrt(252) * (0.9 * shock + np.sqrt(1 - 0.81) * own)))  # noqa: E731
    power, gas, carbon = (walk(v, rng.standard_normal(len(dates))) for v in (0.4, 0.6, 0.3))
    rows = []
    for d, p in zip(dates, power, strict=True):
        ahead = d + pd.offsets.MonthBegin(1)
        rows.append({"date": d, "contract": f"{ahead:%b-%y}", "price": 100 * p})
        rows.append({"date": d, "contract": f"Cal-{(d.year + 1) % 100:02d}", "price": 90 * p})
    history = pd.DataFrame(rows)
    eua = pd.Series(70 * carbon, index=dates)
    gas_monthly = pd.Series(30 * gas, index=dates).resample("MS").mean()
    s = forwards.spark_inputs(history, eua, gas_monthly, dates[-1].date(), months=36)
    assert s["vol"]["power"] == pytest.approx(0.4, rel=0.1) and s["vol"]["carbon"] == pytest.approx(0.3, rel=0.1)
    assert s["vol"]["gas"] == pytest.approx(0.6, rel=0.3)  # from 36 monthly averages
    assert all(0.5 < c < 1 for c in s["corr"].values())
