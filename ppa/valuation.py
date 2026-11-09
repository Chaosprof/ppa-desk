"""Pay-as-produced PPA: fair price, its decomposition, the exchange hedge and the P&L distribution.

The desk buys the plant's output at a fixed price K and sells calendar baseload futures against it.
Per delivery year y and path s (discount factor D_y):

    deal   = revenue + (cannibalisation - balancing) * volume - K * volume
    hedge  = -q_y * (spot_y - hours_y * base_y)              q_y in MW, minimum variance
    P&L    = sum_y D_y * (deal + hedge)

Cannibalisation is a change in the capture rate, a share of the price, so in EUR/MWh it moves with the
level shock, and the hedge shrinks as the capture rate falls. Its slope is uncertain, with one error per
path shared by every year: the one risk that does not diversify over the tenor.

K is first set so E[P&L] = 0, then lowered by the risk premium: K is solved so the hedged deal breaks
even at the P90 outcome (10th percentile of lifetime P&L). What remains after the hedge is shape and
volume risk together.

The headline deal pays for every MWh produced. The negative-price clause is a second structure: only
output in hours at or above zero is paid, so its price is per paid MWh and is not a step of the first.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .simulate import YearSim


def discount_factors(rates: pd.Series, tau: np.ndarray) -> np.ndarray:
    """ECB spot yields are continuously compounded; interpolate linearly in maturity."""
    r = np.interp(tau, rates.index.to_numpy(float), rates.to_numpy())
    return np.exp(-r * tau)


def cannibalisation_adj(sims: list[YearSim], delta_rate: pd.DataFrame) -> np.ndarray:
    """EUR/MWh per year: capture-rate change x monthly base, weighted by expected monthly volume."""
    out = []
    for s in sims:
        d = delta_rate[s.year].reindex(range(1, 13)).to_numpy()
        out.append(float((d * s.monthly_base * s.monthly_volume).sum() / s.monthly_volume.sum()))
    return np.array(out)


def cannibalisation_paths(sims: list[YearSim], adj: np.ndarray, adj_sd: np.ndarray) -> np.ndarray:
    """EUR/MWh by path and year: the expected change `adj` and one slope standard error `adj_sd`, both
    scaled by the level shock."""
    level = np.column_stack([s.level for s in sims])
    return (adj + adj_sd * sims[0].slope_error[:, None]) * level


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
          risk: str = "all") -> Deal:
    """risk="shape": expected generation against simulated prices; "volume": simulated generation
    against the forward curve and the expected cannibalisation (so the hedge has nothing to do);
    "all": both."""
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
    cash = rev + (cann - balancing) * vol
    return Deal(vol, cash, spot, spot_fwd, df)


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
          quantile: float) -> dict:
    """Everything the desk needs for one technology, site and tenor (= len(sims))."""
    volume = np.column_stack([s.volume for s in sims])
    ev = df * volume.mean(0)
    w = ev / ev.sum()
    base = float(w @ np.array([s.base for s in sims]))
    profile = float((df * np.array([s.profile_value for s in sims])).sum() / ev.sum()) - base
    correlation = float((df * np.array([s.revenue.mean() for s in sims])).sum() / ev.sum()) - base - profile
    cann = cannibalisation_paths(sims, adj, adj_sd)
    cannibalisation = float((df * (cann * volume).mean(0)).sum() / ev.sum())

    deal = _deal(sims, df, cann, balancing, clause=False)
    k0 = deal.expected_price()
    q = deal.hedge(k0)
    premium = _premium(deal, k0, q, quantile)
    fair = k0 - premium

    pnl_hedged = deal.pnl(fair, q)
    pnl_open = deal.pnl(fair, None)
    sd_shape = float(_deal(sims, df, cann, balancing, clause=False, risk="shape").pnl(fair, q).std())
    sd_volume = float(_deal(sims, df, cann, balancing, clause=False, risk="volume").pnl(fair, q).std())

    # Negative-price clause: the same plant, paid only in hours at or above zero.
    paid = _deal(sims, df, cann, balancing, clause=True)
    k1 = paid.expected_price()
    fair_paid = k1 - _premium(paid, k1, paid.hedge(k1), quantile)

    # Baseload-shaped alternative: the generator sells P50-equivalent MW of baseload at the forward
    # and keeps shape and volume risk on every MWh produced.
    q_flat = deal.volume.mean(0) / np.array([s.hours for s in sims])
    flat = (deal.cash + q_flat * (deal.spot_fwd - deal.spot)) @ df / (deal.volume @ df)
    hours_w = df * np.array([s.hours for s in sims])
    put = float((df * np.array([s.put.mean() for s in sims])).sum() / ev.sum())
    put_normal = float((df * np.array([s.put_normal for s in sims])).sum() / ev.sum())

    m = 1e6
    return {
        "price": round(fair, 2),
        "steps": {
            "base": round(base, 2),
            "profile": round(profile, 2),
            "correlation": round(correlation, 2),
            "cannibalisation": round(cannibalisation, 2),
            "risk": round(-premium, 2),
            "balancing": round(-balancing, 2),
        },
        "hedge": [{"year": s.year, "mw": round(float(x), 2), "p50_mw": round(float(s.volume.mean() / s.hours), 2)}
                  for s, x in zip(sims, q, strict=True)],
        "pnl": _histogram({"hedged": pnl_hedged / m}),
        "risk": {
            "sd_open": round(float(pnl_open.std()) / m, 3),
            "sd_hedged": round(float(pnl_hedged.std()) / m, 3),
            "sd_shape": round(sd_shape / m, 3),  # standalone; shape and volume co-move, so no sum
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
            "price": round(fair_paid, 2),  # per MWh paid, on 1 - volume_share of the output
            "put_mc": round(put, 3),
            "put_normal": round(put_normal, 3),
            "volume_share": round(float(1 - (df @ paid.volume.mean(0)) / ev.sum()), 4),
            "hours": round(float(np.mean([s.negative_hours.mean() for s in sims])), 1),
        },
    }
