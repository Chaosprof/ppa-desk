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
