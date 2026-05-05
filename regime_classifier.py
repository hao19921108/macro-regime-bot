#!/usr/bin/env python3
"""
Auto-fetching daily regime dashboard.

What it does
------------
1. Downloads prices automatically from Yahoo Finance:
   - SPY
   - GLD
   - VDE
2. Downloads 10Y Treasury yield automatically from FRED:
   - DGS10
3. Computes:
   - 20d realized vol on SPY
   - GLD vs SPY level + acceleration
   - VDE vs SPY level + acceleration
   - Rates level + acceleration
4. Builds a dynamic macro score:
   - GLD = primary risk-off
   - Rates = liquidity / duration pressure
   - VDE = inflation / commodity context
5. Combines:
   - volatility scaling
   - macro overlay
6. Prints a daily dashboard summary.

Install
-------
pip install pandas numpy yfinance pandas_datareader

Usage
-----
python regime_dashboard_autofetch.py

Optional:
python regime_dashboard_autofetch.py --start 2018-01-01 --target-vol 0.25

Notes
-----
- This is a daily dashboard / exposure engine, not an execution engine.
- If FRED temporarily fails, the script falls back to a neutral rates signal.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


# =========================
# Defaults
# =========================
DEFAULT_START = "2018-01-01"
DEFAULT_TARGET_VOL = 0.25
REALIZED_VOL_WINDOW = 20
RS_LOOKBACK = 63
ACCEL_LOOKBACK = 21
GLD_WEIGHT_CAP = 0.60


# =========================
# Helpers
# =========================


def load_rates_yahoo(start):
    df = yf.download("^TNX", start=start, progress=False)
    df = df[["Close"]].rename(columns={"Close": "RATE"})
    
    # ^TNX is 10x the yield (e.g. 40 = 4.0%)
    df["RATE"] = df["RATE"] / 10.0
    
    return df


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def to_unit_signal(x: float, scale: float) -> float:
    """
    Map a raw value into [-1, 1] using a linear clip.
    """
    if pd.isna(x):
        return 0.0
    return clip(x / scale, -1.0, 1.0)


def annualized_vol(returns: pd.Series, window: int) -> pd.Series:
    return returns.rolling(window).std() * np.sqrt(252)


def regime_label(score: float) -> str:
    if score > 0.80:
        return "Strong risk-off"
    if score > 0.45:
        return "Risk-off"
    if score > 0.10:
        return "Caution"
    if score < -0.25:
        return "Risk-on"
    return "Neutral"


def exposure_band(x: float) -> str:
    if x >= 0.90:
        return "Full"
    if x >= 0.70:
        return "High"
    if x >= 0.50:
        return "Moderate"
    if x >= 0.30:
        return "Low"
    return "Defensive"


def macro_multiplier_from_score(score: float) -> float:
    """
    Stepwise mapping from macro score to exposure multiplier.
    """
    if score > 0.80:
        return 0.40
    if score > 0.45:
        return 0.60
    if score > 0.10:
        return 0.75
    if score > -0.25:
        return 0.90
    return 1.00


def apply_gld_weight_cap(w_gld: float, w_vde: float, w_rates: float, cap: float) -> tuple[float, float, float]:
    """
    Prevent GLD from dominating too much.
    """
    if w_gld <= cap:
        return w_gld, w_vde, w_rates

    overflow = w_gld - cap
    w_gld = cap
    other = w_vde + w_rates

    if other <= 0:
        return w_gld, (1.0 - w_gld) / 2.0, (1.0 - w_gld) / 2.0

    w_vde += overflow * (w_vde / other)
    w_rates += overflow * (w_rates / other)
    total = w_gld + w_vde + w_rates
    return w_gld / total, w_vde / total, w_rates / total


# =========================
# Data loaders
# =========================
def load_prices_from_yahoo(start: str) -> pd.DataFrame:
    tickers = ["SPY", "GLD", "VDE"]
    raw = yf.download(
        tickers,
        start=start,
        auto_adjust=True,
        progress=False,
        group_by="column",
    )

    if raw.empty:
        raise RuntimeError("Yahoo Finance returned no price data.")

    # yfinance can return either:
    # 1) MultiIndex columns with top-level fields like Close / Open
    # 2) Flat columns for one ticker
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            prices = raw["Close"].copy()
        else:
            # Fallback if structure differs
            prices = raw.xs("Close", axis=1, level=0, drop_level=True).copy()
    else:
        # Single ticker fallback, though here we request multiple
        prices = raw[["Close"]].rename(columns={"Close": "SPY"}).copy()

    prices = prices.rename_axis("Date").sort_index()
    prices = prices.ffill().dropna(how="all")

    missing = [t for t in tickers if t not in prices.columns]
    if missing:
        raise RuntimeError(f"Missing downloaded price columns: {missing}")

    return prices[tickers]


def load_rates_from_fred(start: str) -> pd.DataFrame:
    """
    DGS10 = 10-Year Treasury Constant Maturity Rate, percent.
    """
    rates = pdr.DataReader("DGS10", "fred", start)
    rates = rates.rename(columns={"DGS10": "RATE"})
    rates = rates.ffill()
    return rates


# =========================
# Signal construction
# =========================
def compute_rs_signal(
    asset: pd.Series,
    benchmark: pd.Series,
    lookback: int,
    accel_lookback: int,
    level_scale: float,
    accel_scale: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Relative-strength signal:
    level = asset_return - benchmark_return over lookback
    accel = change in level vs accel_lookback ago
    final = 0.7 * level_signal + 0.3 * accel_signal
    """
    asset_ret = asset / asset.shift(lookback) - 1.0
    bench_ret = benchmark / benchmark.shift(lookback) - 1.0

    level = asset_ret - bench_ret
    accel = level - level.shift(accel_lookback)

    level_sig = level.apply(lambda x: to_unit_signal(x, level_scale))
    accel_sig = accel.apply(lambda x: to_unit_signal(x, accel_scale))

    final_sig = 0.7 * level_sig + 0.3 * accel_sig
    final_sig = final_sig.clip(-1.0, 1.0)

    return level_sig, accel_sig, final_sig


