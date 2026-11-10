"""Pay-as-produced PPA: fair price, its decomposition, the exchange hedge and the P&L distribution.

The desk buys the plant's output at a fixed price K and sells calendar baseload futures against it.
Per delivery year y and path s (discount factor D_y):

    deal   = revenue + (cannibalisation - balancing) * volume - K * volume
    hedge  = -q_y * (spot_y - hours_y * base_y)              q_y in MW, minimum variance
    P&L    = sum_y D_y * (deal + hedge)

Cannibalisation is a change in the capture rate, a share of the price, so in EUR/MWh it moves with the
level shock, and the hedge shrinks as the capture rate falls. Two errors sit on it, both out of the hedge's
reach. Its slope is uncertain, with one error per path shared by every year: the one risk that does not
diversify over the tenor. And each year lands away from the model for reasons penetration does not explain
(weather, fuel, rules), by the size the model has missed a year ahead in history.

K is first set so E[P&L] = 0, then lowered by the risk premium: K is solved so the hedged deal breaks
even at the P90 outcome (10th percentile of lifetime P&L). What remains after the hedge is shape and
volume risk together.

The model price is then turned into a bid by the terms a desk adds: what executing and funding the hedge
costs, the expected loss if the generator defaults, and the guarantees of origin that come with the power.

The headline deal pays for every MWh produced. The negative-price clause is a second structure: only
output in hours at or above zero is paid, so its price is per paid MWh and is not a step of the first.
Its paid volume shrinks with the build-out, and its cannibalisation is that of the paid hours alone:
the part of the capture-rate drop that comes from more negative hours is in the volume, not the price.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from .simulate import YearSim


def discount_factors(rates: pd.Series, tau: np.ndarray) -> np.ndarray:
    """ECB spot yields are continuously compounded; interpolate linearly in maturity."""
    r = np.interp(tau, rates.index.to_numpy(float), rates.to_numpy())
    return np.exp(-r * tau)


def cannibalisation_adj(sims: list[YearSim], delta_rate: pd.DataFrame, paid: bool = False) -> np.ndarray:
    """EUR/MWh per year: capture-rate change x monthly base, weighted by expected monthly volume
    (`paid`: the volume in hours at or above zero)."""
    out = []
    for s in sims:
        d = delta_rate[s.year].reindex(range(1, 13)).to_numpy()
        volume = s.monthly_volume_pos if paid else s.monthly_volume
        out.append(float((d * s.monthly_base * volume).sum() / volume.sum()))
    return np.array(out)


def paid_volume(sims: list[YearSim], factor: pd.DataFrame) -> np.ndarray:
    """Per year: paid volume relative to the simulated one, from the monthly `factor` weighted by paid volume."""
    out = []
    for s in sims:
        f = factor[s.year].reindex(range(1, 13)).to_numpy()
        out.append(float((f * s.monthly_volume_pos).sum() / s.monthly_volume_pos.sum()))
    return np.array(out)


def year_effects(sims: list[YearSim], rho: float) -> np.ndarray:
    """(paths, years) with unit variance: each year keeps `rho` of the year before and adds its own noise."""
    e = np.column_stack([s.capture_noise for s in sims])
    for j in range(1, e.shape[1]):
        e[:, j] = rho * e[:, j - 1] + np.sqrt(1 - rho**2) * e[:, j]
    return e


def cannibalisation_paths(sims: list[YearSim], adj: np.ndarray, adj_sd: np.ndarray,
                          year_sd: np.ndarray | float = 0.0, rho: float = 0.0) -> np.ndarray:
    """EUR/MWh by path and year: the expected change `adj`, one slope standard error `adj_sd` shared by every
    year, and a year's own surprise `year_sd` that carries over with correlation `rho`; all scaled by the
    level shock."""
    level = np.column_stack([s.level for s in sims])
    return (adj + adj_sd * sims[0].slope_error[:, None] + year_sd * year_effects(sims, rho)) * level


def hedge_cost(sims: list[YearSim], valuation, vol, terms: dict) -> np.ndarray:
    """EUR per MWh of baseload sold, by delivery year: half the bid-offer spread plus funding the initial margin.

    Calendar years up to `liquid_years` ahead trade at the liquid spread, the rest at the illiquid one. Initial
    margin is the move a clearing house covers: a normal quantile over its holding period, at the vol of a
    contract that far from delivery. It is posted from today to mid-delivery and funded at a spread.
    """
    m = terms["margin"]
    cover = norm.ppf(m["confidence"]) * np.sqrt(m["holding_days"] / 252)
    out = []
    for s in sims:
        liquid = s.year - valuation.year <= terms["liquid_years"]
        start = (valuation.replace(year=s.year, month=1, day=1) - valuation).days / 365.25
        t = np.arange(0.0, s.tau, 1 / 12)
        vol_years = float(np.sum(vol(np.maximum(start - t, 0.0)) * np.clip(s.tau - t, 0.0, 1 / 12)))
        out.append(terms["bid_offer"]["liquid" if liquid else "illiquid"]
                   + m["funding_spread"] * cover * vol_years * s.base)
    return np.array(out)


@dataclass
class Deal:
    volume: np.ndarray  # (paths, years)
    cash: np.ndarray  # spot value received incl. cannibalisation and balancing, EUR
    spot: np.ndarray  # sum of hourly prices: EUR per MW of baseload
    spot_fwd: np.ndarray  # (years,) hours * base
    df: np.ndarray  # (years,)

    def expected_price(self) -> float:
        return float((self.df * self.cash.mean(0)).sum() / (self.df * self.volume.mean(0)).sum())

    def hedge(self, k: float) -> np.ndarray:
        """Minimum-variance baseload MW per year for the deal struck at k."""
        x = self.spot - self.spot.mean(0)
        pi = self.cash - k * self.volume
        pi = pi - pi.mean(0)
        return (pi * x).mean(0) / (x * x).mean(0)

    def pnl(self, k: float, q: np.ndarray | None) -> np.ndarray:
        flows = self.cash - k * self.volume
        if q is not None:  # futures settle against the simulated mean, so the hedge adds no sample bias
            flows = flows - q * (self.spot - self.spot.mean(0))
        return flows @ self.df


def _deal(sims: list[YearSim], df: np.ndarray, cann: np.ndarray, balancing: float, clause: bool,
          risk: str = "all", paid: np.ndarray | float = 1.0) -> Deal:
    """risk="shape": expected generation against simulated prices; "volume": simulated generation
    against the forward curve and the expected cannibalisation (so the hedge has nothing to do);
    "all": both. Under the clause, `paid` scales each year's paid volume and its revenue."""
    spot_fwd = np.array([s.hours * s.base for s in sims])
    spot = np.column_stack([s.spot for s in sims])
    if risk == "shape":
        vol = np.column_stack([s.volume_det_pos if clause else np.full_like(s.revenue, s.volume.mean()) for s in sims])
        rev = np.column_stack([s.revenue_det_pos if clause else s.revenue_det for s in sims])
    elif risk == "volume":
        vol = np.column_stack([s.volume_fwd_pos if clause else s.volume for s in sims])
        rev = np.column_stack([s.revenue_fwd_pos if clause else s.revenue_fwd for s in sims])
        spot = np.broadcast_to(spot_fwd, spot.shape)
        cann = np.broadcast_to(cann.mean(0), cann.shape)
    else:
        vol = np.column_stack([s.volume_pos if clause else s.volume for s in sims])
        rev = np.column_stack([s.revenue_pos if clause else s.revenue for s in sims])
    if clause:
        vol, rev = vol * paid, rev * paid
    cash = rev + (cann - balancing) * vol
    return Deal(vol, cash, spot, spot_fwd, df)


