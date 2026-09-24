# DESIGN.md — Spjallrómur Channel Alignment and Mixing Pipeline

## Purpose

Spjallrómur conversations are recorded with each side of the call captured locally,
producing two separate mono WAV files (`speaker_a_convo_<session>.wav`,
`speaker_b_convo_<session>.wav`). Because the two recording devices use independent
clocks, their sample rates drift relative to each other over time. This drift is
assumed to be small and approximately linear within any single session, but
accumulates toward the end of longer recordings.

This pipeline aligns the two channels in time, mixes them into a single stereo WAV
file (A=left, B=right), and produces corrected transcript JSON files with all
timestamps remapped to the new shared timeline.

The pipeline is intended to be fully transparent and reproducible. All intermediate
parameters are written to disk so that the mixing step can be re-run independently
of the analysis step. All outputs — mixed audio, corrected transcripts, and
per-session parameter files — are released alongside the code.

---

## Architecture

The pipeline is divided into four independent stages.

### Stage 1 — Analysis (`analyse.py`)

Iterates over all sessions in the source corpus. For each session, it measures the
lengths of both channels, characterises the drift, and writes a per-session
parameter file. It also writes a corpus-level summary.

**No audio output is produced in Stage 1.**

### Stage 2 — Synthesis (`synthesise.py`)

Reads per-session parameter files produced by Stage 1. For each session, it applies
the correction specified in the parameter file to align the two channels, writes the
mixed stereo WAV, and writes corrected transcript JSON files. Stage 2 can be run
blindly on any session for which a valid parameter file exists.

### Stage 3 — Manual transcript alignment (`align_manual_transcripts.py`)

Added in `1.1.0`. Remaps the corpus manual transcriptions onto the mixed timeline, writing
`<output_root>/manual_transcripts_aligned.json`. Optional: Stage 4 warns and
continues if it has not been run.

Two things vary per transcript entry and are determined from the data rather than
assumed — which recording channel each turn belongs to (the `speaker_a` /
`speaker_b` labels are inverted in five of the 28 entries), and which timeline the
turn times refer to (most predate drift correction; a few were timed against an
already-corrected mix). Both are resolved by scoring turns against the
forced-alignment word timings. Turns on the resampled channel are scaled by
`resample_ratio`; reference-channel turns are never modified.

Each output entry carries an `alignment` block recording the decision, the scores
behind it, the ratio applied and a before/after agreement score.

### Stage 4 — Deposit (`prepare_deposit.py`)

Assembles the CLARIN v2 deposit directory from three read-only inputs: the CLARIN
corpus (`--corpus-root`), the v2 transcript release (`--transcript-root`), and the
Stage 1/2 pipeline outputs (`--output-root`). It mirrors the CLARIN recording
structure — `full_conversations/` (48 sessions) and `half_conversations/`
(6 sessions) — renames the CLARIN WAVs to the v2 stem convention, copies the v2
transcripts and pipeline outputs alongside them, and writes `metadata.tsv`, a
CC BY 4.0 `LICENSE`, `annotations/`, and `code/README.txt`.

The corpus manual transcriptions are copied as-is, keeping the original citable,
and the Stage 3 output is deposited beside them as
`annotations/manual_transcripts_aligned.json`. `annotations/README.txt` is
generated to explain the difference where a user of the deposit will find it. The corpus is expected to supply
`manual_transcripts.json` already anonymised; the pipeline does no anonymisation
of its own. `EXCLUDED_FILENAMES` names files that must never be
deposited, enforced at the single copy choke point and reported when they are
skipped.

---

## Directory Structure

### Audio input (`--corpus-root`, CLARIN release, read-only)

WAV files only. Both `full_conversations` and `half_conversations` exist under
this root; the pipeline processes `full_conversations` only.

```
<corpus_root>/spjallromur/data/full_conversations/
    <session_id>/
        speaker_a_convo_<session_id>.wav
        speaker_b_convo_<session_id>.wav
```

### Transcript input (`--transcript-root`, v2 aligned release, read-only)

Word-level forced-alignment JSON files. Age and gender suffixes vary per speaker;
files are discovered by globbing `a_<session_id>_*.json` and `b_<session_id>_*.json`
within each session folder.

