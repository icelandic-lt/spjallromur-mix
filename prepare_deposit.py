#!/usr/bin/env python3
"""
prepare_deposit.py — Assemble a complete CLARIN v2 deposit directory.

Directory structure mirrors the CLARIN recording structure, not the v2
alignment outcome:

  full_conversations/  — all 48 CLARIN full sessions (both channels recorded)
    <session_id>/      WAVs (renamed to v2 stem) + v2 JSONs (where available)
                       + pipeline outputs (mixed WAV, aligned JSONs, merged
                       transcript, session_params.json)
                       Excluded session (198f2863): session_params.json only.

  half_conversations/  — all 6 CLARIN half sessions (one channel only)
    <session_id>/      WAV (renamed to v2 stem) + v2 JSON transcript

Top-level: README.md, LICENSE, evaluation_of_alignment.md, metadata.tsv,
           annotations/manual_transcripts.json + README.txt,
           code/README.txt.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

# Directories under transcript_root copied to deposit unchanged.
# half_conversations and unaligned are NOT passthrough — they are rebuilt from
# CLARIN WAVs so the deposit structure mirrors the recording structure.
# splits/, combined/, segmented/, results/, src/ are GitHub repo artefacts
# and do not belong in the CLARIN corpus release.
PASSTHROUGH_DIRS = ()

# Filenames that must never reach the deposit, matched on the SOURCE filename at
# the single copy choke point below.  The corpus docs/ directory is not copied to
# the deposit today (PASSTHROUGH_DIRS is empty), but adding "docs" to it would
# otherwise ship the pre-anonymisation annotation file.  Anything listed here is
# skipped and reported, never silently dropped.
EXCLUDED_FILENAMES = frozenset(
    {
        "manual_transcripts_original.json",  # pre-anonymisation originals; retained in the corpus, never deposited
    }
)

# Top-level files copied from transcript_root.
# LICENSE is NOT copied from transcript_root (which carries GPL v3 from the
# GitHub repo); the corpus deposit uses CC BY 4.0, written explicitly below.
PASSTHROUGH_FILES = ("README.md", "evaluation_of_alignment.md")

CC_BY_40_LICENSE = """\
Spjallrómur Corpus

Copyright (C) 2026 Language and Voice Lab, Reykjavík University

This dataset is licensed under the Creative Commons Attribution 4.0
International License (CC BY 4.0).

You are free to:
  Share — copy and redistribute the material in any medium or format
  Adapt — remix, transform, and build upon the material for any purpose,
          even commercially

Under the following terms:
  Attribution — You must give appropriate credit, provide a link to the
                license, and indicate if changes were made.

Full license text: https://creativecommons.org/licenses/by/4.0/legalcode
"""

CODE_README_TEXT = (
    "Mixing pipeline: https://github.com/icelandic-lt/spjallromur-mix\n"
    "\n"
    "Archive this repository at release tag v1.0.0 and place it in this directory\n"
    "before deposit, named exactly:\n"
    "\n"
    "    spjallromur-mix-1.0.0.zip\n"
    "\n"
    "This is the source archive GitHub generates for that tag:\n"
    "https://github.com/icelandic-lt/spjallromur-mix/archive/refs/tags/v1.0.0.zip\n"
    "Its root directory is spjallromur-mix-1.0.0/.\n"
)

ANNOTATIONS_README_TEXT = """\
manual_transcripts.json
=======================

Manual (human) transcriptions of a subset of the full conversations, copied
from the Spjallromur corpus unchanged.  This pipeline does not modify them.

IMPORTANT - TIMELINE
--------------------
The turn timestamps in manual_transcripts.json do NOT refer to the mixed audio
in this deposit (<session_id>_mixed.wav).

The mixed audio is drift-corrected: the two channels of each conversation were
recorded on independent clocks, and the shorter channel has been resampled to
match the longer one (see resample_ratio in each session_params.json).  The
manual transcriptions, for most entries, were timed against the original
per-channel recordings made before that correction.

