# Extracted from the private DeltaLock repository: backend/tests/test_never_short_2026_08_17.py at commit 20f6b7f (4 Sep 2026).
# Changes: only the TestExitPriceResolver class is kept, in its own file; import path; the real instrument, security id, quantities and prices replaced by illustrative values; one test renamed to drop the real strike; a module docstring added for this file.
"""
Exit price from broker truth, never a blended position average (2026-08-17).

The resolver against the shape of the 17-Aug case, with illustrative
numbers: the same security sold at 80.00 and at 120.00 in one session, so
the position's sellAvg of 100.00 belongs to neither trade.

No test in this file makes a real network call.
"""
from unittest.mock import MagicMock


class TestExitPriceResolver:
    """The resolver itself, against the shape of the 17-Aug case."""

    def _client(self, order=None, position=None):
        c = MagicMock()
        c.get_order_details.return_value = order or {}
        c.get_position_for_security.return_value = position or {}
        return c

    def test_the_17_aug_blend_is_refused(self):
        """Sold at 80.00 and 120.00 on the same security; sellAvg = 100.00."""
        from deltalock_extract.exit_price_resolver import resolve_exit_price
        price, source = resolve_exit_price(
            self._client(position={"sellAvg": 100.0, "sellQty": 20}),
            "1001", 10, [],
        )
        assert price is None
        assert source == "UNRESOLVED_BLENDED_POSITION"

    def test_order_truth_wins_over_position(self):
        from deltalock_extract.exit_price_resolver import resolve_exit_price
        price, source = resolve_exit_price(
            self._client(
                order={"orderStatus": "TRADED", "averageTradedPrice": 120.0},
                position={"sellAvg": 100.0, "sellQty": 20},
            ),
            "1001", 10, ["SL-ORDER-1"],
        )
        assert price == 120.0
        assert source.startswith("ORDER_TRUTH")

    def test_untraded_order_is_not_used(self):
        from deltalock_extract.exit_price_resolver import resolve_exit_price
        price, source = resolve_exit_price(
            self._client(order={"orderStatus": "CANCELLED", "averageTradedPrice": 0}),
            "1001", 10, ["SL-ORDER-1"],
        )
        assert price is None

    def test_unreachable_broker_returns_none_not_a_guess(self):
        from deltalock_extract.exit_price_resolver import resolve_exit_price
        c = MagicMock()
        c.get_order_details.side_effect = Exception("api down")
        c.get_position_for_security.side_effect = Exception("api down")
        price, source = resolve_exit_price(c, "1001", 10, ["X"])
        assert price is None
        assert source == "UNRESOLVED_BROKER_UNREACHABLE"