```
<transcript_root>/full_conversations/
    <session_id>/
        a_<session_id>_<age>_<gender>.json
        b_<session_id>_<age>_<gender>.json
```

### Output

```
<output_root>/
    corpus_summary.json                                          ← written by Stage 1
    <session_id>/
        session_params.json                                      ← Stage 1
        <session_id>_mixed.wav                                   ← Stage 2
        a_<session_id>_<age>_<gender>_aligned.json               ← Stage 2
        b_<session_id>_<age>_<gender>_aligned.json               ← Stage 2
        <session_id>_transcript_merged.json                      ← Stage 2
```

---

## Drift Model

All sessions are processed using a single linear resample, regardless of drift
magnitude. The shorter channel is resampled to match the length of the longer
channel using a single linear stretch factor:

**Correction parameter:** `resample_ratio = len_longer_samples / len_shorter_samples`

Stage 1 computes natural gap thresholds in the drift distribution and records them
in `corpus_summary.json` as `proposed_thresholds`. These are **informational only**
and do not gate processing; no session is treated differently on their basis.
The `above_1pct_threshold` flag in each `session_params.json` marks sessions where
drift exceeds 1% so that reviewers can inspect those sessions without any being
silently dropped.

---

## Per-Session Parameter File (`session_params.json`)

Written by Stage 1; updated by Stage 2 with `resample_ratio` and `transcript_source`.
Sessions with known audio quality issues also receive a `quality_warning` field.

```json
{
  "session_id": "2a07b3a7",
  "duration_a_sec": 985.868,
  "duration_b_sec": 984.331,
  "delta_sec": 1.537,
  "drift_percent": 0.156,
  "above_1pct_threshold": false,
  "reference_channel": "b",
  "tier": "A",
  "resample_ratio": 1.00156138,
  "transcript_source": "v2",
  "pipeline_version": "1.0.0",
  "stage1_timestamp": "2026-05-13T00:00:00Z"
}
```

`reference_channel` is always the longer channel; the other is the target and is
resampled. `above_1pct_threshold` is informational only — all sessions are processed
regardless. `tier` is always `"A"`; the field is retained for schema compatibility
with earlier releases and carries no behavioural meaning.

---

## Reference and Target Convention

The **longer channel is always the reference**; its audio and timestamps are
unchanged. The **shorter channel is always the target**; it is warped to match the
reference timeline. This avoids truncating any content and requires remapping only
one transcript.

Both corrected transcripts share the same absolute timeline as the mixed stereo WAV.

---

## Stage 2 Synthesis Pipeline

For each session, Stage 2 executes the following steps in order:

1. **Determine alignment function** — read `session_params.json` to obtain the
   correction parameters for this session.

2. **Apply alignment function to the target signal** — warp the shorter channel's
   audio to match the reference channel's duration, producing an aligned signal.

3. **Apply alignment function to the target transcript** — remap all `startTime` and
   `endTime` values in the target transcript through the same function, producing an
   aligned transcript. Steps 2 and 3 are independent and may run in either order.

4. **Mix aligned signal with reference signal** — interleave the two mono signals as
   left (channel A) and right (channel B) of a stereo WAV, regardless of which is
   reference and which is target.

5. **Merge reference transcript and aligned transcript** — combine all segments from
   both transcripts, sorted by `startTime`, into a single merged transcript for the
   mixed signal.

---

## Transcript Remapping and Speaker Tagging

### Transcript sources and fallback

Stage 2 tries to load the v2 forced-alignment transcript for each speaker from
`--transcript-root/full_conversations/<session_id>/`. If no v2 file is found (e.g.
sessions not included in the v2 release), it falls back to the CLARIN transcript at
`--corpus-root/.../full_conversations/<session_id>/speaker_{a|b}_convo_<session_id>_transcript.json`.

The CLARIN format uses a `segments` → `words` hierarchy with `startTime`/`endTime`
keys. The fallback reader flattens this into the same word-list format used by v2
(with `start`/`end` keys), so the rest of the pipeline is format-agnostic.
`norm_word` is set to `null` for CLARIN-sourced words (no normalised form available).