As a result the manual turn times run progressively out of step with the mixed
audio.  Across the 21 sessions that carry manual transcriptions the discrepancy
at the end of a conversation is about 7 seconds on average, exceeds 1 second in
14 of them, and reaches roughly 80 seconds in the worst case (2c1b4416, which
has 4.4% clock drift).

A minority of entries appear instead to have been timed against an already
drift-corrected mix, so the offset is not uniform and cannot be removed by
applying a single correction to the file as a whole.

Note also that the speaker_a / speaker_b labels used in manual_transcripts.json
do not always correspond to channels a and b of the recording.

FOR WORK AGAINST THE MIXED AUDIO
--------------------------------
Use the forced-alignment transcripts, which share the mixed timeline exactly:

  <speaker>_<session_id>_<age>_<gender>_aligned.json   per speaker
  <session_id>_transcript_merged.json                  both speakers, merged

These are produced by the mixing pipeline and their timestamps are remapped
onto the mixed audio.  See code/README.txt for the pipeline source.
"""

METADATA_COLUMNS = [
    "session_id",
    "speaker",
    "age",
    "gender",
    "duration_sec",
    "conversation_type",
    "has_mixed_audio",
    "transcript_source",
    "above_1pct_threshold",
    "audio_excluded",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_clarin_wav_pairs(
    corpus_root: Path, conv_type: str
) -> dict[str, dict[str, Path]]:
    """Return {session_id: {speaker: wav_path}} for CLARIN sessions of the given type."""
    sessions: dict[str, dict[str, Path]] = {}
    for wav in corpus_root.rglob("speaker_*_convo_*.wav"):
        if conv_type not in wav.parts:
            continue
        parts = wav.name.split("_convo_")
        if len(parts) != 2:
            continue
        speaker = parts[0].replace("speaker_", "")
        sid = parts[1][:-4]
        if speaker in {"a", "b"}:
            sessions.setdefault(sid, {})[speaker] = wav
    return sessions


def find_v2_stems(transcript_root: Path, session_id: str) -> dict[str, str]:
    """Return {speaker: stem} for this session, searching all subdirs of transcript_root."""
    result: dict[str, str] = {}
    for p in transcript_root.rglob(f"*_{session_id}_*.json"):
        stem = p.stem  # e.g. "a_2a07b3a7_20-29_m"
        parts = stem.split("_")
        if len(parts) >= 4 and parts[0] in {"a", "b"} and parts[1] == session_id:
            result[parts[0]] = stem
    return result


def find_v2_transcript_paths(transcript_root: Path, session_id: str) -> dict[str, Path]:
    """Return {speaker: path} for v2 JSON transcripts, searching all subdirs.

    Searches the entire transcript_root so that sessions whose transcripts live
    outside full_conversations/ (e.g. 2a139f9b, whose speakers are in
    half_conversations/ and unaligned/) are found correctly.
    """
    result: dict[str, Path] = {}
    for p in transcript_root.rglob(f"*_{session_id}_*.json"):
        parts = p.stem.split("_")
        if len(parts) >= 4 and parts[0] in {"a", "b"} and parts[1] == session_id:
            result[parts[0]] = p
    return result


def stem_age_gender(stem: str) -> tuple[str, str]:
    """Parse age and gender from a v2 stem like 'a_2a07b3a7_20-29_m'."""
    parts = stem.split("_")
    # parts: [speaker, session_id, age_range, gender_code]
    age = parts[2] if len(parts) >= 3 else ""
    gender = parts[3] if len(parts) >= 4 else ""
    return age, gender


def read_clarin_demographics(wav_path: Path, session_id: str, speaker: str) -> dict:
    demo_path = (
        wav_path.parent / f"speaker_{speaker}_convo_{session_id}_demographics.json"
    )
    if demo_path.exists():
        with demo_path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def read_session_params(output_root: Path, session_id: str) -> dict | None:
    p = output_root / session_id / "session_params.json"
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)
    return None


def copy_file(src: Path, dst: Path) -> bool:
    """Copy src to dst unless src is on the exclusion list.  Returns True if copied."""
    if src.name in EXCLUDED_FILENAMES:
        print(f"  EXCLUDED from deposit: {src.name}", file=sys.stderr)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    return True


def copy_dir_unchanged(src: Path, dst: Path) -> int:
    count = 0
    for f in src.rglob("*"):
        if f.is_file():
            if copy_file(f, dst / f.relative_to(src)):
                count += 1
    return count


def write_metadata_tsv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(METADATA_COLUMNS) + "\n")
        for row in rows:
            fh.write(
                "\t".join(str(row.get(col, "")) for col in METADATA_COLUMNS) + "\n"
            )


# ---------------------------------------------------------------------------
# Full conversations processing
# ---------------------------------------------------------------------------


def process_full_conversations(
    corpus_root: Path,
    transcript_root: Path,
    output_root: Path,
    deposit_root: Path,
) -> tuple[list[dict], dict]:
    """
    Process all CLARIN full_conversations sessions into the deposit.
    Returns (metadata_rows, stats).
    """
    clarin_pairs = find_clarin_wav_pairs(corpus_root, "full_conversations")
    metadata_rows: list[dict] = []
    stats = {
        "total": len(clarin_pairs),
        "with_mixed": 0,
        "without_mixed": 0,
        "excluded": 0,
        "warnings": [],
    }

    for session_id in sorted(clarin_pairs):
        session_dir = deposit_root / "full_conversations" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        params = read_session_params(output_root, session_id)
        is_excluded = params is not None and params.get("status") == "excluded"

        # Always copy session_params.json if it exists.
        src_params = output_root / session_id / "session_params.json"
        if src_params.exists():
            copy_file(src_params, session_dir / "session_params.json")
        elif params is None:
            stats["warnings"].append(f"[{session_id}] no session_params.json found")

        if is_excluded:
            stats["excluded"] += 1
            _append_metadata_rows(
                metadata_rows,
                session_id,
                "full",
                clarin_pairs[session_id],
                transcript_root,
                params,
                has_mixed=False,
                audio_excluded=True,
            )
            continue

        # Discover v2 stems for WAV renaming and metadata.
        v2_stems = find_v2_stems(transcript_root, session_id)

        # Copy CLARIN WAVs renamed to v2 stem pattern.
        for spk, wav_path in sorted(clarin_pairs[session_id].items()):
            stem = v2_stems.get(spk)
            if stem:
                dst_name = f"{stem}.wav"
            else:
                dst_name = wav_path.name
                stats["warnings"].append(
                    f"[{session_id}/{spk}] no v2 stem found; kept CLARIN filename"
                )
            copy_file(wav_path, session_dir / dst_name)

        # Copy v2 JSON transcripts — search all subdirs so that sessions whose
        # transcripts live outside full_conversations/ (e.g. 2a139f9b) are
        # found correctly.
        v2_paths = find_v2_transcript_paths(transcript_root, session_id)
        for jf in sorted(v2_paths.values()):
            copy_file(jf, session_dir / jf.name)

        # Copy pipeline outputs (skip old CLARIN-style aligned filenames).
        out_dir = output_root / session_id
        if out_dir.exists():
            for f in sorted(out_dir.iterdir()):
                if not f.is_file():
                    continue
                name = f.name
                # Skip old CLARIN-format aligned files produced before the v2 rewrite.
                if name.startswith("speaker_") and name.endswith("_aligned.json"):
                    continue
                # session_params.json already copied above.
                if name == "session_params.json":
                    continue
                copy_file(f, session_dir / name)
        else:
            stats["warnings"].append(f"[{session_id}] output directory not found")

        has_mixed = (session_dir / f"{session_id}_mixed.wav").exists()
        if has_mixed:
            stats["with_mixed"] += 1
        else:
            stats["without_mixed"] += 1
            stats["warnings"].append(f"[{session_id}] no mixed WAV in deposit")

        _append_metadata_rows(
            metadata_rows,
            session_id,
            "full",
            clarin_pairs[session_id],
            transcript_root,
            params,
            has_mixed=has_mixed,
            audio_excluded=False,
        )

    return metadata_rows, stats


def _append_metadata_rows(
    rows: list[dict],
    session_id: str,
    conv_type: str,
    wav_map: dict[str, Path],
    transcript_root: Path,
    params: dict | None,
    has_mixed: bool,
    audio_excluded: bool,
) -> None:
    v2_stems = find_v2_stems(transcript_root, session_id)
    transcript_source = (params.get("transcript_source") or "") if params else ""
    above_1pct = params.get("above_1pct_threshold", False) if params else ""

    for spk in sorted(wav_map):
        stem = v2_stems.get(spk, "")
        age, gender = stem_age_gender(stem) if stem else ("", "")

        demo = read_clarin_demographics(wav_map[spk], session_id, spk)
        duration = demo.get("duration_seconds", "")

        rows.append(
            {
                "session_id": session_id,
                "speaker": spk,
                "age": age,
                "gender": gender,
                "duration_sec": duration,
                "conversation_type": conv_type,
                "has_mixed_audio": has_mixed,
                "transcript_source": transcript_source,
                "above_1pct_threshold": above_1pct,
                "audio_excluded": audio_excluded,
            }
        )


# ---------------------------------------------------------------------------
# Half conversations processing
# ---------------------------------------------------------------------------


def process_half_conversations(
    corpus_root: Path,
    transcript_root: Path,
    deposit_root: Path,
) -> list[dict]:
    """
    Build deposit/half_conversations/ from CLARIN WAVs + v2 transcripts.
    Returns metadata rows.
    """
    clarin_pairs = find_clarin_wav_pairs(corpus_root, "half_conversations")
    metadata_rows: list[dict] = []

    for session_id in sorted(clarin_pairs):
        session_dir = deposit_root / "half_conversations" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        v2_stems = find_v2_stems(transcript_root, session_id)

        # Copy CLARIN WAV renamed to v2 stem.
        for spk, wav_path in sorted(clarin_pairs[session_id].items()):
            stem = v2_stems.get(spk)
            dst_name = f"{stem}.wav" if stem else wav_path.name
            copy_file(wav_path, session_dir / dst_name)

        # Copy v2 JSON transcripts.
        v2_paths = find_v2_transcript_paths(transcript_root, session_id)
        for jf in sorted(v2_paths.values()):
            copy_file(jf, session_dir / jf.name)

        _append_metadata_rows(
            metadata_rows,
            session_id,
            "half",
            clarin_pairs[session_id],
            transcript_root,
            params=None,
            has_mixed=False,
            audio_excluded=False,
        )

    print(f"Processed half_conversations/: {len(clarin_pairs)} sessions")
    return metadata_rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Assemble a complete CLARIN v2 deposit directory."
    )
    parser.add_argument(
        "--deposit-root", required=True, help="Where to write the deposit."
    )
    parser.add_argument(
        "--corpus-root", required=True, help="CLARIN corpus root (WAV files)."
    )
    parser.add_argument(
        "--transcript-root", required=True, help="v2 transcript root (JSON files)."
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Pipeline output root (from analyse + synthesise).",
    )
    args = parser.parse_args()

    deposit_root = Path(args.deposit_root).expanduser().resolve()
    corpus_root = Path(args.corpus_root).expanduser().resolve()
    transcript_root = Path(args.transcript_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    for label, p in [
        ("deposit-root", deposit_root),
        ("corpus-root", corpus_root),
        ("transcript-root", transcript_root),
        ("output-root", output_root),
    ]:
        if label == "deposit-root":
            deposit_root.mkdir(parents=True, exist_ok=True)
        elif not p.exists() or not p.is_dir():
            print(f"Error: {label} not found or not a directory: {p}", file=sys.stderr)
            sys.exit(1)

    # 1. full_conversations (48 CLARIN sessions)
    print("Processing full_conversations...")
    fc_metadata, stats = process_full_conversations(
        corpus_root, transcript_root, output_root, deposit_root
    )

    # 2. half_conversations (6 CLARIN sessions, built from WAVs + v2 transcripts)
    half_metadata = process_half_conversations(
        corpus_root, transcript_root, deposit_root
    )

    # 3. Passthrough directories from transcript_root
    for dirname in PASSTHROUGH_DIRS:
        src = transcript_root / dirname
        if src.exists():
            dst = deposit_root / dirname
            n = copy_dir_unchanged(src, dst)
            print(f"Copied {dirname}/: {n} files")
        else:
            print(f"  (skipped {dirname}/: not found in transcript_root)")

    # 4. Top-level files from transcript_root
    for fname in PASSTHROUGH_FILES:
        src = transcript_root / fname
        if src.exists():
            copy_file(src, deposit_root / fname)
            print(f"Copied {fname}")
        else:
            print(f"  (skipped {fname}: not found)")

    # CC BY 4.0 license for the corpus deposit (transcript_root carries GPL v3
    # from the GitHub repo, which is not appropriate for corpus data).
    (deposit_root / "LICENSE").write_text(CC_BY_40_LICENSE, encoding="utf-8")
    print("Written LICENSE (CC BY 4.0)")

    # 5. Metadata TSV
    all_metadata = fc_metadata + half_metadata
    metadata_path = deposit_root / "metadata.tsv"
    write_metadata_tsv(metadata_path, all_metadata)
    print(f"Written metadata.tsv ({len(all_metadata)} rows)")

    # 6. annotations/manual_transcripts.json
    # Deposited under its plain name, matching the corpus source.  Any future
    # annotation batch is deposited as its own separately named file.
    # The corpus is expected to supply manual_transcripts.json already
    # anonymised; the pipeline does no anonymisation of its own.
    manual_src = next(corpus_root.rglob("manual_transcripts.json"), None)
    if manual_src is not None:
        annotations_dir = deposit_root / "annotations"
        annotations_dir.mkdir(parents=True, exist_ok=True)
        copy_file(manual_src, annotations_dir / "manual_transcripts.json")
        print("Copied annotations/manual_transcripts.json")
        # The manual transcriptions are on a different timeline from the mixed
        # audio shipped beside them; say so where a user will find it.
        (annotations_dir / "README.txt").write_text(
            ANNOTATIONS_README_TEXT, encoding="utf-8"
        )
        print("Written annotations/README.txt")
    else:
        print(
            "  WARNING: manual_transcripts.json not found under corpus-root; "
            "annotations/ will be missing from the deposit",
            file=sys.stderr,
        )

    # 7. code/README.txt
    code_dir = deposit_root / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "README.txt").write_text(CODE_README_TEXT, encoding="utf-8")
    print("Written code/README.txt")

    # 8. Summary
    print()
    print(f"Summary:")
    print(f"  full_conversations sessions: {stats['total']} (CLARIN)")
    print(f"    With mixed audio:          {stats['with_mixed']}")
    print(f"    Excluded (no output):      {stats['excluded']}")
    print(f"  half_conversations sessions: {len(half_metadata)} (CLARIN)")
    if stats["warnings"]:
        print(f"  Warnings ({len(stats['warnings'])}):")
        for w in stats["warnings"]:
            print(f"    {w}")
    else:
        print("  No warnings.")


if __name__ == "__main__":
    main()
