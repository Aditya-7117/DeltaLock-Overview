# Durability and idempotency

Two processes drive one position: a Python layer that owns entry and state, and a Go engine that
owns exits. That split buys latency where it matters and costs a correctness problem, because
either process can die between deciding something and recording it.

The answer is an append-ahead log. Nothing is processed before it is written.

## The write path

Quoted from the private repository, `backend/execution/durable_events.py`. The identifier
pattern immediately above `append` is omitted here because it encodes the producer namespace.

```python
class DurableEventStore:
    """Thread-safe append-ahead log for Go engine events with MVCC support."""

    @classmethod
    def append(cls, payload: dict) -> str:
        """
        Initial persistence of an event BEFORE processing.
        Python is a strict consumer; event_id must be provided by source.
        """
        event_id = payload.get("_event_id")
        cls.validate_identity(event_id)

        with cls._lock:
            # 1. IDEMPOTENCY & COLLISION DETECTION
            if event_id in cls._seen_ids:
                existing_payload = cls._seen_ids[event_id]

                # Deep Equality Check
                if payload != existing_payload:
                    log.critical("[EVENT_ID_COLLISION] event_id=%s", event_id)
                    raise RuntimeError(f"Event ID collision detected: {event_id}")

                log.info("[DUPLICATE_SKIPPED] event_id=%s", event_id)
                return event_id

            # 2. INITIALIZE V1
            ts = int(time.time() * 1000)
            payload["_durable_ts"] = ts
            payload["_v"] = 1
            payload["execution_status"] = {"paper": "PENDING", "live": "PENDING"}

            try:
                with open(_EVENT_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, cls=_SafeEncoder) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
```

Four decisions are worth pointing at.

**`os.fsync` and not just `flush`.** A flush moves the bytes to the operating system. Only the
fsync moves them to the disk. Without it the log survives a process crash but not a machine
crash, which is the case the log exists for.

**A redelivered event returns, it does not reapply.** The same event id arriving twice is the
normal case, not the exceptional one, because the producer retries when it is unsure.

**The same id carrying different content raises rather than overwrites.** If an id is reused for
a genuinely different event, the assumption the whole system rests on is broken, and the loud
failure is the correct one. Quietly accepting it would corrupt the replay.

**Event ids come from the producer, not from the consumer.** Python is a strict consumer here. A
consumer that mints its own ids cannot recognise a retry, because the retry would arrive with a
new id.

## Idempotent state transitions

The log guarantees each event is applied once. The registry makes that guarantee harmless if it
ever fails, by making the transitions themselves idempotent. From `backend/execution/state.py`:

```python
    def mark_open(cls, trade_id: str, fill_price: float, filled_qty: int = 0):
        with cls._lock:
            state = cls._trades.get(trade_id)
            if not state:
                return
            if state.status == ExecutionStatus.OPEN:
                return
            if state.status == ExecutionStatus.CLOSED:
                return

            state.status = ExecutionStatus.OPEN
```

A second fill event for a trade that is already open does nothing. A fill event for a trade that
has already closed does nothing, rather than resurrecting it.

## Partial fills

`mark_open` takes the broker-confirmed filled quantity rather than trusting the quantity the
trade was registered with. The docstring in the private repository explains why, and it is the
clearest example in the codebase of a bug found by operating the system rather than by reading
it:

> Accept the broker-confirmed filled_qty and atomically persist it as state.qty. Without this, a
> partial entry fill leaves state.qty pointing at the registration qty while the broker holds a
> different number of shares, every downstream modify_order then carries the wrong qty and
> either gets rejected or fires SL on a non-existent share count, leaving the position naked.

A naked position is one with no protective stop attached. The failure is silent at the moment it
happens and expensive later, which is the combination worth engineering against.
