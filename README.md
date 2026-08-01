# Ripple

Ripple treats a screenplay as a production database rather than a static document. It imports Fountain, Final Draft XML, PDF, and plain text, parses them into structured script units, extracts production entities and evidence-backed assertions into a graph, and shows the downstream production and continuity impact of a proposed edit before anything is applied.

The loop is: select a unit, inspect its graph, edit it, see the ripple, decide.

## Status

Import adapters and the demo corpus are implemented. The graph extraction, ripple preview, and web application are in progress.

## Requirements

- Python 3.11 or newer
- Optional: `tesseract` and `pdftoppm` (poppler) for OCR of scanned PDFs. Without them, scanned PDFs are rejected with a stated reason rather than silently mis-parsed.

## Install and run

```bash
./launch.command
```

This creates `tools/.venv`, installs the package with its development extras, and runs the test suite. It is safe to run repeatedly and works from a path containing spaces.

Manual equivalent:

```bash
python3 -m venv tools/.venv && tools/.venv/bin/pip install -e ".[dev]" && tools/.venv/bin/python -m pytest
```

## Importing a screenplay

```python
from ripple.adapters import import_screenplay

result = import_screenplay(open("script.fountain", "rb").read(), "script.fountain")
print(result.outcome, result.scene_count, result.unit_count)
for warning in result.warnings:
    print(warning.code, warning.message)
```

`import_screenplay` never raises for bad input. A file that cannot become a screenplay returns an `ImportResult` with `outcome` `REJECTED` and a `rejection_code`.

| Outcome | Meaning |
|---|---|
| `accepted` | Parsed with no findings |
| `accepted_with_warnings` | Parsed, with non-blocking findings recorded |
| `needs_review` | Parsed, but structure was inferred rather than read (OCR, no indentation, weak signal) |
| `rejected` | Not importable; `rejection_code` says why |

Format detection reads the bytes. A file extension is a hint that can break a tie between two text formats and never overrides a content signal.

## Demo corpus

`demo-scripts/` holds original screenplays written for this project, each authored in Fountain and rendered to Final Draft XML, PDF, and plain text by one tool:

```bash
tools/.venv/bin/python tools/render_screenplay.py
```

Each script ships a `dependencies.md` recording the entities, assertions, and planted dependency chains a correct extraction must produce.

## Tests

```bash
tools/.venv/bin/python -m pytest
```

Test-order randomisation is enabled. A failure that depends on order is a bug in shared state, not something to pin away.

## Licence

AGPL-3.0. See [LICENSE](LICENSE).

Built by Mo Shehu — mohammedshehu.com