The `transcript_source` field in `session_params.json` records which source was used
(`"v2"` or `"clarin"`). Output aligned transcript filenames follow the v2 convention
(`a_<session_id>_<age>_<gender>_aligned.json`) when source is v2, and
`a_<session_id>_aligned.json` when source is CLARIN.

### Transcript format (v2)

The v2 transcript JSON files contain a flat word list with forced-alignment
timestamps, with no segment grouping:

```json
{
  "metadata": {
    "age": "40-49",
    "gender": "female",
    "audio_duration": 664.704,
    "speaker": "a"
  },
  "words": [
    { "word": "Þá", "norm_word": "þá", "start": 2.1, "end": 2.37 },
    ...
  ]
}
```

### Per-speaker aligned transcripts

All `start` and `end` timestamps in the target transcript are remapped. The
reference transcript is copied unchanged except that both receive two additional
metadata fields: `audio_file` (pointing to the mixed WAV) and `audio_duration`
(updated to the reference channel duration). A `"speaker"` field (`"a"` or `"b"`)
is added to every word in both transcripts.

Remapping is a scalar multiplication:
```
t_new = t_old * resample_ratio
```

Original transcript files are never modified.

### Merged transcript (`<session_id>_transcript_merged.json`)

The merged transcript interleaves all words from both aligned transcripts, sorted
by `start` time. Each word retains its `"speaker"` field. The top-level structure is:

```json
{
  "session_id": "2a07b3a7",
  "audio_file": "2a07b3a7_mixed.wav",
  "recording_duration": 985.868,
  "words": [
    { "speaker": "a", "word": "Já,", "norm_word": "já", "start": 15.959, "end": 16.199 },
    { "speaker": "b", "word": "Ókei,", "norm_word": "ókei", "start": 21.632, "end": 21.722 },
    ...
  ]
}
```

---

## Corpus-Level Summary (`corpus_summary.json`)

Written by Stage 1 after processing all sessions. Contains:

- Per-session: `session_id`, `duration_a_sec`, `duration_b_sec`, `delta_sec`,
  `drift_percent`, `above_1pct_threshold`, `tier`
- Aggregate statistics: count, mean/std/max drift, and the number of sessions
  above the 1% drift threshold
- Proposed thresholds with rationale (informational only)

---

## Audio Output

- Format: WAV, stereo (Speaker A = left channel, Speaker B = right channel)
- Sample rate: inherited from source files (assumed 16 kHz; a warning is raised if
  A and B differ)
- Bit depth: 16-bit PCM
- Filename: `<session_id>_mixed.wav`

Resampling uses `librosa.resample` with `res_type='kaiser_best'` by default,
configurable to `'kaiser_fast'` for batch processing.

---

## Dependencies

```
librosa
soundfile
numpy
scipy
resampy
```

No other dependencies. Python 3.11 - 3.12.

`resampy` is required by the default `--res-type kaiser_best`; librosa treats it as
an optional extra, so it is pinned explicitly here.

---

## Configurables (command-line arguments)

All three stages accept:

| Argument | Default | Description | Used by |
|---|---|---|---|
| `--corpus-root` | (required) | Path to CLARIN corpus root (WAV files) | all |
| `--transcript-root` | (required\*) | Path to v2 transcript root; CLARIN used as fallback | all |
| `--output-root` | (required) | Path to output directory | all |
| `--deposit-root` | (required) | Path to the deposit directory to assemble | Stage 3 |
| `--sessions` | all | Comma-separated list of session IDs to process | Stage 2 |
| `--res-type` | `kaiser_best` | librosa resampling quality | Stage 2 |

\* Optional in `analyse.py` (not used for filtering); required in `synthesise.py`
and `prepare_deposit.py`.

`--corpus-root` and `--transcript-root` should both point at the release roots, not
at a `full_conversations/` subdirectory: Stage 2 resolves v2 transcripts relative to
`<transcript_root>/full_conversations/`, and Stage 3 needs `half_conversations/`
to be reachable under `<corpus_root>`.

---

## What Is Released

