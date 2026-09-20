# System invariants

The private repository keeps a single file of invariants that the system may never violate. It
is the first document I open when changing anything in the execution path, and most of the
regression suite exists to hold one of these lines.

The engineering invariants are reproduced below. Invariants covering entry trigger conditions,
guard thresholds and stop-management methodology are held privately, because those describe the
trading behaviour rather than the system.

## State

- The execution registry is the single source of truth. No component mutates trade state
  outside it.
- `mark_open` is idempotent. Calling it twice on the same trade is a no-op, not a second entry.
- `mark_closed` is idempotent, on the same terms.
- Illegal transitions are rejected, not tolerated. A trade cannot move from CREATED to CLOSED
  without passing through OPEN.

## Mode

- Paper mode must never place a real order.
- Live mode must never simulate an entry.
- There is no silent mode switching in either direction. The live gate is explicit and
  human-controlled.

## Execution variants

- Every signal runs through three execution variants in parallel.
- All three always run in paper mode.
- Exactly one variant is ever permitted to touch live capital.
- No variant may be disabled, removed or merged into another. Their isolation is what makes the
  comparison between them meaningful.

## Authority

- Entry decisioning exists in one place only, in the Python layer.
- The Go engine is the final authority on exit price. Python does not override it.
- Routing is explicit. No component routes by catching an exception.

## Events and durability

- Every event is persisted before it is processed, never after.
- Every event is idempotent.
- Every event is replay-safe, so recovery is a replay rather than a reconstruction.

## Subscriptions

- A trade that is active must be subscribed to market data.
- An instrument stays subscribed until every variant on it has closed.
- No early unsubscribe. This invariant exists because breaking it once left a variant tracking
  a position it could no longer see.

## Session

- All trades are force-exited ahead of the session close.
- All instruments are unsubscribed at the close, and feeds stop.
