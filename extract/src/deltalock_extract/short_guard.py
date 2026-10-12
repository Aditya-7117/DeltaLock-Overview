# Extracted from the private DeltaLock repository: backend/broker_adapters/short_guard.py at commit 20f6b7f (4 Sep 2026).
# Changes: the 18-Aug incident told in lots rather than real quantities, without the margin figure; dashes replaced in comments and messages. The audit hook imports a module that is not in this extract, so here it is a silent no-op; the tests patch it. Logic unchanged.
"""
NEVER-SHORT GUARD: the last wall before any SELL reaches Dhan.

THE RULE THIS ENFORCES
----------------------
DeltaLock is a long-only options system. It BUYS options and later sells
what it bought. It must never, under any circumstance (bug, race,
restart, stale state, operator error), transmit a SELL that is not
covered by a long position it actually holds. An uncovered SELL on an
index option is a naked short: unbounded loss, and it scales with lot
size. This system is expected to carry materially larger size within
months, so this cannot rest on "no code path does that today". Code
paths change. This one check does not.

WHY IT LIVES HERE
-----------------
Every order this system places (entry, stop loss, exit, emergency
flatten, reconciler restore) funnels through
DhanRESTClient._execute_with_retry. Guarding the individual callers means
guarding a list that grows; guarding here means a future call site cannot
opt out, because there is nowhere else to go. Defence in depth, not a
replacement for the caller-level checks (_place_sl_safe's position
confirmation, _handle_live_exit's cancel-verify, the reconciler's
exit-in-flight skip). Those prevent the situation; this makes the
outcome impossible.

THE DECISION, AND WHY IT IS ASYMMETRIC
--------------------------------------
  long already fully covered by
  resting SELL orders                   -> BLOCK (the 18-Aug duplicate)
  broker shows a long >= requested qty  -> ALLOW
  broker shows a long < requested qty   -> BLOCK (would sell more than held)
  broker shows NO long at all           -> BLOCK (this is the naked short)
  broker cannot be reached              -> ALLOW, loudly

The first line exists because holding a long is not by itself permission
to sell: the long may already be spoken for by a protective order that is
working right now. Two stop losses of one lot against a long of one lot
each pass the held-quantity test alone, while together they are two lots
of short-side quantity. Blocking there can never create a naked
position: the only thing that trips it is an order ALREADY protecting
the position, and refusing to add a second leaves the first in force.

The last line is the opposite trade-off, and is the only real judgement
call here. Blocking on an unreachable broker would mean an API blip could
stop us placing a STOP LOSS, leaving a real long unprotected, trading a
bounded, known loss for an unbounded, unknown one. We cannot prove a
short when we cannot see the account, and an unprotected long is the
worse failure. Every such case is logged CRITICAL and audited so it can
never pass silently.

Positions are cached briefly: SL trailing calls this on a live path and
must not add a REST round-trip per modify.
"""

import logging
import threading
import time

log = logging.getLogger(__name__)

# Positions rarely change intra-trade; this only needs to be fresh enough
# to catch "we are already flat". Short enough that a fill is reflected
# almost immediately, long enough that SL trailing does not add a REST
# call per modify.
_POSITION_CACHE_TTL_SEC = 2.0

_cache_lock = threading.Lock()
_positions_cache = {"ts": 0.0, "by_sid": None}


class NakedShortBlocked(Exception):
    """Raised instead of transmitting an uncovered SELL."""


def _net_long_qty(positions, security_id: str) -> int:
    """Net long quantity held for one security, across position shapes."""
    sid = str(security_id)
    total = 0
    for pos in positions or []:
        pos_sid = str(
            pos.get("securityId")
            or pos.get("SecurityId")
            or pos.get("security_id")
            or ""
        )
        if pos_sid != sid:
            continue
        qty = (
            pos.get("netQty")
            if pos.get("netQty") is not None
            else pos.get("net_qty")
            if pos.get("net_qty") is not None
            else pos.get("netQuantity")
        )
        try:
            total += int(float(qty or 0))
        except (TypeError, ValueError):
            continue
    return total


def _get_positions_cached(client):
    with _cache_lock:
        if (
            _positions_cache["by_sid"] is not None
            and (time.time() - _positions_cache["ts"]) < _POSITION_CACHE_TTL_SEC
        ):
            return _positions_cache["by_sid"], True

    positions = client.get_positions()

    with _cache_lock:
        _positions_cache["by_sid"] = positions
        _positions_cache["ts"] = time.time()
    return positions, False


_orders_cache = {"orders": None, "ts": 0.0}


def _get_open_orders_cached(client):
    """Resting orders, cached on the same short TTL as positions.

    SL trailing calls the guard on a live path, so this must not add a
    REST round-trip per modify. (Modifies do not carry
    transaction_type=SELL and so never reach the guard at all; this
    matters for the cancel-replace PLACE that follows one.)
    """
    with _cache_lock:
        if (
            _orders_cache["orders"] is not None
            and (time.time() - _orders_cache["ts"]) < _POSITION_CACHE_TTL_SEC
        ):
            return _orders_cache["orders"]

    orders = client.get_open_orders()

    with _cache_lock:
        _orders_cache["orders"] = orders
        _orders_cache["ts"] = time.time()
    return orders


