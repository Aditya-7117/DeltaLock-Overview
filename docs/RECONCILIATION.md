# Broker reconciliation and recovery

The registry records what DeltaLock believes it holds. The broker knows what it actually holds.
In live mode a reconciler thread compares the two on every cycle and repairs the difference.
Most of what it does was added after a live incident showed a gap, and the dates below are those
incidents. The reconciler's source stays private because it is written against Dhan's
API; the pieces that are not are in [`extract/`](../extract/).

## The loop

The API server starts the reconciler as a daemon thread. Each cycle:

1. Reaps finished trades out of the Go exit engine. This runs in paper and live mode, because Go
   holds paper trades too.
2. In live mode only, runs one reconciliation pass. A non-blocking lock skips the cycle if the
   previous pass is still running, so passes never overlap. An exception is audited as fatal for
   that pass, and the loop carries on.

## One reconciliation pass

The pass reads the broker's positions first. If that read fails, it does nothing this cycle
rather than reconcile against a guess. It then compares, security by security, what the broker
holds with the system's open live trades:

| Broker | Registry | Reading | Action |
|---|---|---|---|
| Long | No trade | An orphan. It may be a lost DeltaLock trade, or a position the operator holds in the same account for other reasons | Never flattened automatically, because acting on a position the system does not understand is the worse bug. A critical alert that escalates the longer the orphan persists |
| Flat | Open | A phantom: the trade was closed outside the system's view | The close is booked without placing any order (see below) |
| Long, other quantity | Open | Quantity drift | The registry's quantity and stop order id are synced to the broker's, through the registry's atomic helpers |

Every open live trade's protective stop is then checked at the broker. A missing, cancelled or
rejected stop is placed again, rate-limited per trade, or raised as a critical alert when
automatic resync is off. A filled stop means the position is already closing, so nothing is done.
A status the code does not recognise raises an alert and changes nothing. Trades under a manual
override are left alone.

## Phantom closes

A trade the registry shows open while the broker shows nothing is not closed at once. The
broker's position data can lag a fill by a few seconds, so a trade that has only just opened is
given a grace period, and the registry is read again to confirm the trade is still open.

The exit price then comes from the broker. This path used to close the trade at its stop or entry
price, a number the system made up for an exit it never saw. It now asks the broker through the
exit price resolver, falls back to an internal estimate only when the broker cannot answer, and
labels that estimate as one in the audit trail, so a fabricated close is never mistaken for a
measured one.

## Exit prices from broker truth

A broker position is netted per security, so its average sell price covers every sell in that
security for the day. On 17 August 2026 one option contract was traded twice, and the position
average, which belonged to neither trade, was written to both as their exit price. With
illustrative numbers: sold at 80.00 and at 120.00, the position says 100.00.

`resolve_exit_price()` now works in a fixed order:

1. The traded price of this trade's own exit order or stop order. Exact, and immune to netting.
2. The position average, but only when the position's sold quantity equals this trade's, so no
   other trade could have contributed to it.
3. Nothing. The trade is flagged for manual reconciliation. A wrong number that looks
   authoritative is worse than a missing one: the first is trusted, the second gets checked.

The resolver and its four tests are in [`extract/`](../extract/).

## Recovery steps in every live pass

- **Entries that filled while nobody was listening.** A trade still in CREATED whose broker entry
  order has filled is moved to OPEN, flagged as unprotected and audited. This covers a dropped
  WebSocket fill event and a crash between the fill and the state change. On 8 July 2026 this
  watchdog itself failed silently for 27 minutes: a broker call returned a list where a mapping
  was expected and the exception was swallowed. It now raises a visible audit event when its
  scans keep failing.
- **Positions without a stop.** Added after an incident on 7 May 2026. A trade whose protective
  stop failed to place is retried on every cycle, after a fresh check of the broker position. If
  it is still unprotected after a short timeout, the reconciler flattens it. If the flatten fails
  too, it raises a critical alert and keeps alternating stop and flatten attempts until the
  operator steps in.
- **Flattening that the exchange will accept.** The emergency flatten is a marketable limit order
  priced from the live last traded price, falling back to the stop or entry price only when no
  tick is available. On 20 August 2026 a reference taken from a stale stop put the limit outside
  the exchange's price band, and every flatten attempt was rejected.
- **Entry cancels that did not confirm.** If a guard killed an entry but the broker did not
  confirm the cancel at once, the reconciler retries until the order is final. Cancelled or
  rejected closes the trade; filled is flattened and then closed; still working is cancelled
  again.
- **Exit authority that did not reach Go.** If handing a filled trade to the Go engine fails, the
  broker stop is already in place, so the reconciler retries the hand-over every cycle and never
  flattens for it.

## Positions the broker closed itself

Dhan squares off open intraday derivative positions shortly before the close. On
4 September 2026 it cancelled DeltaLock's resting stop as part of that, the replacement stops were
rejected because intraday orders were no longer accepted, and the broker then closed the position
itself. When the system's own end-of-session exit ran, the never-short guard correctly refused to
sell a position that no longer existed. The exit path read that refusal as "naked" and
escalated, and the trade sat in the registry as open, with no exit price, while the broker had
booked a real fill.

The fix draws one distinction. "We hold a long and it is unprotected" is naked: an emergency.
"The broker says we hold nothing" means someone else closed it: the close is booked from the
broker's own trade book and no order is sent. Because the system is long-only, nothing held is
the only safe reading of a zero position. If the fill cannot be found, the exit price stays
unresolved rather than guessed.

## Trades Go was never told had finished

In live mode the Go engine does not end its live stream on its own simulated stop, because the
broker is the only exit authority for a live trade. So Go learns that a trade is finished only by
being told. The event-driven reap can miss: a dropped WebSocket message, a reconnect during the
fill, or a crash at the wrong moment. On 17 August 2026 a trade that closed in the morning was
still active in Go four hours later, processing every tick on its security, and the system slowed
as the session went on.

A sweep on every cycle now reaps any trade that is finished in Python but still active in Go, and
only when every stream on that trade has finished. The record of reaped trades is bounded,
because the process runs for weeks and an unbounded set is exactly the kind of slow leak that
turns into a late-session slowdown.

## Tests

The reconciler's own tests (`test_durability_hardening_2026_08_17.py`,
`test_incident_2026_07_15_live_exit.py`, `test_tick_path_starvation_2026_09_04.py` and the
phantom-close tests in `test_never_short_2026_08_17.py`) stay private, because they drive the
broker client. The exit price resolver's tests run in [`extract/`](../extract/).
