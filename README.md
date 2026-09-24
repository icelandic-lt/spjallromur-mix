# spjallromur-mix

This repository provides **spjallromur-mix**, a pipeline for aligning and mixing the
dual-channel recordings in the [Spjallrómur](https://clarin.is/en/resources/spjallromur/)
conversational Icelandic corpus, and assembling the CLARIN deposit directory.

![Python](https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
[![CI Status](https://github.com/icelandic-lt/spjallromur-mix/actions/workflows/ci.yml/badge.svg)](https://github.com/icelandic-lt/spjallromur-mix/actions/workflows/ci.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22725063.svg)](https://doi.org/10.5281/zenodo.22725063)

## Overview

**spjallromur-mix** is part of the
[Icelandic Language Technology Programme](https://github.com/icelandic-lt/icelandic-lt).

- **Category:** [Tools / Data Curation](https://github.com/icelandic-lt/icelandic-lt/blob/main/doc/lr.md)
- **Domain:** Laptop/Workstation
- **Languages:** Python
- **Language Version/Dialect:**
  - Python: 3.11 - 3.12
- **Audience**: Developers, Researchers
- **Origins:** [Spjallrómur](https://clarin.is/en/resources/spjallromur/)

## Status
![Stable](https://img.shields.io/badge/Stable-green)

## System Requirements

- Operating system: Linux or macOS
- Python 3.11 - 3.12
- A local copy of the Spjallrómur CLARIN release and of the v2 transcript release
  (neither is redistributed with this repository)
- Roughly 10 GB of free disk space to assemble a full deposit

## Description

Each conversation is recorded locally on both sides of the call, producing two
separate mono WAV files. Independent soundcard clocks cause the recordings to drift
out of sync over time. This pipeline corrects for that drift, mixes the two channels
into a stereo WAV (Speaker A = left, Speaker B = right), and produces time-aligned
transcript JSON files including a merged transcript with speaker labels.

## Pipeline

**Stage 1 — Analysis (`analyse.py`)**: scans all sessions, measures clock drift,
and writes per-session parameter files and a corpus-level summary. No audio is produced.

**Stage 2 — Synthesis (`synthesise.py`)**: reads the parameter files from Stage 1
and produces, for each session, a mixed stereo WAV and corrected transcript files.
All 48 full conversations are processed; one session (`198f2863`) carries a
`quality_warning` in its `session_params.json` due to a probable sample rate
mismatch in the WAV header and the presence of multiple voices.

**Stage 3 — Deposit (`prepare_deposit.py`)**: assembles the complete CLARIN v2
deposit directory from the CLARIN source, the v2 transcript root, and the pipeline
outputs.

See [DESIGN.md](DESIGN.md) for full design documentation.

## Dependencies

```
librosa, resampy, soundfile, numpy, scipy
```

Install with:
```bash
pip install -r requirements.txt
```

## Usage

The pipeline uses two separate read-only input roots:
- `--corpus-root`: the original CLARIN release (WAV files)
- `--transcript-root`: the v2 GitHub release (forced-alignment JSON files)

```bash
# Stage 1: measure drift and write parameter files
python analyse.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output

# Stage 2: produce mixed WAV and aligned transcripts
python synthesise.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output

# Process a specific subset of sessions
python synthesise.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output \
    --sessions 2a07b3a7,01119679

# Stage 3: remap the manual transcriptions onto the mixed timeline
python align_manual_transcripts.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output

# Stage 4: assemble the CLARIN v2 deposit directory
python prepare_deposit.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output \
    --deposit-root /path/to/deposit
```

## Outputs

Each session folder in the pipeline output directory contains:
- `session_params.json` — drift measurements and correction parameters
- `<session_id>_mixed.wav` — stereo mixed audio
- `a_<session_id>_<age>_<gender>_aligned.json` — aligned transcript, Speaker A
- `b_<session_id>_<age>_<gender>_aligned.json` — aligned transcript, Speaker B
- `<session_id>_transcript_merged.json` — both speakers interleaved by time

The deposit also carries the corpus's manual transcriptions, in two forms:
`annotations/manual_transcripts.json` (the corpus original, copied verbatim, on
the original per-channel timeline) and `annotations/manual_transcripts_aligned.json`
(the same transcriptions remapped onto the mixed timeline). See
[DESIGN.md](DESIGN.md) and the generated `annotations/README.txt`.

## Corpus statistics

```bash
python stats/corpus_stats_table.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output
```

Prints computed statistics and emits a LaTeX table for the resource paper.

## Conversational analysis

```bash
python stats/conversational_analysis.py --output-root /path/to/output
```

Turn-taking analysis over the merged transcripts produced by Stage 2: turn
durations, speaking time, overlaps, gaps and turn-taking rates, reported both
corpus-wide and separately for Speaker A and Speaker B. Sessions carrying a
`quality_warning` are excluded. Writes `stats/conversational_stats.csv` (per
session) and `stats/conversational_stats_table.tex` (summary table for the
resource paper).

Statistics are derived from forced-alignment timestamps, so small alignment
errors propagate into the overlap and gap estimates.

## Citation

Archived on Zenodo. Cite the concept DOI to refer to the pipeline in general —
it always resolves to the latest version:

> [10.5281/zenodo.22725063](https://doi.org/10.5281/zenodo.22725063)

To pin a specific release, use its own DOI: `1.1.0` is
[10.5281/zenodo.22941439](https://doi.org/10.5281/zenodo.22941439) and `1.0.0` is
[10.5281/zenodo.22725064](https://doi.org/10.5281/zenodo.22725064). See
[DESIGN.md](DESIGN.md) for what changed between them.

## Licence

The pipeline code in this repository is licensed under the
[Apache License 2.0](LICENSE).

The Spjallrómur corpus data (available separately via CLARIN) is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
