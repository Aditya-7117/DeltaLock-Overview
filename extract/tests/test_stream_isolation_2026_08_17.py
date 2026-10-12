# Extracted from the private DeltaLock repository: backend/tests/test_stream_isolation_2026_08_17.py at commit 20f6b7f (4 Sep 2026).
# Changes: only the tests of stream_fields itself are kept (the others scan the reconciler, the order-update handler and the dashboard source, which are not in this extract); stream names and field prefixes replaced by the neutral labels used in stream_fields.py, with the test names to match; real trade ids, stop levels and P&L replaced by illustrative values; an internal document reference and an unused variable removed; dashes replaced.
"""
Stream isolation: BASE / V1 / V2 / V3 must never bleed.

Exactly one stream ever touches live money:

    BASE        paper ONLY   current_sl, pnl, net_pnl, exit_price
    V1          paper + LIVE v1_*            <-- the only live stream
    V2          paper ONLY   v2_*
    V3          paper ONLY   v3_*

The failure mode is a NAMING error, not a logic error. `current_sl` and
`pnl` read like "the trade's SL" and "the trade's P&L", so they get
reached for by reflex, but they belong to the paper-only BASE stream.
A live path that uses them places real orders, or reports real money,
from a simulation.

Both halves were live on 2026-08-17:

  - Three live SL placement paths selected the broker trigger as
    `current_sl or sl`. On one live trade the BASE stream had trailed to a
    different level from V1, so a restore would have put the REAL broker
    stop wider than the stream that owns it.
  - The dashboard engine panel rendered `t.pnl` regardless of the
    selected stream, showing a paper profit for a trade whose live result
    was a loss.

The numbers below are illustrative. No test in this file makes a real
network call.
"""
import pytest
from unittest.mock import MagicMock


def _state(**kw):
    s = MagicMock()
    s.trade_id = kw.pop("trade_id", "EXT-STREAM-LIVE")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# =====================================================================
# 1. LIVE SL TRIGGER SELECTION
# =====================================================================

class TestLiveSLTriggerNeverUsesBaseStream:

    def test_v1_level_is_selected(self):
        from deltalock_extract.stream_fields import live_sl_trigger
        s = _state(v1_current_sl=110.0, current_sl=104.0, sl=60.0)
        assert live_sl_trigger(s) == 110.0

    def test_the_17_aug_divergence(self):
        """THE MEASURED CASE, in shape. BASE had trailed to one level while
        V1 held another. The live broker order belongs to V1."""
        from deltalock_extract.stream_fields import live_sl_trigger
        s = _state(v1_current_sl=110.0, current_sl=104.0, sl=60.0)
        got = live_sl_trigger(s)
        assert got != 104.0, (
            "selected the paper-only BASE stream for a LIVE broker order "
            "- this would have restored the real stop wider"
        )
        assert got == 110.0

    def test_falls_back_to_signal_sl_never_to_base(self):
        """With no V1 level yet, the original signal stop is the only
        safe floor. BASE's trailed value must never be used."""
        from deltalock_extract.stream_fields import live_sl_trigger
        s = _state(v1_current_sl=None, current_sl=104.0, sl=60.0)
        got = live_sl_trigger(s)
        assert got == 60.0
        assert got != 104.0

    def test_returns_default_when_nothing_is_known(self):
        from deltalock_extract.stream_fields import live_sl_trigger
        s = _state(v1_current_sl=None, current_sl=104.0, sl=None)
        assert live_sl_trigger(s, default=None) is None

    def test_zero_and_garbage_v1_values_do_not_win(self):
        from deltalock_extract.stream_fields import live_sl_trigger
        for bad in (0, 0.0, None, "abc"):
            s = _state(v1_current_sl=bad, current_sl=104.0, sl=60.0)
            assert live_sl_trigger(s) == 60.0


# =====================================================================
# 2. PER-STREAM P&L
# =====================================================================

class TestStreamNetPnlNeverReturnsAnotherStream:

    def _full(self):
        return _state(
            net_pnl=1500.0, pnl=1500.0,          # BASE (paper)
            v1_net_pnl=-80.0, v1_pnl=-40.0,
            v2_net_pnl=11.0, v2_pnl=12.0,
            v3_net_pnl=22.0, v3_pnl=23.0,
        )

    @pytest.mark.parametrize("stream,expected", [
        ("V1", -80.0),
        ("V2", 11.0),
        ("V3", 22.0),
        ("BASE", 1500.0),
    ])
    def test_each_stream_returns_its_own_number(self, stream, expected):
        from deltalock_extract.stream_fields import stream_net_pnl
        assert stream_net_pnl(self._full(), stream) == expected

    def test_the_17_aug_confusion(self):
        """A V1-mode panel showed BASE's paper profit for a trade whose
        live result was a loss."""
        from deltalock_extract.stream_fields import stream_net_pnl
        s = self._full()
        assert stream_net_pnl(s, "V1") == -80.0
        assert stream_net_pnl(s, "V1") != stream_net_pnl(s, "BASE")

    def test_missing_stream_result_returns_none_not_another_stream(self):
        """A stream with no recorded result must render as unknown, never
        borrow a sibling's number."""
        from deltalock_extract.stream_fields import stream_net_pnl
        s = _state(net_pnl=1500.0, pnl=1500.0,
                   v1_net_pnl=None, v1_pnl=None)
        assert stream_net_pnl(s, "V1") is None

    def test_net_is_preferred_over_gross(self):
        from deltalock_extract.stream_fields import stream_net_pnl
        s = _state(v1_net_pnl=-80.0, v1_pnl=-40.0)
        assert stream_net_pnl(s, "V1") == -80.0

    def test_unknown_stream_name_raises_rather_than_guessing(self):
        from deltalock_extract.stream_fields import stream_net_pnl
        with pytest.raises(ValueError):
            stream_net_pnl(self._full(), "V4")

    def test_v1_and_v2_are_never_aliased(self):
        """V2 is a paper-only forward-test stream. Confusing it with V1
        would report forward-test numbers as live results."""
        from deltalock_extract.stream_fields import stream_net_pnl
        s = self._full()
        assert stream_net_pnl(s, "V1") != stream_net_pnl(s, "V2")


# =====================================================================
# 3. THE CONTRACT ITSELF
# =====================================================================

class TestNoCrossStreamContaminationAnywhere:

    def test_each_stream_has_a_distinct_field_namespace(self):
        """Sanity on the contract itself: no two streams may share a
        P&L field name, or selection can never be unambiguous."""
        from deltalock_extract.stream_fields import stream_net_pnl
        seen = {}
        for stream in ("BASE", "V1", "V2", "V3"):
            # Give this stream a unique value; all others unset.
            probe = MagicMock()
            for f in ("net_pnl", "pnl", "v1_net_pnl", "v1_pnl",
                      "v2_net_pnl", "v2_pnl", "v3_net_pnl", "v3_pnl"):
                setattr(probe, f, None)
            marker = 1000 + len(seen)
            field = {"BASE": "net_pnl", "V1": "v1_net_pnl",
                     "V2": "v2_net_pnl", "V3": "v3_net_pnl"}[stream]
            setattr(probe, field, marker)
            got = stream_net_pnl(probe, stream)
            assert got == marker, f"{stream} did not read its own field"
            seen[stream] = marker

        assert len(set(seen.values())) == 4
