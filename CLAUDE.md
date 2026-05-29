@README.md

## Development Notes

Run the tool against the test data:
```
./pc-meeting-scheduler -d test/input1
```

Regenerate anonymized test inputs (fuzzing is on by default, producing
different paper/reviewer counts from the real data each time):
```
for i in 1 2 3; do
  python3 tools/anonymize.py -d data/fast27spring -c config.yaml \
    --generate --seed $((i * 9999)) -o test/input$i/
done
```

Utility scripts live in `tools/` and are documented in `tools/README.md` — do
not mention them in the main `README.md`.

## Keeping README.md current

Update `README.md` **in the same commit** as any of the following changes:

| Changed thing | Section to update |
|---|---|
| Command-line flag added / removed / renamed | **Command-line options** |
| Config key added / removed / renamed (e.g. `timezone`, `algorithm`) | **Configuration** — add the key with its comment |
| New scheduling algorithm | **Scheduling algorithms** table |
| Source file added / moved / removed | **Project structure** tree |
| `test/`, `data/`, or `tools/` layout changes | **Project structure** tree |
| New output format or output option | **Output formats** |

## Key conventions

- Use American English spelling throughout — code, comments, docs, and strings
  (e.g. "minimize" not "minimise", "color" not "colour", "anonymize" not "anonymise").
- Do not use real reviewer or author names in code comments or docstrings —
  use clearly fictional names (e.g. "Jane Smith", "Alex Chen").
- Source lives in `src/`; schedulers are under `src/schedulers/`.
- Shared scheduling infrastructure (data structures, ABC, helpers, report) is
  in `src/scheduler.py`; population-based utilities shared by hill-climbing
  and genetic are in `src/schedulers/population.py`.
- `config.yaml` in the project root is the default config used when no config
  is found in the input directory.
- Tools in `tools/` auto-activate the project venv via `os.execv`; they can be
  run with the system `python3` directly.
- **`data/` contains real conference data and must not be distributed or
  committed to a public repository.**  Anything under `test/` is synthetic and
  safe to share.
