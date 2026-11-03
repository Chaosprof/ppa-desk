"""Option pricing: Bachelier puts, Kirk's spread approximation, Monte Carlo and LSMC."""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def bachelier_put(forward, strike, sd):
    """Undiscounted normal-model put: E[(K - X)^+] for X ~ N(forward, sd^2)."""
    sd = np.maximum(sd, 1e-12)
    d = (strike - forward) / sd
    return (strike - forward) * norm.cdf(d) + sd * norm.pdf(d)


def black_call(f, k, sd):
    """Undiscounted Black call with total log-vol sd = sigma * sqrt(T)."""
    d1 = (np.log(f / k) + 0.5 * sd**2) / sd
    return f * norm.cdf(d1) - k * norm.cdf(d1 - sd)


def kirk(f1, f2, k, s1, s2, rho, t):
    """Kirk (1995): call on F1 - F2 - K with F2 + K treated as lognormal. Undiscounted."""
    w = f2 / (f2 + k)
    sd = np.sqrt(s1**2 - 2 * rho * s1 * s2 * w + (s2 * w) ** 2) * np.sqrt(t)
    return black_call(f1, f2 + k, sd)


def fuel_basket(gas, carbon, hr, ef, s_g, s_c, rho_gc, t):
    """Moment-match HR*G + EF*C to one lognormal: forward and vol."""
    a, b = hr * gas, ef * carbon
    m1 = a + b
    m2 = a**2 * np.exp(s_g**2 * t) + b**2 * np.exp(s_c**2 * t) + 2 * a * b * np.exp(rho_gc * s_g * s_c * t)
    return m1, np.sqrt(np.log(m2 / m1**2) / t)


def basket_corr(power, gas, carbon, hr, ef, s_p, s_g, s_c, rho_pg, rho_pc, rho_gc, t):
    """Log-correlation between power and the moment-matched fuel basket."""
    a, b = hr * gas, ef * carbon
    m1, s_f = fuel_basket(gas, carbon, hr, ef, s_g, s_c, rho_gc, t)
    cross = (a * np.exp(rho_pg * s_p * s_g * t) + b * np.exp(rho_pc * s_p * s_c * t)) / m1
    return float(np.clip(np.log(cross) / (s_p * s_f * t), -1, 1))


def spark_kirk(s: dict, t: float) -> float:
    v, c = s["vol"], s["corr"]
    fuel, s_f = fuel_basket(s["gas"], s["carbon"], s["heat_rate"], s["emission_factor"],
                            v["gas"], v["carbon"], c["gas_carbon"], t)
    rho = basket_corr(s["power"], s["gas"], s["carbon"], s["heat_rate"], s["emission_factor"],
                      v["power"], v["gas"], v["carbon"], c["power_gas"], c["power_carbon"], c["gas_carbon"], t)
    return float(kirk(s["power"], fuel, s["vom"], v["power"], s_f, rho, t))


def spark_mc(s: dict, t: float, n: int = 400_000, seed: int = 1) -> tuple[float, float]:
    """Three correlated lognormal forwards, antithetic draws. Returns (value, standard error)."""
    v, c = s["vol"], s["corr"]
    corr = np.array([[1, c["power_gas"], c["power_carbon"]],
                     [c["power_gas"], 1, c["gas_carbon"]],
                     [c["power_carbon"], c["gas_carbon"], 1]])
    vol = np.array([v["power"], v["gas"], v["carbon"]])
    fwd = np.array([s["power"], s["gas"], s["carbon"]])
    eps = np.random.default_rng(seed).standard_normal((n // 2, 3)) @ np.linalg.cholesky(corr).T
    eps = np.vstack([eps, -eps])
    x = fwd * np.exp(vol * np.sqrt(t) * eps - 0.5 * vol**2 * t)
    payoff = np.maximum(x[:, 0] - s["heat_rate"] * x[:, 1] - s["emission_factor"] * x[:, 2] - s["vom"], 0)
    pairs = 0.5 * (payoff[: n // 2] + payoff[n // 2 :])
    return float(payoff.mean()), float(pairs.std(ddof=1) / np.sqrt(len(pairs)))


def swing_lsmc(spreads: np.ndarray, rights: int, degree: int = 3) -> tuple[float, float]:
    """Least-squares Monte Carlo (Longstaff–Schwartz) for a swing option.

    spreads: (paths, days) daily exercise value, e.g. a battery's best intraday spread net of losses.
    rights: number of days the holder may exercise. Returns (value, perfect-foresight upper bound).
    Continuation values are regressed on the day's spread with one regression per remaining-rights
    level; the decision on each path uses the fitted values, cash flows use the realised ones.
    """
    n_paths, n_days = spreads.shape
    value = np.zeros((rights + 1, n_paths))  # realised future cash flow given rights left
    for d in range(n_days - 1, -1, -1):
        x = spreads[:, d]
        basis = np.vander((x - x.mean()) / (x.std() + 1e-12), degree + 1)
        new = value.copy()
        for r in range(1, rights + 1):
            keep, use = value[r], value[r - 1]
            if d == n_days - 1:
                exercise = x > 0
            else:
                gain = use - keep  # value of the right if kept
                coef, *_ = np.linalg.lstsq(basis, gain, rcond=None)
                exercise = x + basis @ coef > 0
            new[r] = np.where(exercise, x + use, keep)
        value = new
    foresight = np.sort(np.maximum(spreads, 0), axis=1)[:, -rights:].sum(axis=1)
    return float(value[rights].mean()), float(foresight.mean())
