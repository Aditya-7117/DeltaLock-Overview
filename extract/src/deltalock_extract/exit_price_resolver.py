# Extracted from the private DeltaLock repository: backend/broker_adapters/exit_price_resolver.py at commit 20f6b7f (4 Sep 2026).
# Changes: the incident in the docstring is told with illustrative numbers instead of the real instrument, prices and P&L; one log message drops a P&L figure; dashes replaced. Logic unchanged.
"""
Per-trade exit price resolution: order truth, never a blended average.

THE BUG THIS EXISTS TO PREVENT (2026-08-17)
-------------------------------------------
Recovery paths that needed a real exit price were reading `sellAvg` off
the broker's POSITION record. A position is netted per security, so
`sellAvg` is the average across EVERY sell in that security for the day.
The moment the same instrument is traded twice in a session, that number
belongs to neither trade.

That day one option contract was traded twice. Illustrative numbers
with the same shape:
    trade 1 sold at  80.00
    trade 2 sold at 120.00
    position sellAvg = (80.00 + 120.00) / 2 = 100.00

The blended figure was duly written to BOTH trades' records as "the
real exit price recovered from the broker", which understated one
trade's P&L and the day's reported total. Dhan's position record
blends the buy side identically.

Nothing was fabricated. It was a real broker number, correctly fetched,
and completely wrong for the question being asked.

PRECEDENCE
----------
1. ORDER TRUTH: averageTradedPrice on THIS trade's own exit order, or
   the SL order that filled it. Per-order, exact, immune to netting.
   Always preferred.
2. POSITION AVERAGE, only when provably unblended: the position's total
   sold quantity equals this trade's quantity, so no other trade
   contributed to it.
3. NOTHING. Return None and let the caller flag the trade for manual
   reconciliation. A wrong P&L that looks authoritative is worse than an
   obviously missing one: the first is silently trusted, the second gets
   checked against the contract note.

Rule 3 is the point. When a position was closed outside this system
(an operator squaring off in the broker app, as happened on 17-Aug), we
hold no order id for that fill and the position average is a blend. That
is genuinely unattributable, and saying so is the only honest answer.
"""

import logging

log = logging.getLogger(__name__)


def _order_traded_price(client, order_id):
    """averageTradedPrice for one order, or None."""
    if not order_id:
        return None
    try:
        details = client.get_order_details(order_id) or {}
    except Exception as exc:
        log.warning(f"[EXIT_PRICE_ORDER_QUERY_FAILED] order={order_id} err={exc}")
        return None

    status = (
        details.get("orderStatus") or details.get("orderstatus") or ""
    ).upper()
    if status not in ("TRADED", "COMPLETE", "PART_TRADED"):
        return None

    raw = details.get("averageTradedPrice") or details.get("price")
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def resolve_exit_price(client, security_id, trade_qty, order_ids=()):
    """Best defensible exit price for ONE trade.

    Returns (price, source). `price` is None when no trustworthy value
    exists; callers must treat that as "flag for manual reconciliation",
    never as licence to substitute an internal estimate and present it as
    broker truth.

    `order_ids` should list this trade's own candidate fill orders,
    most-authoritative first, typically its exit order id then its SL
    order id.
    """
    for oid in order_ids:
        price = _order_traded_price(client, oid)
        if price is not None:
            return price, f"ORDER_TRUTH:{oid}"

    # No usable order. The position average is only meaningful if this
    # trade is the ONLY thing that sold this security today.
    try:
        pos = client.get_position_for_security(str(security_id)) or {}
    except Exception as exc:
        log.warning(
            f"[EXIT_PRICE_POSITION_QUERY_FAILED] sec={security_id} err={exc}"
        )
        return None, "UNRESOLVED_BROKER_UNREACHABLE"

    sell_avg = 0.0
    try:
        sell_avg = float(pos.get("sellAvg") or pos.get("SellAvg") or 0)
    except (TypeError, ValueError):
        sell_avg = 0.0

    sold_qty = _int(pos.get("sellQty") or pos.get("SellQty"))
    want_qty = _int(trade_qty)

    if sell_avg > 0 and want_qty > 0 and sold_qty == want_qty:
        # Exactly this trade's quantity was sold in this security all
        # day, so the average cannot be a blend of anything else.
        return sell_avg, "POSITION_AVG_UNBLENDED"

    if sell_avg > 0 and sold_qty > want_qty:
        log.critical(
            f"[EXIT_PRICE_BLENDED_REFUSED] sec={security_id} "
            f"sellAvg={sell_avg} sold_qty={sold_qty} trade_qty={want_qty}: "
            f"this security was sold more than once today, so the position "
            f"average belongs to no single trade. REFUSING to report it as "
            f"this trade's exit price (17-Aug: doing exactly that understated "
            f"one trade's P&L). Flagging for manual reconciliation."
        )
        return None, "UNRESOLVED_BLENDED_POSITION"

    return None, "UNRESOLVED_NO_BROKER_EVIDENCE"
