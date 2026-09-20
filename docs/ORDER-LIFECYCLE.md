# Order lifecycle

What happens to a signal between arriving and being closed. Trigger conditions and stop
methodology are excluded throughout: this document is the shape of the lifecycle, not the rules
that drive it.

## State machine

Every trade lives in the execution registry as one explicit state. The registry is the only
component allowed to move it.

```mermaid
stateDiagram-v2
    [*] --> CREATED: signal extracted and validated
    CREATED --> CANCELLED: pre-entry guard rejects
    CREATED --> OPEN: entry filled, mark_open(fill_price, filled_qty)
    OPEN --> CLOSED: exit filled, mark_closed(price, reason)
    OPEN --> CLOSED: session force exit
    CANCELLED --> [*]
    CLOSED --> [*]

    note right of CREATED
        CREATED to CLOSED is rejected.
        A trade cannot close without
        having opened.
    end note

    note right of OPEN
        mark_open is idempotent.
        A duplicate fill event returns
        without opening a second position.
    end note
```

Three states and two terminal ones is deliberate. Every extra state in an execution path is a
transition somebody has to reason about during a live incident.

## Sequence

The path a single signal takes, with the parallel variants collapsed into one lane for
readability.

```mermaid
sequenceDiagram
    autonumber
    participant SRC as External intelligence source
    participant EXT as LLM extraction and guardrails
    participant REG as Execution registry
    participant PY as Python entry layer
    participant GO as Go exit engine
    participant BRK as Broker
    participant LOG as Durable event log

    SRC->>EXT: unstructured natural-language alert
    EXT->>EXT: parse to schema, reject duplicate, malformed or low-confidence
    EXT->>REG: validated trade intent
    REG->>REG: resolve instrument, subscribe, register three variants
    REG->>PY: arm entry

    Note over PY: pre-entry guards run before any order

    PY->>BRK: entry order (live variant only, gate armed)
    BRK-->>PY: fill confirmation with filled quantity
    PY->>REG: mark_open(fill_price, filled_qty)
    REG->>GO: hand exit authority to the Go engine

    loop while position is open
        BRK-->>GO: market data ticks
        GO->>BRK: stop maintenance
        GO->>LOG: append event before processing
        LOG-->>REG: replay-safe event applied once
    end

    GO->>BRK: exit order
    BRK-->>GO: exit fill
    GO->>REG: mark_closed(price, reason)
    REG->>REG: unsubscribe only when all three variants have closed
```

## Why authority is split

Entry decisioning exists only in Python, where correctness and readability matter most. Exit is
owned by a separate Go service, because responding to tick data at a consistent latency is the
part that cannot afford an interpreter pause. The registry arbitrates: it hands exit authority
over at fill, and from that point the Go engine is the final authority on exit price. Python
does not override it.

The cost of that split is that two processes share one truth. The durability model in
[DURABILITY.md](DURABILITY.md) is what makes that safe.

## Crash recovery

Recovery is a replay, not a reconstruction. Every lifecycle event is appended to a durable log
before it is processed, so a process that dies mid-trade restarts by replaying the log into the
registry and arrives at the same state it held before. Because each event is idempotent, an
event that was written but only partly processed is applied exactly once on replay.

## Session end

All trades are force-exited ahead of the session close, and every instrument is unsubscribed at
the close. Force exit overrides the normal exit path. An unattended system that can hold a
position past the close is a system that can be surprised overnight.
