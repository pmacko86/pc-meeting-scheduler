@README.md

## Development Notes

Run the tool against the test data:
```
./pc-meeting-scheduler -d test/input1
```

Utility scripts live in `tools/` and are documented in `tools/README.md` — do not mention them in the main `README.md`.

Keep `README.md` up to date: when adding or removing command-line options, config keys, algorithms, source files, or output formats, update the relevant section of `README.md` in the same change.

Key conventions:
- Do not use real reviewer or author names in code comments or docstrings — use clearly fictional names (e.g. "Jane Smith", "Alex Chen") to illustrate examples.
- Source lives in `src/`; schedulers are under `src/schedulers/`.
- Shared scheduling infrastructure (data structures, ABC, helpers, report) is in `src/scheduler.py`; population-based utilities shared by hill-climbing and genetic are in `src/schedulers/population.py`.
