# Extracted from the private DeltaLock repository: backend/broker_adapters/sl_order_ledger.py at commit 20f6b7f (4 Sep 2026).
# Changes: a margin figure removed from the docstring; dashes replaced. Logic unchanged.
"""
Every SL order id a trade has ever owned, so a rejection can never be
invisible just because the id has since been superseded.

WHAT WENT WRONG (2026-08-18)
----------------------------
Both live trades had their INITIAL stop loss rejected by Dhan RMS
("insufficient funds") seconds after the entry filled. Dhan's position
API already showed the long, but its MARGIN ledger had not yet linked
the covering buy, so RMS priced a routine protective sell as a naked
short and refused it. A retry placed
moments later succeeded both times, and trailing then ran normally.

The recovery worked. The problem is that our logs contain NO RECORD of
either rejection.

dhan_order_ws routes order updates with
`elif state.sl_broker_order_id == order_no`. By the time each
rejection arrived, that field already held the RETRY's id, so the
rejection matched nothing and fell through in silence: no audit, no
naked_alert, no counter. We only learned it happened because the
operator looked at the broker's order book.

That is the dangerous part. Not the rejection itself, which is a broker
timing artifact we cannot prevent and which the retry path handles. The
danger is a protective order failing while the system believes it is
protected. If the retry had ALSO failed, the position would have been
naked with nothing raised.

So: remember every SL id per trade. A terminal status on ANY of them is
recognised and audited, whether or not it is the current one.

Bounded per trade and per process: a handful of ids per trade, and
finished trades are dropped by clear_sl_orders().
"""

import logging
import threading

log = logging.getLogger(__name__)

_MAX_TRADES = 300
_MAX_IDS_PER_TRADE = 40

_ledger = {}            # trade_id -> list[str] (insertion-ordered, deduped)
_by_order = {}          # order_id -> trade_id
_guard = threading.Lock()


def record_sl_order(trade_id: str, order_id) -> None:
    """Remember that this trade owned this SL order id. Never raises."""
    if not trade_id or not order_id:
        return
    oid = str(order_id)
    try:
        with _guard:
            if len(_ledger) >= _MAX_TRADES and trade_id not in _ledger:
                for _old in list(_ledger.keys())[:50]:
                    for _o in _ledger.pop(_old, []):
                        _by_order.pop(_o, None)
            ids = _ledger.setdefault(trade_id, [])
            if oid not in ids:
                ids.append(oid)
                if len(ids) > _MAX_IDS_PER_TRADE:
                    dropped = ids.pop(0)
                    _by_order.pop(dropped, None)
            _by_order[oid] = trade_id
    except Exception:
        pass


def owner_of(order_id) -> str:
    """Which trade owned this SL order id, if any."""
    if not order_id:
        return None
    with _guard:
        return _by_order.get(str(order_id))


def known_ids(trade_id: str):
    with _guard:
        return list(_ledger.get(trade_id, []))


def clear_sl_orders(trade_id: str) -> None:
    with _guard:
        for oid in _ledger.pop(trade_id, []):
            _by_order.pop(oid, None)
