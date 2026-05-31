# Tools

Utility scripts for the PC Meeting Scheduler.

## paper_schedule.py

Shows reviewer availability for one or more papers across all scheduling time
slots.  For each slot, it reports how many of the paper's reviewers can (Yes
or Maybe) and can't attend, and calls out reviewers who provided no
availability data at all.  Each paper is printed as a separate block.
Uses color when stdout is a terminal.

```bash
python tools/paper_schedule.py -d <input-dir> <paper-id> [<paper-id> ...]
python tools/paper_schedule.py -a rqc.json -s schedule.csv 42 97 175
```

Output for each paper and time slot:
- Per-reviewer availability (Yes / Maybe / No)
- Count of reviewers who can make it vs. who can't
- Reviewers flagged as "no available slots" (filled out the poll but always No)
- Reviewers flagged as "no availability data" (did not fill out the poll at all)

### Options

```
PAPER_ID [...]              One or more paper IDs to display
-d DIR, --directory DIR     Input directory (auto-detects assignments and schedule)
-a FILE, --assignments      HotCRP RQC JSON file
-s FILE, --schedule         Scheduling preferences CSV
-c FILE, --config           Configuration YAML or JSON
```

---

## anonymize.py

Generates anonymized test inputs from real HotCRP RQC JSON and Xoyondo CSV
files.  All identifying information is replaced with convincingly fake data;
only the fields the scheduler actually needs are kept.

**What is anonymized:**
- Paper IDs → sequential fake IDs
- Paper titles → generated academic-sounding titles
- Reviewer names and emails → fake names and `@<institution>.edu` addresses
- Author names, affiliations, abstracts, review text → stripped entirely
- Xoyondo meeting title → kept as-is; reviewer names mapped to same fake names

**What is preserved:**
- Document structure (number of papers, reviews per paper, time slots)
- Tag names (e.g. `pre-accept`, `pay-attention`) — tags are kept intact but
  redistributed across papers during the permutation step
- Availability structure (Yes/No/Maybe per reviewer per slot)

### Usage

```bash
# Auto-detect files in a directory
python tools/anonymize.py -d test/input1/ -o anonymized/

# Explicit files
python tools/anonymize.py \
  -a path/to/rqc.json \
  -s path/to/Xoyondo.csv \
  -o anonymized/

# Reproducible output (same seed → same fake names and permutations)
python tools/anonymize.py -d test/input1/ -o anonymized/ --seed 42

# Pure structural anonymization — no permutations
python tools/anonymize.py -d test/input1/ -o anonymized/ --no-permute
```

### Options

```
-d DIR, --directory DIR    Input directory (auto-detects assignments and schedule)
-a FILE, --assignments     HotCRP RQC JSON file
-s FILE, --schedule        Xoyondo CSV file
-c FILE, --config          Config YAML/JSON — only tags listed here are kept
-o DIR, --output DIR       Output directory (default: ./anonymized/)
--generate                 Statistical generation mode (see below)
--permute / --no-permute   Enable/disable random permutations in anonymize mode (default: on)
--seed N                   Random seed for reproducibility (printed on every run)
```

### Statistical generation mode (`--generate`)

When `--generate` is passed, the tool extracts statistical properties from the
input files and generates a completely fresh synthetic dataset — no real paper
IDs, titles, reviewer names, or availability patterns are preserved.

```bash
# Derive stats from real inputs, generate synthetic output
python tools/anonymize.py -d test/input1/ --generate -o generated/

# Generate from built-in defaults (no input files needed)
python tools/anonymize.py --generate -o generated/

# Reproducible generation
python tools/anonymize.py -d test/input1/ --generate --seed 42 -o generated/
```

Statistics extracted from the input (or defaults if omitted):

| Property | Description |
|---|---|
| Paper count | Total number of papers |
| Reviewer count | Unique reviewers in assignments |
| Schedule reviewer count | Reviewers in the scheduling poll |
| Reviews-per-paper distribution | Empirical histogram used for sampling |
| Tag rates | Fraction of papers with each config tag |
| Days × slots | Schedule grid dimensions |
| Yes / Maybe rate | Per-slot availability density |

Generated output uses fictional dates (January 2030) and is safe to share
without any anonymization concerns.

### Permutations in anonymize mode (enabled by default)

When `--permute` is on the tool applies these random changes to increase
dataset variety:

- **Tag redistribution**: shuffles which papers carry which tags, preserving
  the total count of each tag across all papers.
- **Reviewer reassignment**: randomly swaps ~15% of reviewer assignments
  between papers.
- **Availability fuzzing**: randomly changes ~15% of each reviewer's slot
  values by one step (Yes → Maybe, Maybe → No, etc.).
- **Reviewer order**: shuffles the order of reviewer rows in the CSV output.

Use `--seed N` to reproduce a specific permutation.