- `analyse.py`, `synthesise.py` and `prepare_deposit.py`
- `corpus_summary.json`
- Per-session `session_params.json` files
- Mixed stereo WAV files (`*_mixed.wav`)
- Per-speaker aligned transcript JSON files (`*_aligned.json`)
- Merged transcript JSON files (`*_transcript_merged.json`)
- The assembled CLARIN deposit directory (Stage 3 output)
- This `DESIGN.md`

Original source WAV files and original transcript JSON files are not redistributed
(they form part of the separately released Spjallrómur corpus).

---

## Known Session Anomalies

### `198f2863` — quality-flagged (probable sample rate mismatch)

Session `198f2863` is processed by the full pipeline but carries a `quality_warning`
in its `session_params.json`. The WAV header reports 16000 Hz but the audio was
likely recorded at approximately 12000 Hz. The pipeline reads and mixes the audio
at face value (no sample rate correction is applied), so the mixed output plays back
approximately 33% too fast and high-pitched. The recording also contains multiple
voices and audio artefacts.

No correction is applied deliberately: adding a resampling step would introduce
further artefacts, and leaving the audio at its recorded rate gives downstream
pipelines the raw signal to work with along with full documentation of the issue.

### Manual transcriptions are on a different timeline from the mixed audio

`annotations/manual_transcripts.json` carries human transcriptions for 21 of the
full conversations. It is deposited verbatim, and its turn times refer to the
original per-channel recordings, made before drift correction. Against
`<session_id>_mixed.wav` they run progressively out of step — averaging about 7 s
at the end of a conversation, exceeding 1 s in 14 of the 21 sessions, and reaching
~80 s for `2c1b4416` (4.4% drift).

Stage 3 addresses this by producing `annotations/manual_transcripts_aligned.json`,
which carries the same transcriptions on the mixed timeline. The original is kept
unmodified so it remains citable.

Two complications ruled out a blanket correction and are handled per entry:

- The `speaker_a` / `speaker_b` labels do not always correspond to channels a and
  b. Five of the 28 entries are inverted, all from the transcriber who diarised
  from a mixed recording.
- A minority of entries were timed against an already drift-corrected mix and need
  no correction; a uniform remap would have broken them.

Of the 28 entries, 24 were remapped and 4 correctly needed none. Agreement with
the forced alignment improved for every remapped entry and regressed for none. The
aligned timestamps are derived rather than hand-checked, and inherit the accuracy
of the forced alignment used to place them.

### `2a139f9b` — no v2 transcript

This session is present in the CLARIN release but was not included in the v2
forced-alignment release. Stage 2 falls back to the CLARIN transcript and records
`transcript_source: "clarin"` in `session_params.json`. The timestamps are the
original, unverified CLARIN ones.

---

## Releases

| Version | DOI | Contents |
|---|---|---|
| `1.0.0` | [10.5281/zenodo.22725064](https://doi.org/10.5281/zenodo.22725064) | Stages 1, 2 and the deposit stage. Produced the mixed audio, the aligned forced-alignment transcripts and the per-session parameter files. |
| `1.1.0` | [10.5281/zenodo.22941439](https://doi.org/10.5281/zenodo.22941439) | Adds `align_manual_transcripts.py` (Stage 3) and `annotations/manual_transcripts_aligned.json`. Stages 1 and 2 are unchanged and reproduce the same audio and transcripts byte for byte. |

To cite the pipeline in general, use the concept DOI
[10.5281/zenodo.22725063](https://doi.org/10.5281/zenodo.22725063), which always
resolves to the latest version. The per-version DOIs above pin a specific release.

`session_params.json` records `pipeline_version: "1.0.0"` because that data was
produced by the 1.0.0 pipeline and is bit-identical under 1.1.0; restamping it
would alter already-deposited files for no informational gain.
`manual_transcripts_aligned.json` records `1.1.0`, the version that produced it.

---

## Open Items

- [ ] Confirm sample rate of all Spjallrómur sessions (assumed 16 kHz; session
      `198f2863` is a known exception — see Known Session Anomalies)
- [x] Assign a version string and DOI for the pipeline release — concept DOI
      [10.5281/zenodo.22725063](https://doi.org/10.5281/zenodo.22725063); see
      Releases for per-version DOIs
