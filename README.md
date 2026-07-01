# CandidateSync — Multi-Source Candidate Data Transformer

Merges candidate data from a Recruiter CSV, ATS JSON export, resumes (PDF/DOCX), and recruiter notes (TXT) into one clean, confidence-scored candidate profile — with a runtime-configurable output schema.

Design document: `Design Doc.pdf`

## Project structure

```
candidatesync/
├── config/
│   └── minimal_profile.json   # example runtime projection config
├── inputs/                     # sample input files
│   ├── ats_export.json
│   ├── corrupt_json.json       # intentionally malformed, for robustness testing
│   ├── recruiter_export.csv
│   ├── recruiter_notes.txt
│   ├── resume_priya_sharma.pdf
│   └── resume_vikram_singh.docx
├── outputs/                     # generated output JSON lands here
├── src/
│   ├── extractors/             # one module per source type
│   ├── normalizers/            # phone/date/country/email/skill normalizers
│   ├── pipeline/                # merge, confidence, projection, validation
│   ├── models.py
│   ├── run.py                   # CLI entry point
│   └── transformer.py           # pipeline orchestrator
└──  tests/                      # unit + integration tests
└── .gitignore
```

## Setup

```bash
git clone <your-repo-url>
cd candidatesync
pip install -r requirements.txt --break-system-packages   # or use a venv
```

## Running the pipeline

All commands are run from the repo root.

**Default schema** (full canonical output, no config):

```bash
python src/run.py \
  --inputs inputs/*.csv inputs/*.json inputs/*.pdf inputs/*.docx inputs/*.txt \
  --output outputs/default_output.json \
  --pretty
```

**Custom config** (field selection, renaming, normalization):

```bash
python src/run.py \
  --inputs inputs/*.csv inputs/*.json inputs/*.pdf inputs/*.docx inputs/*.txt \
  --config config/minimal_profile.json \
  --output outputs/custom_output.json \
  --pretty
```

All CLI flags:

```
--inputs / -i     One or more file paths or glob patterns (required)
--config / -c     Path to a runtime projection config JSON (optional)
--output / -o     Output JSON path
--pretty          Pretty-print the output JSON
--strict          Raise an error instead of warning on schema validation failure
--verbose / -v    Show per-source extraction progress
```

## Running tests

```bash
python tests/test_normalizers.py    # unit tests
python tests/test_pipeline.py        # integration tests
```

## Core design principles

- **Deterministic** — no LLMs. Extraction uses `pdfplumber`, `python-docx`, `csv`/`json`, and regex/rule-based parsing. Same inputs always produce the same output.
- **Never guess** — if a value can't be determined confidently, the output is `null`. Wrong-but-confident data is worse than honestly-empty data.
- **Explainable** — the canonical profile tracks `provenance` (source + method) and a `confidence` score, and the projection layer can include or omit them depending on the runtime configuration.
- **Robust** — a missing, empty, or malformed source file is logged and skipped; it never crashes the run.

## Candidate matching & merge policy (summary)

Identity matching uses strict precedence — each tier is only checked if the one above found nothing:
1. Normalized email
2. Normalized phone (only if no email)
3. Normalized name + company (only if the record has neither email nor phone at all)

Source priority: ATS JSON (0.90) > Recruiter CSV (0.75) > Resume (0.70) > Recruiter Notes (0.50). Names choose the most complete valid value. Other scalar fields use source priority. Arrays are merged with deduplication, while structured lists (experience, education) merge by natural key with gap-filling from lower-priority sources.

## Known limitations

- English-language sources only.
- No fuzzy name matching — exact normalized match only, to keep merges predictable and auditable.
- No persistent database — output is a JSON file per run.
- GitHub API source descoped in favor of deeper resume/notes coverage.

## Sample outputs

`output/default_output.json` and `output/custom_output.json` in this repo are the actual outputs produced by running the commands above on the bundled `inputs/` files.