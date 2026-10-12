# Live safety

DeltaLock trades real money. This page covers what stands between a validated signal and a live
broker order: the split between paper and live, the live gate, order sizing, the never-short
wall, the stop-order ledger and stream isolation. Values such as limits, cutoffs and lot counts
stay private. Four of the mechanisms run in [`extract/`](../extract/) with their tests.

## Paper and live are separate trades

When live trading is armed, every incoming signal becomes two isolated trades in the registry: a
paper clone and a live clone. The paper clone always runs. Rules that apply only to real money,
such as entry cutoffs, are checked against the live clone alone, so a live block never stops the
paper record of the same signal.

Routing is decided per trade, not by the global mode. A paper clone opens and closes through the
paper engine even while live trading is armed; only a live trade goes through the live gate to
the live engine. Before that rule, the end-of-session force exit once sent a paper clone's close
to the live engine, which refused it as a safety violation.

Four execution streams run on every signal, and exactly one of them ever touches live money. The
fourth, added on 29 June 2026, is a paper-only forward test of a candidate replacement for the
live stream. Live authority stays with the current stream until the operator promotes the
candidate.

## The live gate

`assert_live_allowed()` runs before every live open and close, and checks in a fixed order:

1. The operator has armed live trading by hand.
2. The runtime profile allows live mode.
3. The broker session is valid.
4. A real authenticated call to the broker succeeds. This second layer fails closed: missing
   configuration or a failed call blocks live trading, and the block is audited.
5. The market is open.

The live routes check the session again before acting, and a live open request cannot set its
own quantity.

## Order sizing in one place

`PositionConfig.get_final_qty()` is the only place an order quantity is computed. It multiplies
the exchange's lot size for the index by a whole-number multiplier that the operator sets from
the dashboard, separately for paper and for live.

- A new multiplier applies to new trades only. Positions already open keep their size.
- A multiplier must be at least 1, and an unknown mode or index is refused.
- Index names that merely contain a supported name are refused rather than sized with the wrong
  lot.
- The multipliers persist through an atomic write (a temporary file, then a rename), so a crash
  mid-write cannot corrupt them, and on startup only valid values are loaded back.
- Reads and writes share a lock, so the dashboard never sees half of an update.

The live route sets the quantity itself from this configuration and overrides anything in the
request, so a malformed or replayed request cannot change the size of a live order.

## The never-short wall

DeltaLock only buys options and later sells what it bought. An uncovered sell on an index option
is a naked short, with unbounded loss that grows with size. So every SELL passes one guard,
`assert_sell_is_covered()`, at the single function every broker order leaves through: entries,
stop losses, exits, emergency flattens and reconciler restores alike. A future call site cannot
opt out, because there is nowhere else to go.

| Broker state | Decision |
|---|---|
| The long is already fully covered by resting SELL orders | Block |
| A long at least as large as the SELL | Allow |
| A long smaller than the SELL | Block |
| No long at all | Block: this is the naked short |
| Broker unreachable | Allow, with a critical log and an audit event |

The first line came from 18 August 2026, when two initial stop losses went out a second apart
against one long. Each passed the held-quantity check alone; together they were twice the
position. Dhan's risk system (RMS) refused the second only because the account lacked the margin
for a naked short, a protection that disappears as capital grows. Blocking there can never leave
a position naked, because the only thing that trips it is an order already protecting it.

The last line is the one judgement call. Blocking on an unreachable broker would mean an API
blip could stop the system placing a stop loss on a real long, swapping a bounded loss for an
unbounded one. So it allows, loudly. Positions and resting orders are cached for two seconds, so
stop trailing does not add a broker round-trip to every modification.

The guard and its 20 tests are in [`extract/`](../extract/). A wiring test in the private suite
fails if the guard is ever removed from the broker client's order path.

## A rejected stop is never silent

On the same day, both live trades had their first stop loss rejected by Dhan's risk system
seconds after entry, because its margin ledger had not yet linked the covering buy. A retry
worked both times. The logs held no trace of either rejection: the order-update handler matched
updates against the trade's current stop order id, and by then that was the retry's.

`sl_order_ledger` now remembers every stop order id a trade has owned, so a terminal status on a
superseded order is still attributed and audited. It is bounded per trade and per process. The
ledger and its 6 tests are in [`extract/`](../extract/).

## One stream may touch live money

Each stream keeps its own stop and its own profit and loss in separately named fields. The
fields with plain names, `current_sl` and `pnl`, belong to a paper-only stream, and on
17 August 2026 that naming caused two bugs: three live stop placement paths read the paper
stream's trailed stop, and the dashboard showed a paper profit for a trade whose live result was
a loss.

`stream_fields` is now the only way a live-money path reads a stream's values. The live stop
comes from the live stream, falls back to the signal's original stop, and never to the paper
stream's value. A request for an unknown stream raises instead of guessing, and a stream with no
result returns nothing rather than a sibling's number. In the extract the streams carry neutral
names, because the real ones describe how each stream manages exits. The module and its 12 tests
are in [`extract/`](../extract/).
