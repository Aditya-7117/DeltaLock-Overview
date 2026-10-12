# Extracted from the private DeltaLock repository: backend/tests/test_never_short_2026_08_17.py at commit 20f6b7f (4 Sep 2026).
# Changes: only the TestNeverShortGuard class is kept (the other classes exercise the broker client and the reconciler, which are not in this extract); import paths; security ids and quantities replaced by placeholders; the dummy credential setup and an unused import removed; the module docstring cut to the part these tests cover; dashes replaced.
"""
NEVER-SHORT guarantees, from the 2026-08-17 session.

DeltaLock is long-only. It buys options and later sells what it bought.
An uncovered SELL on an index option is a naked short: unbounded loss,
scaling directly with lot size. This must be structurally impossible, not
merely absent from today's call graph.

THE WALL (short_guard). Every order funnels through
DhanRESTClient._execute_with_retry, so a SELL that is not covered by a real long
position is refused there and cannot reach the broker no matter which
call site issued it.

No test in this file makes a real network call.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _fresh_position_cache():
    from deltalock_extract import short_guard
    short_guard.invalidate_position_cache()
    yield
    short_guard.invalidate_position_cache()


def _client(positions):
    c = MagicMock()
    c.get_positions.return_value = positions
    return c


# =====================================================================
# 1. THE WALL
# =====================================================================

class TestNeverShortGuard:

    def test_sell_with_no_position_is_blocked(self):
        """THE CATASTROPHIC CASE. Broker is flat, something asks to SELL.
        This is a naked short and must never reach the exchange."""
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)

        with pytest.raises(NakedShortBlocked, match="NO long position"):
            assert_sell_is_covered(_client([]), "1001", 10, "place_stop_loss(T1)")

    def test_sell_larger_than_the_long_is_blocked(self):
        """Selling 20 against a long of 10 shorts the excess 10."""
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)

        client = _client([{"securityId": "1001", "netQty": 10}])
        with pytest.raises(NakedShortBlocked, match="exceeds the long"):
            assert_sell_is_covered(client, "1001", 20, "place_market_sell(T1)")

    def test_covered_sell_is_allowed(self):
        from deltalock_extract.short_guard import assert_sell_is_covered
        client = _client([{"securityId": "1001", "netQty": 10}])
        assert_sell_is_covered(client, "1001", 10, "place_stop_loss(T1)")

    def test_a_long_in_a_different_strike_does_not_cover_this_one(self):
        """Netting is per security. A long in one strike must not authorise
        a SELL in another; that would be a short in the second strike."""
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)

        client = _client([{"securityId": "1001", "netQty": 10}])
        with pytest.raises(NakedShortBlocked):
            assert_sell_is_covered(client, "1002", 10, "place_stop_loss(T2)")

    def test_a_short_leg_elsewhere_cannot_be_used_as_cover(self):
        from deltalock_extract.short_guard import (
            assert_sell_is_covered, NakedShortBlocked)

        client = _client([{"securityId": "1001", "netQty": -10}])
        with pytest.raises(NakedShortBlocked):
            assert_sell_is_covered(client, "1001", 10, "place_market_sell(T1)")

    def test_unreachable_broker_allows_but_screams(self):
        """Deliberate asymmetry. Blocking on an API blip would stop us
        placing a STOP LOSS on a real long, trading a bounded loss for an
        unbounded one. Allowed, never silent."""
        from deltalock_extract.short_guard import assert_sell_is_covered

        client = MagicMock()
        client.get_positions.side_effect = Exception("api down")

        with patch("deltalock_extract.short_guard._audit") as aud:
            assert_sell_is_covered(client, "1001", 10, "place_stop_loss(T1)")

        assert aud.called and aud.call_args[0][0] == "SHORT_GUARD_BLIND"

    def test_positions_are_cached_so_sl_trailing_adds_no_rest_call(self):
        """SL trailing calls this on a live path; a REST round-trip per
        modify would reintroduce the latency this session was spent
        removing."""
        from deltalock_extract.short_guard import assert_sell_is_covered

        client = _client([{"securityId": "1001", "netQty": 10}])
        for _ in range(20):
            assert_sell_is_covered(client, "1001", 10, "trail")

        assert client.get_positions.call_count == 1

    def test_cache_invalidation_forces_a_re_read(self):
        from deltalock_extract import short_guard

        client = _client([{"securityId": "1001", "netQty": 10}])
        short_guard.assert_sell_is_covered(client, "1001", 10, "trail")
        short_guard.invalidate_position_cache()
        short_guard.assert_sell_is_covered(client, "1001", 10, "trail")

        assert client.get_positions.call_count == 2

    def test_zero_quantity_is_a_noop(self):
        from deltalock_extract.short_guard import assert_sell_is_covered
        client = MagicMock()
        assert_sell_is_covered(client, "1001", 0, "noop")
        client.get_positions.assert_not_called()
