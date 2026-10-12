# DeltaLock extract

Four safety modules copied from the private DeltaLock repository, with 42 of the tests that guard
them. They run on their own: no network, no broker, no third-party packages.

| Module | What it decides | Private source |
|---|---|---|
| `short_guard.py` | Whether a SELL may reach the broker: blocked when no long covers it, when it is larger than the long, or when resting sells already cover the long; allowed, loudly, when the broker cannot be read | `backend/broker_adapters/short_guard.py` |
| `exit_price_resolver.py` | A trade's exit price: its own order's traded price first, a position average only when nothing else could have blended into it, and otherwise nothing, so the trade is flagged for manual reconciliation | `backend/broker_adapters/exit_price_resolver.py` |
| `sl_order_ledger.py` | Which trade owned a stop order id, including ids a retry has since replaced, so a rejection of an old stop is still attributed | `backend/broker_adapters/sl_order_ledger.py` |
| `stream_fields.py` | Which stream's stop a live broker order uses, and which stream's profit and loss a view reports, with no fallback to another stream's numbers | `backend/execution/stream_fields.py` |

Every file comes from commit `20f6b7f` (4 September 2026), and its first two lines list exactly
what changed. In short:

- Security ids, quantities, prices, order ids and trade ids are placeholders or illustrative
  values. One lot is 10 here.
- Margin figures and P&L from real incidents are removed from docstrings and messages. The
  incidents are still told, in shape.
- The execution streams carry neutral names (BASE, V1, V2, V3), with field prefixes to match,
  because their real names describe how each stream manages exits.
- Only the test classes that exercise these modules are included. The rest of each test file
  drives the broker client, the reconciler or the dashboard, which stay private.
- `short_guard`'s audit hook imports a module that is not in this extract, so here it does
  nothing; the tests patch it where they check it.
- Comment and docstring punctuation, and one internal document reference.

## Run it

```bash
python -m pip install -e ".[test]"
python -m pytest
```

42 test functions (45 test cases), standard library only, Python 3.10 or later. GitHub Actions
runs them on every push.

## What is not here

No signal parsing, entry or exit rules, stop-management methodology, sizing values, instrument
lists, configuration or broker transport. The line is drawn in [DISCLOSURE.md](../DISCLOSURE.md).