def _resting_sell_qty(orders, security_id: str) -> int:
    """Total SELL quantity already working at the broker for one security."""
    sid = str(security_id)
    total = 0
    for o in orders or []:
        o_sid = str(
            o.get("securityId") or o.get("SecurityId") or o.get("security_id") or ""
        )
        if o_sid != sid:
            continue
        if (o.get("transactionType") or o.get("TransactionType") or "").upper() != "SELL":
            continue
        qty = (
            o.get("remainingQuantity")
            if o.get("remainingQuantity") is not None
            else o.get("quantity")
            if o.get("quantity") is not None
            else o.get("Quantity")
        )
        try:
            total += int(float(qty or 0))
        except (TypeError, ValueError):
            continue
    return total


def invalidate_position_cache():
    """Call after any fill so the next check re-reads broker truth."""
    with _cache_lock:
        _positions_cache["by_sid"] = None
        _positions_cache["ts"] = 0.0
        _orders_cache["orders"] = None
        _orders_cache["ts"] = 0.0


def assert_sell_is_covered(client, security_id: str, quantity: int, context: str):
    """Raise NakedShortBlocked rather than transmit an uncovered SELL.

    `client` must expose get_positions(). `context` is the operation label
    used in logs and audit so a block is traceable to its call site.
    """
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return  # nothing to sell; nothing to guard

    try:
        positions, from_cache = _get_positions_cached(client)
    except Exception as exc:
        # Cannot see the account. See the module docstring: allowing here
        # keeps stop-loss placement possible during a broker/API blip,
        # which is the lesser of the two risks, but it is never silent.
        log.critical(
            f"[SHORT_GUARD_BLIND] context={context} sid={security_id} qty={qty} "
            f"err={exc}: cannot read broker positions, so a short cannot be "
            f"proven or disproven. ALLOWING so that protective orders remain "
            f"placeable; an unprotected long is the worse failure. Verify "
            f"manually if this repeats."
        )
        _audit("SHORT_GUARD_BLIND", security_id, qty, context, str(exc))
        return

    held = _net_long_qty(positions, security_id)

    # ALREADY-FULLY-COVERED RULE (2026-08-18).
    #
    # Holding a long is not by itself permission to sell: the long may
    # ALREADY be spoken for by a protective order that is working right
    # now. On 18-Aug two initial stop losses were placed a second apart,
    # one lot each against a long of one lot. Each passed the held-quantity
    # check on its own while together they were two lots of short-side
    # quantity. Dhan's RMS refused the second only because
    # the account lacked the margin for a naked short; on a larger account
    # it is accepted and both triggering leaves the position SHORT one lot.
    #
    # So: if the resting SELL quantity for this security already covers
    # the entire long, anything additional is by definition uncovered.
    #
    # THIS CAN NEVER CREATE A NAKED POSITION, which is what makes it safe
    # to block on. The only thing that trips it is an order that is
    # ALREADY protecting the position; refusing to add a second one
    # leaves the first one working. Contrast the held-quantity checks
    # below, which is why THOSE fail open when the broker is unreachable.
    #
    # A legitimate cancel-replace is unaffected: by the time the
    # replacement is placed the old order has been cancelled and verified,
    # so resting is 0. If a cancel has not settled yet, blocking is the
    # correct outcome anyway: the position keeps the old, still-live stop.
    if held > 0:
        try:
            resting = _resting_sell_qty(_get_open_orders_cached(client), security_id)
        except Exception as exc:
            resting = 0
            log.warning(
                f"[SHORT_GUARD_ORDER_SCAN_FAILED] context={context} "
                f"sid={security_id} err={exc}: cannot see resting orders; "
                f"falling back to the held-quantity check alone"
            )
        if resting >= held:
            msg = (
                f"BLOCKED already-covered SELL: context={context} "
                f"sid={security_id} qty={qty}: the long of {held} is ALREADY "
                f"fully covered by {resting} of resting SELL quantity. Adding "
                f"more is short-side exposure. The existing protective order "
                f"stays in force, so nothing is left unprotected by this."
            )
            log.critical(f"[SHORT_GUARD_BLOCKED] {msg}")
            _audit("CRITICAL_SHORT_ORDER_BLOCKED", security_id, qty, context,
                   f"net_long={held} resting_sell={resting}")
            raise NakedShortBlocked(msg)

    if held >= qty:
        return

    if held <= 0:
        msg = (
            f"BLOCKED uncovered SELL: context={context} sid={security_id} "
            f"qty={qty} but broker shows NO long position (net={held}). "
            f"This system is long-only and never shorts options; "
            f"transmitting this would open a naked short."
        )
    else:
        msg = (
            f"BLOCKED oversized SELL: context={context} sid={security_id} "
            f"qty={qty} exceeds the long actually held (net={held}). The "
            f"excess would be a naked short."
        )

    log.critical(f"[SHORT_GUARD_BLOCKED] {msg}")
    _audit("CRITICAL_SHORT_ORDER_BLOCKED", security_id, qty, context,
           f"net_long={held}")
    raise NakedShortBlocked(msg)


def _audit(event_type: str, security_id, quantity, context: str, detail: str):
    try:
        from backend.audit.audit_emitter import emit_audit
        emit_audit(
            event_type=event_type,
            trade_id=context,
            category="RISK",
            source="SHORT_GUARD",
            mode="LIVE",
            payload={
                "security_id": str(security_id),
                "quantity": quantity,
                "context": context,
                "detail": detail,
            },
        )
    except Exception:
        pass  # audit must never be the reason an order decision changes
