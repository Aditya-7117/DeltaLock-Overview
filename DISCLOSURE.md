# Disclosure

This repository is an architecture overview of a private system. The strategy logic is
deliberately excluded.

DeltaLock is a working automated execution system that trades real capital. What is published
here is the engineering: the architecture, the order lifecycle, the state and durability model,
the safety design, the test structure, and the incidents the system has actually hit. What is
not published is anything that would let a reader reproduce the trading behaviour.

## Published here

- Architecture and sequence diagrams, and component boundaries
- The order-lifecycle state machine, its states, transitions and invariants
- Idempotency key derivation and duplicate-event handling
- Write-ahead journaling and replay-based crash recovery
- The shadow-variant design and the human-controlled live gate
- The test tree: module names, counts, and what each module asserts
- Code excerpts quoted from the private repository, chosen because they carry no strategy
- A post-mortem of a real live-trading incident, including both root causes

## Never published, here or anywhere

- Signal generation logic of any kind
- Entry and exit trigger conditions, and the rules that produce them
- Stop-management methodology
- Feature engineering, tuned parameter values and model weights
- The identity of the external intelligence source
- Broker credentials, API keys, account identifiers, capital amounts and profit or loss
- Real instrument-level positions or trade history

## The line I drew

If a reader could reproduce the trading results from an artefact, it is strategy and it stays
private. If it only shows that the system is correct, recoverable and auditable, it is
engineering and it is published.

Some of this repository is therefore deliberately incomplete. Where a document stops short, it
says so rather than leaving the gap unexplained.