def compute_rate_signal(
    rate: pd.Series,
    accel_lookback: int,
    level_scale: float = 0.50,
    accel_scale: float = 0.25,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Rates signal:
    level = 21d change in rate
    accel = change in that level vs 21d ago

    Positive = tighter / more caution.
    """
    level = rate - rate.shift(accel_lookback)
    accel = level - level.shift(accel_lookback)

    level_sig = level.apply(lambda x: to_unit_signal(x, level_scale))
    accel_sig = accel.apply(lambda x: to_unit_signal(x, accel_scale))

    final_sig = 0.7 * level_sig + 0.3 * accel_sig
    final_sig = final_sig.clip(-1.0, 1.0)

    return level_sig, accel_sig, final_sig


@dataclass
class DashboardState:
    date: pd.Timestamp
    spy_price: float
    gld_price: float
    vde_price: float
    rate_value: Optional[float]

    realized_vol: float
    vol_multiplier: float

    gld_level: float
    gld_accel: float
    gld_signal: float

    vde_level: float
    vde_accel: float
    vde_signal: float

    rate_level: float
    rate_accel: float
    rate_signal: float

    w_gld: float
    w_vde: float
    w_rates: float

    macro_score: float
    macro_multiplier: float
    final_exposure: float
    regime: str


def build_dashboard(start: str, target_vol: float) -> DashboardState:
    prices = load_prices_from_yahoo(start)

    df = prices.copy()

    # Rates optional fallback
    try:
       rates = load_rates_yahoo(start)
       df = df.join(rates, how="left")
       df["RATE"] = df["RATE"].ffill()
    except Exception as e:
       print("Rates fetch failed, using neutral signal.")
       df["RATE"] = np.nan
    
    # Volatility engine
    df["SPY_RET"] = df["SPY"].pct_change()
    df["REALIZED_VOL"] = annualized_vol(df["SPY_RET"], REALIZED_VOL_WINDOW)
    df["VOL_MULT"] = (target_vol / df["REALIZED_VOL"]).clip(lower=0.0, upper=1.0)

    # GLD vs SPY
    gld_level, gld_accel, gld_sig = compute_rs_signal(
        df["GLD"],
        df["SPY"],
        RS_LOOKBACK,
        ACCEL_LOOKBACK,
        level_scale=0.10,
        accel_scale=0.05,
    )
    df["GLD_LEVEL"] = gld_level
    df["GLD_ACCEL"] = gld_accel
    df["GLD_SIG"] = gld_sig

    # VDE vs SPY
    vde_level, vde_accel, vde_sig = compute_rs_signal(
        df["VDE"],
        df["SPY"],
        RS_LOOKBACK,
        ACCEL_LOOKBACK,
        level_scale=0.12,
        accel_scale=0.06,
    )
    df["VDE_LEVEL"] = vde_level
    df["VDE_ACCEL"] = vde_accel
    df["VDE_SIG"] = vde_sig

    # Rates
    if df["RATE"].notna().any():
        rate_level, rate_accel, rate_sig = compute_rate_signal(
            df["RATE"],
            ACCEL_LOOKBACK,
            level_scale=0.50,
            accel_scale=0.25,
        )
        df["RATE_LEVEL"] = rate_level
        df["RATE_ACCEL"] = rate_accel
        df["RATE_SIG"] = rate_sig
    else:
        df["RATE_LEVEL"] = 0.0
        df["RATE_ACCEL"] = 0.0
        df["RATE_SIG"] = 0.0

    # Dynamic weights by signal strength
    abs_sum = df["GLD_SIG"].abs() + df["VDE_SIG"].abs() + df["RATE_SIG"].abs()
    abs_sum = abs_sum.replace(0, np.nan)

    df["W_GLD"] = (df["GLD_SIG"].abs() / abs_sum).fillna(1 / 3)
    df["W_VDE"] = (df["VDE_SIG"].abs() / abs_sum).fillna(1 / 3)
    df["W_RATE"] = (df["RATE_SIG"].abs() / abs_sum).fillna(1 / 3)

    capped_weights = df[["W_GLD", "W_VDE", "W_RATE"]].apply(
        lambda row: pd.Series(
            apply_gld_weight_cap(
                float(row["W_GLD"]),
                float(row["W_VDE"]),
                float(row["W_RATE"]),
                GLD_WEIGHT_CAP,
            ),
            index=["W_GLD_CAP", "W_VDE_CAP", "W_RATE_CAP"],
        ),
        axis=1,
    )
    df = pd.concat([df, capped_weights], axis=1)

    # Composite macro score
    df["MACRO_SCORE"] = (
        df["W_GLD_CAP"] * df["GLD_SIG"]
        + df["W_VDE_CAP"] * df["VDE_SIG"]
        + df["W_RATE_CAP"] * df["RATE_SIG"]
    ).clip(-1.0, 1.0)

    df["MACRO_MULT"] = df["MACRO_SCORE"].apply(macro_multiplier_from_score)
    df["FINAL_EXPOSURE"] = (df["VOL_MULT"] * df["MACRO_MULT"]).clip(lower=0.0, upper=1.0)

    latest = df.dropna(subset=["SPY", "GLD", "VDE", "REALIZED_VOL", "FINAL_EXPOSURE"]).iloc[-1]

    return DashboardState(
        date=latest.name,
        spy_price=float(latest["SPY"]),
        gld_price=float(latest["GLD"]),
        vde_price=float(latest["VDE"]),
        rate_value=float(latest["RATE"]) if pd.notna(latest["RATE"]) else None,

        realized_vol=float(latest["REALIZED_VOL"]),
        vol_multiplier=float(latest["VOL_MULT"]),

        gld_level=float(latest["GLD_LEVEL"]),
        gld_accel=float(latest["GLD_ACCEL"]),
        gld_signal=float(latest["GLD_SIG"]),

        vde_level=float(latest["VDE_LEVEL"]),
        vde_accel=float(latest["VDE_ACCEL"]),
        vde_signal=float(latest["VDE_SIG"]),

        rate_level=float(latest["RATE_LEVEL"]),
        rate_accel=float(latest["RATE_ACCEL"]),
        rate_signal=float(latest["RATE_SIG"]),

        w_gld=float(latest["W_GLD_CAP"]),
        w_vde=float(latest["W_VDE_CAP"]),
        w_rates=float(latest["W_RATE_CAP"]),

        macro_score=float(latest["MACRO_SCORE"]),
        macro_multiplier=float(latest["MACRO_MULT"]),
        final_exposure=float(latest["FINAL_EXPOSURE"]),
        regime=regime_label(float(latest["MACRO_SCORE"])),
    )


def print_dashboard(state: DashboardState, target_vol: float) -> None:
    print("\n" + "=" * 72)
    print("AUTO-FETCH DAILY REGIME DASHBOARD")
    print("=" * 72)

    print(f"Date                : {state.date.date()}")
    print(f"SPY                 : {state.spy_price:,.2f}")
    print(f"GLD                 : {state.gld_price:,.2f}")
    print(f"VDE                 : {state.vde_price:,.2f}")
    print(f"10Y rate            : {state.rate_value:,.2f}" if state.rate_value is not None else "10Y rate            : N/A")

    print("\nVOLATILITY ENGINE")
    print("-" * 72)
    print(f"20d realized vol    : {state.realized_vol:.2%}")
    print(f"Target vol          : {target_vol:.2%}")
    print(f"Vol multiplier      : {state.vol_multiplier:.2f}")

    print("\nMACRO SIGNALS")
    print("-" * 72)
    print(f"GLD level           : {state.gld_level:+.2f}")
    print(f"GLD acceleration    : {state.gld_accel:+.2f}")
    print(f"GLD final signal    : {state.gld_signal:+.2f}")
    print()
    print(f"VDE level           : {state.vde_level:+.2f}")
    print(f"VDE acceleration    : {state.vde_accel:+.2f}")
    print(f"VDE final signal    : {state.vde_signal:+.2f}")
    print()
    print(f"Rates level         : {state.rate_level:+.2f}")
    print(f"Rates acceleration  : {state.rate_accel:+.2f}")
    print(f"Rates final signal  : {state.rate_signal:+.2f}")

    print("\nDYNAMIC WEIGHTS")
    print("-" * 72)
    print(f"GLD weight          : {state.w_gld:.1%}")
    print(f"VDE weight          : {state.w_vde:.1%}")
    print(f"Rates weight        : {state.w_rates:.1%}")

    print("\nFINAL OUTPUT")
    print("-" * 72)
    print(f"Macro score         : {state.macro_score:+.2f}")
    print(f"Regime              : {state.regime}")
    print(f"Macro multiplier    : {state.macro_multiplier:.2f}")
    print(f"Final exposure      : {state.final_exposure:.1%} ({exposure_band(state.final_exposure)})")

    print("\nINTERPRETATION")
    print("-" * 72)
    if state.macro_score > 0.45:
        print("Reduce gross exposure. Favor defense, cash, or lower-beta sleeves.")
    elif state.macro_score > 0.10:
        print("Trim risk. Demand stronger confirmation before adding exposure.")
    elif state.macro_score < -0.25:
        print("Backdrop is supportive. Allow stronger deployment if leadership is clean.")
    else:
        print("Neutral backdrop. Let the RS + band sleeve selection drive positioning.")

    print("=" * 72 + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-fetching regime dashboard")
    parser.add_argument("--start", default=DEFAULT_START, help="Start date, e.g. 2018-01-01")
    parser.add_argument("--target-vol", type=float, default=DEFAULT_TARGET_VOL, help="Target annualized vol, default 0.25")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = build_dashboard(start=args.start, target_vol=args.target_vol)
    print_dashboard(state, args.target_vol)


if __name__ == "__main__":
    main()