def credit_loss(deal: Deal, k: float, level: np.ndarray, hazard: float, recovery: float) -> float:
    """Expected loss in EUR if the generator defaults: default probability x loss rate x what the years left
    are worth to the desk when they are worth something.

    The hedge is on the exchange and offsets none of it, so the exposure is the PPA alone. At the start of
    each year, the value of the years left is their expected discounted P&L given the price level of the year
    before, by regression on its log; before the first year it is the expected lifetime P&L.
    """
    flows = (deal.cash - k * deal.volume) * deal.df
    left = flows[:, ::-1].cumsum(axis=1)[:, ::-1]  # year j and every year after it
    loss = 0.0
    for j in range(flows.shape[1]):
        worth = np.full(len(left), left[:, 0].mean())
        if j:
            x = np.log(level[:, j - 1])
            basis = np.vander((x - x.mean()) / (x.std() + 1e-12), 3)
            worth = basis @ np.linalg.lstsq(basis, left[:, j], rcond=None)[0]
        loss += hazard * np.exp(-hazard * j) * np.maximum(worth, 0.0).mean()
    return float((1 - recovery) * loss)


def _terms(deal: Deal, k: float, q: np.ndarray, hours: np.ndarray, level: np.ndarray,
           cost: np.ndarray | None, credit: dict | None) -> dict:
    """EUR per MWh bought: executing and funding the hedge, and the expected loss on the generator's default."""
    volume = float((deal.df * deal.volume.mean(0)).sum())
    hedging = 0.0 if cost is None else float((deal.df * np.abs(q) * hours * cost).sum()) / volume
    loss = 0.0 if credit is None else credit_loss(deal, k, level, **credit) / volume
    return {"hedging": hedging, "credit": loss}


