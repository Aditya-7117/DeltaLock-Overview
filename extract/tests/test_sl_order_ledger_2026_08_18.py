# Extracted from the private DeltaLock repository: backend/tests/test_sl_rejection_visibility_2026_08_18.py at commit 20f6b7f (4 Sep 2026).
# Changes: only the TestSLOrderLedger class is kept (the other class exercises the order-update handler, which is not in this extract); import paths; real order ids and the trade id replaced by placeholders; the docstring tells the incident without prices or the margin figure, and drops the day's order book; dashes replaced.
"""
An SL rejection must never be invisible, 2026-08-18.

WHAT HAPPENED. Both live trades had their INITIAL stop loss rejected by
Dhan RMS seconds after the entry filled ("insufficient funds"): Dhan's
position API already showed the long, but its MARGIN ledger had not yet
linked the covering buy, so a routine protective sell was priced as a
naked short and refused. A retry
succeeded both times and trailing then ran normally.

Our logs held NO record of either rejection. dhan_order_ws routed
updates with `elif state.sl_broker_order_id == order_no`; by the
time each rejection arrived that field held the RETRY's id, so the
rejection matched nothing and fell through in silence.

The rejection is a broker timing artifact we cannot prevent, and the
retry path handles it. A protective order failing while the system
believes it is protected is the part that must never be silent.

No test in this file makes a real network call.
"""
import pytest


@pytest.fixture(autouse=True)
def _clean_ledger():
    from deltalock_extract import sl_order_ledger as led
    led.clear_sl_orders("EXT-REJ-LIVE")
    yield
    led.clear_sl_orders("EXT-REJ-LIVE")


class TestSLOrderLedger:

    def test_remembers_every_id_a_trade_owned(self):
        from deltalock_extract import sl_order_ledger as led
        for oid in ("ORD-INITIAL", "ORD-RETRY"):
            led.record_sl_order("EXT-REJ-LIVE", oid)

        assert led.known_ids("EXT-REJ-LIVE") == ["ORD-INITIAL", "ORD-RETRY"]
        assert led.owner_of("ORD-INITIAL") == "EXT-REJ-LIVE"

    def test_superseded_id_still_resolves_to_its_trade(self):
        """THE WHOLE POINT. The initial id is no longer current, but a
        rejection for it must still be attributable."""
        from deltalock_extract import sl_order_ledger as led
        led.record_sl_order("EXT-REJ-LIVE", "ORD-INITIAL")   # rejected
        led.record_sl_order("EXT-REJ-LIVE", "ORD-RETRY")     # retry, current

        assert led.owner_of("ORD-INITIAL") == "EXT-REJ-LIVE"

    def test_unknown_order_has_no_owner(self):
        from deltalock_extract import sl_order_ledger as led
        assert led.owner_of("ORD-UNKNOWN") is None

    def test_never_raises_on_junk(self):
        from deltalock_extract import sl_order_ledger as led
        led.record_sl_order(None, "1")
        led.record_sl_order("EXT-REJ-LIVE", None)
        assert led.owner_of(None) is None

    def test_per_trade_id_list_is_bounded(self):
        from deltalock_extract import sl_order_ledger as led
        for i in range(led._MAX_IDS_PER_TRADE + 20):
            led.record_sl_order("EXT-REJ-LIVE", f"ORD-{i}")
        assert len(led.known_ids("EXT-REJ-LIVE")) <= led._MAX_IDS_PER_TRADE

    def test_clear_forgets_the_trade_and_its_orders(self):
        from deltalock_extract import sl_order_ledger as led
        led.record_sl_order("EXT-REJ-LIVE", "ORD-A")
        led.clear_sl_orders("EXT-REJ-LIVE")
        assert led.known_ids("EXT-REJ-LIVE") == []
        assert led.owner_of("ORD-A") is None
