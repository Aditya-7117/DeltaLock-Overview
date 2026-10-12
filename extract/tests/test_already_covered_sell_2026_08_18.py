# Extracted from the private DeltaLock repository: backend/tests/test_already_covered_sell_2026_08_18.py at commit 20f6b7f (4 Sep 2026).
# Changes: import paths; security ids and quantities replaced by placeholders (one lot is 10 here); the docstring tells the incident without order ids, prices or the margin figure; dashes replaced.
"""
Already-fully-covered rule: never a SECOND protective sell, 2026-08-18.

Holding a long is not by itself permission to sell. The long may already
be spoken for by a protective order that is working right now.

On 18-Aug two initial stop losses went out a second apart, one lot each
against a long of one lot. Each passed the held-quantity check on its
own; together they were two lots of short-side quantity. Dhan's RMS
refused the second ONLY because the account lacked the margin
for a naked short, a protection that disappears precisely as capital
scales.

WHY IT IS SAFE TO BLOCK HERE, when the guard otherwise fails OPEN: the
only thing that can trip this rule is an order that is ALREADY protecting
the position. Refusing to add a second one leaves the first in force, so
this can never create a naked position. Contrast the held-quantity
checks, which fail open on an unreachable broker exactly because blocking
there COULD leave a long unprotected.

No test in this file makes a real network call.
"""
import pytest
from unittest.mock import MagicMock

LONG_10 = [{"securityId": "1001", "netQty": 10}]


def _sell_order(qty=10, sec="1001", oid="SL-1", txn="SELL"):
    return {"securityId": sec, "transactionType": txn,
            "orderType": "STOP_LOSS", "quantity": qty, "orderId": oid}


def _client(positions, orders):
    c = MagicMock()
    c.get_positions.return_value = positions
    c.get_open_orders.return_value = orders
    return c


@pytest.fixture(autouse=True)
def _fresh_caches():
    from deltalock_extract.short_guard import invalidate_position_cache
    invalidate_position_cache()
    yield
    invalidate_position_cache()


class TestAlreadyFullyCoveredRule:

    def test_second_sl_against_a_fully_covered_long_is_blocked(self):
        """THE 18-AUG DUPLICATE."""
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)

        with pytest.raises(NakedShortBlocked, match="already-covered"):
            assert_sell_is_covered(
                _client(LONG_10, [_sell_order()]), "1001", 10,
                "place_stop_loss(dup)")

    def test_first_sl_with_nothing_resting_is_allowed(self):
        """The normal case must be untouched: this is the order that
        actually protects the position."""
        from deltalock_extract.short_guard import assert_sell_is_covered
        assert_sell_is_covered(
            _client(LONG_10, []), "1001", 10, "place_stop_loss(first)")

    def test_cancel_replace_is_unaffected(self):
        """By the time a replacement is placed the old order has been
        cancelled and verified, so nothing rests. Blocking legitimate
        replaces would be a naked-position bug of its own."""
        from deltalock_extract.short_guard import assert_sell_is_covered
        assert_sell_is_covered(
            _client(LONG_10, []), "1001", 10, "place_stop_loss(replace)")

    def test_market_exit_after_sl_cancel_is_allowed(self):
        """_handle_live_exit cancels and verifies the SL before selling, so
        the exit path must sail through."""
        from deltalock_extract.short_guard import assert_sell_is_covered
        assert_sell_is_covered(
            _client(LONG_10, []), "1001", 10, "place_market_sell(exit)")

    def test_resting_order_on_another_security_does_not_block(self):
        from deltalock_extract.short_guard import assert_sell_is_covered
        assert_sell_is_covered(
            _client(LONG_10, [_sell_order(sec="2002")]), "1001", 10,
            "place_stop_loss(other)")

    def test_resting_buy_order_does_not_block(self):
        from deltalock_extract.short_guard import assert_sell_is_covered
        assert_sell_is_covered(
            _client(LONG_10, [_sell_order(txn="BUY")]), "1001", 10,
            "place_stop_loss(buy)")

    def test_partial_resting_quantity_still_allows_the_remainder(self):
        """Resting 4 against a long of 10 does not fully cover it, so a
        protective order for the rest must remain placeable."""
        from deltalock_extract.short_guard import assert_sell_is_covered
        assert_sell_is_covered(
            _client(LONG_10, [_sell_order(qty=4)]), "1001", 6,
            "place_stop_loss(partial)")

    def test_order_scan_failure_falls_back_to_held_check(self):
        """This must never become a new way to fail CLOSED on a protective
        order: an unreadable order book cannot block a stop loss."""
        from deltalock_extract.short_guard import assert_sell_is_covered
        c = MagicMock()
        c.get_positions.return_value = LONG_10
        c.get_open_orders.side_effect = Exception("api down")
        assert_sell_is_covered(c, "1001", 10, "place_stop_loss(scanfail)")

    def test_blocking_never_leaves_the_position_unprotected(self):
        """The safety argument, stated directly: the only thing that trips
        this rule is an order already protecting the position, and the
        refusal does not touch it."""
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)
        resting = [_sell_order(oid="THE-PROTECTIVE-ONE")]
        c = _client(LONG_10, resting)

        with pytest.raises(NakedShortBlocked):
            assert_sell_is_covered(c, "1001", 10, "place_stop_loss(dup)")

        assert c.get_open_orders.return_value == resting
        assert not c.cancel_order.called

    def test_open_orders_are_cached_so_trailing_adds_no_rest_call(self):
        from deltalock_extract.short_guard import assert_sell_is_covered
        c = _client(LONG_10, [])
        for _ in range(20):
            assert_sell_is_covered(c, "1001", 10, "trail")
        assert c.get_open_orders.call_count == 1

    def test_blocked_duplicate_is_audited_as_critical(self):
        from deltalock_extract import short_guard
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)
        from unittest.mock import patch

        with patch.object(short_guard, "_audit") as aud:
            with pytest.raises(NakedShortBlocked):
                assert_sell_is_covered(
                    _client(LONG_10, [_sell_order()]), "1001", 10,
                    "place_stop_loss(dup)")

        assert aud.called
        assert aud.call_args[0][0] == "CRITICAL_SHORT_ORDER_BLOCKED"