def _premium(deal: Deal, k: float, q: np.ndarray, quantile: float) -> float:
    """Price cut that makes the `quantile` of hedged lifetime P&L exactly zero."""
    at = lambda x: float(np.quantile(deal.pnl(k - x, q), quantile))  # noqa: E731, increasing in x
    if at(0.0) >= 0:
        return 0.0
    return float(brentq(at, 0.0, k + 100.0, xtol=1e-6))


def _histogram(series: dict[str, np.ndarray], bins: int = 36) -> dict:
    allv = np.concatenate(list(series.values()))
    lo, hi = np.quantile(allv, [0.005, 0.995])
    edges = np.linspace(lo, hi, bins + 1)
    out = {"edges": edges.round(3).tolist()}
    for name, v in series.items():
        out[name] = np.histogram(v, edges)[0].tolist()  # the outer 1% falls outside the range
    return out


def value(sims: list[YearSim], df: np.ndarray, adj: np.ndarray, adj_sd: np.ndarray, balancing: float,
          quantile: float, paid_adj: np.ndarray | None = None, paid_adj_sd: np.ndarray | None = None,
          paid: np.ndarray | None = None, *, year_sd: np.ndarray | float = 0.0,
          paid_year_sd: np.ndarray | float | None = None, rho: float = 0.0, cost: np.ndarray | None = None,
          credit: dict | None = None, origin: float = 0.0, confidence: tuple = (0.25, 0.10, 0.05)) -> dict:
    """Everything the desk needs for one technology, site and tenor (= len(sims)).

    `adj` and `adj_sd` are the cannibalisation and its slope error over all hours; the `paid_` pair is the
    same over hours at or above zero, and `paid` each year's paid volume relative to the simulated one.
    Left out, the clause takes the all-hours cannibalisation on today's paid volume. `year_sd` and `rho` are
    the year-to-year capture surprise. `cost` (per MWh hedged, by year), `credit` (hazard and recovery) and
    `origin` (guarantees of origin, per MWh) turn the model price into a bid; `confidence` lists the P&L
    quantiles at which the premium is also solved.
    """
    paid_adj = adj if paid_adj is None else paid_adj
    paid_adj_sd = adj_sd if paid_adj_sd is None else paid_adj_sd
    paid_year_sd = year_sd if paid_year_sd is None else paid_year_sd
    paid = np.ones(len(sims)) if paid is None else paid
    level = np.column_stack([s.level for s in sims])
    hours = np.array([s.hours for s in sims])
    volume = np.column_stack([s.volume for s in sims])
    ev = df * volume.mean(0)
    w = ev / ev.sum()
    base = float(w @ np.array([s.base for s in sims]))
    profile = float((df * np.array([s.profile_value for s in sims])).sum() / ev.sum()) - base
    correlation = float((df * np.array([s.revenue.mean() for s in sims])).sum() / ev.sum()) - base - profile
    cann = cannibalisation_paths(sims, adj, adj_sd, year_sd, rho)
    cannibalisation = float((df * (cann * volume).mean(0)).sum() / ev.sum())

    deal = _deal(sims, df, cann, balancing, clause=False)
    k0 = deal.expected_price()
    q = deal.hedge(k0)
    premium = _premium(deal, k0, q, quantile)
    fair = k0 - premium
    terms = _terms(deal, fair, q, hours, level, cost, credit)
    steps = {
        "base": round(base, 2),
        "profile": round(profile, 2),
        "correlation": round(correlation, 2),
        "cannibalisation": round(cannibalisation, 2),
        "risk": round(-premium, 2),
        "balancing": round(-balancing, 2),
        "hedging": round(-terms["hedging"], 2),
        "credit": round(-terms["credit"], 2),
        "origin": round(origin, 2),
    }
    price = round(sum(steps.values()), 2)  # the steps as shown add up to the price as shown
    ladder = [{"confidence": round(100 * (1 - p)), "premium": round(_premium(deal, k0, q, p), 2)} for p in confidence]
    for rung in ladder:
        rung["price"] = round(price - steps["risk"] - rung["premium"], 2)

    pnl_hedged = deal.pnl(fair, q)
    pnl_open = deal.pnl(fair, None)
    # Each part on its own. Expected generation earns the profile's price, not the correlation-reduced one,
    # so it carries more of the level and needs its own hedge; under the deal's it would be left part open.
    # The two capture errors are orthogonal to the level, so no hedge touches them.
    shape = _deal(sims, df, adj * level, balancing, clause=False, risk="shape")
    sd_shape = float(shape.pnl(fair, shape.hedge(fair)).std())
    sd_volume = float(_deal(sims, df, cann, balancing, clause=False, risk="volume").pnl(fair, q).std())
    sd_slope = float(((adj_sd * sims[0].slope_error[:, None] * level * volume) @ df).std())
    sd_year = float(((year_sd * year_effects(sims, rho) * level * volume) @ df).std())

    # Negative-price clause: the same plant, paid only in hours at or above zero.
    clause = _deal(sims, df, cannibalisation_paths(sims, paid_adj, paid_adj_sd, paid_year_sd, rho), balancing,
                   clause=True, paid=paid)
    k1 = clause.expected_price()
    q1 = clause.hedge(k1)
    fair_paid = k1 - _premium(clause, k1, q1, quantile)
    paid_terms = _terms(clause, fair_paid, q1, hours, level, cost, credit)
    price_paid = fair_paid - paid_terms["hedging"] - paid_terms["credit"] + origin
    unpaid_then = volume.mean(0) - clause.volume.mean(0)
    unpaid_now = volume.mean(0) - np.array([s.volume_pos.mean() for s in sims])
    more = np.divide(unpaid_then, unpaid_now, out=np.ones(len(sims)), where=unpaid_now > 0)  # puts grow with it

    # Baseload-shaped alternative: the generator sells P50-equivalent MW of baseload at the forward
    # and keeps shape and volume risk on every MWh produced.
    q_flat = deal.volume.mean(0) / hours
    flat = (deal.cash + q_flat * (deal.spot_fwd - deal.spot)) @ df / (deal.volume @ df)
    hours_w = df * hours
    put = float((df * more * np.array([s.put.mean() for s in sims])).sum() / ev.sum())
    put_normal = float((df * more * np.array([s.put_normal for s in sims])).sum() / ev.sum())

    m = 1e6
    return {
        "price": price,
        "steps": steps,
        "premiums": ladder,
        "hedge": [{"year": s.year, "mw": round(float(x), 2), "p50_mw": round(float(s.volume.mean() / s.hours), 2)}
                  for s, x in zip(sims, q, strict=True)],
        "pnl": _histogram({"hedged": pnl_hedged / m}),
        "risk": {
            "sd_open": round(float(pnl_open.std()) / m, 3),
            "sd_hedged": round(float(pnl_hedged.std()) / m, 3),
            "sd_shape": round(sd_shape / m, 3),  # each part alone; the rest is price and volume moving together
            "sd_slope": round(sd_slope / m, 3),
            "sd_year": round(sd_year / m, 3),
            "sd_volume": round(sd_volume / m, 3),
            "p10_hedged": round(float(np.quantile(pnl_hedged, 0.10)) / m, 3),
            "es95_hedged": round(float(pnl_hedged[pnl_hedged <= np.quantile(pnl_hedged, 0.05)].mean()) / m, 3),
            "volume_gwh": round(float((deal.volume @ df).mean()) / 1e3, 1),
        },
        "baseload": {
            "price": round(float(hours_w @ np.array([s.base for s in sims]) / hours_w.sum()), 2),
            "p10": round(float(np.quantile(flat, 0.10)), 2),
            "p50": round(float(np.quantile(flat, 0.50)), 2),
            "p90": round(float(np.quantile(flat, 0.90)), 2),
        },
        "negative": {
            "price": round(price_paid, 2),  # per MWh paid, on 1 - volume_share of the output
            "put_mc": round(put, 3),
            "put_normal": round(put_normal, 3),
            "volume_share": round(float(1 - (df @ clause.volume.mean(0)) / ev.sum()), 4),
            "volume_share_last": round(float(unpaid_then[-1] / volume.mean(0)[-1]), 4),
        },
    }
