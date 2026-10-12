# Extracted from the private DeltaLock repository: backend/execution/stream_fields.py at commit 20f6b7f (4 Sep 2026).
# Changes: stream names, their aliases and their field prefixes replaced by neutral labels (BASE, V1, V2, V3), because the real names describe how each stream manages exits; the incident told without its trade ids, prices or P&L; an internal document reference removed; dashes replaced. Logic unchanged.
"""
Stream field selection: one place, so streams cannot contaminate.

THE ARCHITECTURE is unambiguous: four independent streams run
per trade, and exactly ONE of them ever touches live money.

    BASE        -> paper ONLY.  fields: current_sl, pnl, net_pnl, exit_price
    V1          -> paper + LIVE. fields: v1_*      <-- the only live one
    V2          -> paper ONLY.  fields: v2_*
    V3          -> paper ONLY.  fields: v3_*

The failure mode this module exists to remove is not a logic error, it is
a NAMING error. `current_sl` and `pnl` read like "the trade's SL" and
"the trade's P&L", so they get reached for by reflex, but they belong to
the paper-only BASE stream. A live path that uses them is placing real
orders, or reporting real money, from a simulation.

Both halves of that were live on 2026-08-17:

  - Three live SL placement paths (dhan_order_ws fast-retry, and the
    reconciler's naked-retry, resync and enforcement paths) selected the
    broker trigger as `current_sl or sl`. On one live trade the BASE
    stream had trailed to a different level from V1, so a restore would
    have put the real broker stop wider than the stream that owns it.
  - The dashboard's engine panel rendered `t.pnl` regardless of the
    selected stream, showing a paper profit for a trade whose live result
    was a loss.

Everything live-money-facing selects through here. `sl` (the original
signal stop) is the only shared, stream-agnostic field and is a safe
floor; `current_sl` is never an acceptable fallback for a live order.
"""

import logging

log = logging.getLogger(__name__)


def live_sl_trigger(state, default=None):
    """The stop level for a LIVE broker order on this trade.

    V1 is the only stream permitted to place live orders, so this
    returns the V1 level, falling back to the original signal SL,
    never to the BASE stream's `current_sl`, which trails
    independently and is paper-only.
    """
    v1 = getattr(state, "v1_current_sl", None)
    try:
        if v1 and float(v1) > 0:
            return float(v1)
    except (TypeError, ValueError):
        pass

    base = getattr(state, "sl", None)
    try:
        if base and float(base) > 0:
            log.warning(
                f"[LIVE_SL_FALLBACK_TO_SIGNAL] trade="
                f"{getattr(state, 'trade_id', '?')} v1_current_sl unset; "
                f"using the original signal SL {base}. NOT falling back to "
                f"current_sl: that is the paper-only BASE stream and would "
                f"place the real broker stop at a level this trade never set."
            )
            return float(base)
    except (TypeError, ValueError):
        pass

    return default


def stream_net_pnl(state, stream: str):
    """Net P&L for one named stream. Never silently returns another's.

    Returns None when the stream has no recorded result, so callers can
    render "no result" instead of a confident wrong number.
    """
    s = (stream or "").upper()
    if s in ("V1", "V1_LIVE", "V1_SL"):
        pair = ("v1_net_pnl", "v1_pnl")
    elif s in ("V2", "V2_PAPER"):
        pair = ("v2_net_pnl", "v2_pnl")
    elif s == "V3":
        pair = ("v3_net_pnl", "v3_pnl")
    elif s == "BASE":
        pair = ("net_pnl", "pnl")
    else:
        raise ValueError(f"unknown stream {stream!r}")

    for field in pair:
        val = getattr(state, field, None)
        if val is not None:
            return val
    return None
