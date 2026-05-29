# PC Meeting Scheduler

Schedules PC meeting discussion sessions from HotCRP reviewer assignments and
availability preferences (e.g. from Xoyondo), taking into account reviewer
availability, paper tags, and session-length constraints.

## Quick start

```bash
./pc-meeting-scheduler -d <input-directory>
```

The script is self-contained: on first run it creates a `.venv/`, installs
dependencies, then runs. Point it at a directory containing your input files
and it will auto-detect them by filename.

```bash
# Typical invocation with explicit files
./pc-meeting-scheduler \
  -a assignments.json \
  -s Xoyondo.csv \
  -c config.yaml

# Run all algorithms and write an HTML report
./pc-meeting-scheduler -d inputs/ -A all --html schedule.html --html-details
```

## Inputs

| Input | Flag | Auto-detected by |
|---|---|---|
| HotCRP assignments JSON | `-a` / `--assignments` | filename contains `rqc`, `hotcrp`, `assignments`, or `reviews` |
| Scheduling preferences CSV | `-s` / `--schedule` | filename contains `xoyondo`, `schedule`, `preferences`, `when2meet`, or `doodle` |
| Configuration YAML or JSON | `-c` / `--config` | filename contains `config`, `settings`, or `options` |

**Assignments**: In HotCRP, go to *Download → JSON for reviewqualitycollector.org*.

**Scheduling preferences**: Export the Xoyondo poll as CSV. Each row is a
reviewer; each column is an hour slot. Cells contain `Yes`, `No`, or `Maybe`.

**Config**: See [Configuration](#configuration) below. If no config is found in
the input directory, `config.yaml` in the project root is used as a default.

## Command-line options

```
./pc-meeting-scheduler [options]

Input:
  -d DIR, --directory DIR      Scan DIR for input files (auto-detection)
  -a FILE, --assignments FILE  HotCRP JSON export
  -s FILE, --schedule FILE     Scheduling preferences CSV
  -c FILE, --config FILE       Configuration YAML or JSON

Output:
  --csv FILE                   Write schedule to CSV (use - for stdout)
  --html FILE                  Write schedule to HTML (use - for stdout)
  --html-details               Include reviewer attendance & skipped papers in HTML
  --color {always,auto,never}  Terminal color output (default: auto)

Verbosity:
  -v, --verbose                Show loading and auto-detection messages;
                               sets --reviewer-report default to 'summary'
  --reviewer-report {full,summary,none}
                               Reviewer matching report detail
                               (default: summary if -v, none otherwise)

Algorithm:
  -A {greedy,session-first,hill-climbing,genetic,all}
                               Scheduling algorithm (default: from config or 'greedy')
                               Use 'all' to run every algorithm in sequence
```

## Configuration

Create a `config.yaml` (or copy the project-root `config.yaml` as a starting
point). Tags are matched by prefix against HotCRP tag names — the tool strips
the `#weight` suffix, so `pre-accept` in the config matches `pre-accept#0` in
the assignments JSON.

```yaml
# Papers excluded from scheduling (decided before the meeting).
skip_tags:
  - pre-accept
  - pre-reject

# Papers flagged in the schedule report for extra attention.
attention_tags:
  - pay-attention

# One-shot revision papers (flagged in output, not excluded).
one_shot_tags:
  - one-shot

# Minutes per paper (determines session capacity: session_length / minutes_per_paper).
minutes_per_paper: 15

# Minimum matched reviewers that must be available for a slot to be used.
# Reviewers not found in the schedule are ignored.
min_reviewers_per_slot: 3

# Length of each session in minutes.
session_length: 120

# Preferred minimum papers per session.
# Sessions below this are flagged; algorithms prefer slots that can reach it.
min_papers_per_session: 4

# Scheduling algorithm: greedy | session-first | hill-climbing | genetic
algorithm: greedy

# Timezone shown in the HTML schedule output.
# Use "auto" to detect the local timezone automatically, or set an explicit
# string such as "Eastern Time (ET)" or "UTC".
timezone: auto
```

## Scheduling algorithms

| Algorithm | Description |
|---|---|
| `greedy` | Most-constrained-first: schedules papers with fewest viable slots first; uses one-step look-ahead to minimize total sessions. |
| `session-first` | Fills the most-popular slot (most papers viable) first, then repeats. Tends to pack sessions more densely when many papers share availability windows. |
| `hill-climbing` | Starts from multiple seeds (greedy + session-first + random), then hill-climbs each by trying all single-paper moves and pairwise swaps. Returns the best result across seeds. |
| `genetic` | Evolves a population of complete assignments using uniform crossover, repair, mutation, and deterministic niching. Seeds from greedy and session-first results. |

All population-based algorithms optimize a weighted fitness score that
prioritizes reviewer attendance above session count.

Use `-A all` to run every algorithm and compare results side-by-side.

## Output formats

**Terminal** (default): color-coded text report (ANSI colors when stdout is
a TTY; plain text when piped).

**CSV** (`--csv FILE`): one row per paper with columns Session, Paper ID,
Paper Title, Available Reviewers, Unavailable Reviewers, Missing Scheduling
Info.

**HTML** (`--html FILE`): self-contained single-file report with color-coded
session cards and reviewer counts. Add `--html-details` to include reviewer
attendance issues and the skipped-papers list.

## Project structure

```
pc-meeting-scheduler     Shell launcher (creates .venv, installs deps, runs main.py)
config.yaml              Default configuration (used when no config found in input dir)
README.md                This file
CLAUDE.md                AI assistant context

src/
  main.py                CLI entry point — argument parsing, input loading, orchestration
  config.py              Config dataclass
  colors.py              ANSI color constants (auto-detected from TTY)
  papers.py              Paper dataclass, HotCRP JSON parser, reviewer extraction
  reviewers.py           Reviewer dataclasses, name matching, reviewer report
  schedule.py            SchedulingPreferences dataclass, Xoyondo CSV parser
  scheduler.py           ScheduledPaper/Session/Result, SchedulingAlgorithm ABC,
                         shared scheduling utilities, text/CSV/HTML report writers
  schedulers/
    __init__.py          Re-exports all four scheduler classes
    population.py        Shared utilities for population-based algorithms
                         (Assignment type, fitness functions, seed generation)
    greedy.py            GreedyScheduler
    session_first.py     SessionFirstScheduler
    hill_climbing.py     HillClimbingScheduler
    genetic.py           GeneticScheduler

test/
  input1/                Anonymized synthetic test inputs (generated via tools/anonymize.py)
  input2/                ...
  input3/                ...

data/                    Real conference data — not for distribution
  <meeting>/             One subdirectory per meeting
    *-rqc.json           HotCRP assignments export
    *.csv                Scheduling preferences
    config.yaml          Meeting-specific configuration

tools/
  anonymize.py           Anonymize or generate synthetic test inputs
  README.md              Tool documentation
```
