#!/usr/bin/env python3
"""
align_manual_transcripts.py — Remap the corpus manual transcriptions onto the
mixed audio timeline produced by synthesise.py.

The manual transcriptions in the Spjallrómur corpus are not timed against the
drift-corrected mixed audio.  Two things vary per transcript entry and must be
determined from the data rather than assumed:

  1. Which recording channel each turn belongs to.  The speaker_a / speaker_b
     labels are not reliable — some entries were diarised from a mixed
     recording, where physical channel identity is not recoverable, and their
     labels are inverted with respect to the WAV channels.

  2. Which timeline the turn times refer to.  Most entries were timed against
     the original per-channel recordings, made before drift correction, and
     need the target channel scaled by resample_ratio.  A minority were timed
     against an already drift-corrected mix and need no correction at all.

Both are resolved by scoring the turns against the forced-alignment word
timings, which are ground truth for when each channel carried speech.  Every
decision, its evidence and a before/after validation score are recorded in the
output so the result can be audited rather than taken on trust.

Reference-channel turns are never modified: alignment stretches only the
shorter (target) channel, by resample_ratio >= 1.0.

Usage:
    python align_manual_transcripts.py \\
        --corpus-root /path/to/clarin/spjallromur \\
        --transcript-root /path/to/spjallromur-v2 \\
        --output-root /path/to/output
"""

import argparse
import copy
import json
import sys
from pathlib import Path

from synthesise import load_transcript

# Stamped into manual_transcripts_aligned.json.  analyse.py keeps writing
# "1.0.0" into session_params.json: that data was produced by the 1.0.0
# pipeline and is bit-identical, so restamping it would churn deposited
# files for no informational gain.
PIPELINE_VERSION = "1.1.0"

# Minimum separation between the best and second-best hypothesis for a decision
# to count as confident.  Entries whose channel attribution is ambiguous are
# passed through unmodified rather than guessed at.
MIN_MARGIN = 0.05

# When the source timeline cannot be separated, the two hypotheses differ by at
# most (resample_ratio - 1) * turn_time seconds.  Below this bound the choice is
# immaterial and the majority case (original per-channel timeline) is applied;
# above it an ambiguous entry is passed through and flagged instead.
IMMATERIAL_SHIFT_SEC = 2.0


# ---------------------------------------------------------------------------
# Speech activity and overlap scoring
# ---------------------------------------------------------------------------


def merge_intervals(words) -> list:
    """Merge word timings into disjoint speech intervals, sorted by start."""
    spans = sorted(
        [w["start"], w["end"]]
        for w in words
        if w.get("start") is not None
        and w.get("end") is not None
        and w["end"] > w["start"]
    )
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def overlap_seconds(turns, intervals) -> float:
    """Total seconds of overlap between turns and speech intervals."""
    if not intervals:
        return 0.0
    total = 0.0
    for turn in turns:
        start, end = turn["startTime"], turn["endTime"]
        for lo, hi in intervals:
            if hi <= start:
                continue
            if lo >= end:
                break
            total += min(end, hi) - max(start, lo)
    return total


def turn_seconds(turns) -> float:
    return sum(t["endTime"] - t["startTime"] for t in turns)


def score_hypothesis(turns_by_label, activity, mapping) -> float:
    """Duration-weighted fraction of manual turn time landing on channel speech.

    `mapping` maps a manual speaker label to a recording channel.
    """
    hit = span = 0.0
    for label, turns in turns_by_label.items():
        hit += overlap_seconds(turns, activity[mapping[label]])
        span += turn_seconds(turns)
    return hit / span if span else 0.0


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

MAPPINGS = {
    "identity": {"speaker_a": "a", "speaker_b": "b"},
    "swapped": {"speaker_a": "b", "speaker_b": "a"},
}


def original_activity(
    transcript_root: Path, corpus_root: Path, session_id: str
) -> dict:
    """Speech intervals per channel on the ORIGINAL (pre-correction) timeline."""
    activity = {}
    for speaker in ("a", "b"):
        _, transcript, _ = load_transcript(
            transcript_root, corpus_root, session_id, speaker
        )
        activity[speaker] = (
            merge_intervals(transcript.get("words", [])) if transcript else []
        )
    return activity


def aligned_activity(output_root: Path, session_id: str) -> dict:
    """Speech intervals per channel on the MIXED (post-correction) timeline."""
    activity = {}
    session_dir = output_root / session_id
    for speaker in ("a", "b"):
        matches = sorted(session_dir.glob(f"{speaker}_{session_id}*_aligned.json"))
        if not matches:
            activity[speaker] = []
            continue
        with matches[0].open(encoding="utf-8") as fh:
            activity[speaker] = merge_intervals(json.load(fh).get("words", []))
    return activity


# ---------------------------------------------------------------------------
# Per-entry decision and remapping
# ---------------------------------------------------------------------------


def scale_turns(turns, factor):
    return [
        {"startTime": t["startTime"] * factor, "endTime": t["endTime"] * factor}
        for t in turns
    ]


def decide_mapping(turns_by_label, act_original, act_aligned) -> dict:
    """Which recording channel does each manual speaker label correspond to?

    Scored against both candidate timelines and taking the better, so the
    mapping verdict does not depend on the timeline question below.
    """
    scores = {}
    for name, mapping in MAPPINGS.items():
        scores[name] = max(
            score_hypothesis(turns_by_label, act_original, mapping),
            score_hypothesis(turns_by_label, act_aligned, mapping),
        )
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "label_mapping": ranked[0][0],
        "margin": round(ranked[0][1] - ranked[1][1], 6),
        "confident": (ranked[0][1] - ranked[1][1]) >= MIN_MARGIN,
        "scores": {k: round(v, 6) for k, v in scores.items()},
    }


def decide_timeline(target_turns, act_aligned, target_channel, ratio) -> dict:
    """Are the target-channel turns already on the mixed timeline, or not?

    Scored on target-channel turns only: reference-channel turns are identical
    under both hypotheses and would only dilute the signal.  Comparing the turns
    unscaled against scaled by resample_ratio is the sharpest available test.
    """
    activity = act_aligned[target_channel]
    span = turn_seconds(target_turns)
    if not target_turns or not activity or span == 0 or ratio == 1.0:
        return {
            "source_timeline": "original_channels",
            "margin": 0.0,
            "confident": False,
            "scores": {"unscaled": 0.0, "scaled_by_ratio": 0.0},
        }
    unscaled = overlap_seconds(target_turns, activity) / span
    scaled = overlap_seconds(scale_turns(target_turns, ratio), activity) / span
    return {
        "source_timeline": (
            "original_channels" if scaled >= unscaled else "mixed_aligned"
        ),
        "margin": round(abs(scaled - unscaled), 6),
        "confident": abs(scaled - unscaled) >= MIN_MARGIN,
        "scores": {
            "unscaled": round(unscaled, 6),
            "scaled_by_ratio": round(scaled, 6),
        },
    }


def remap_entry(entry: dict, mapping: dict, target_channel: str, ratio: float) -> int:
    """Scale target-channel turns by ratio, in place.  Returns turns changed."""
    changed = 0
    for turn in entry["turns"]:
        if mapping[turn["speaker"]] != target_channel:
            continue
        turn["startTime"] = round(turn["startTime"] * ratio, 6)
        turn["endTime"] = round(turn["endTime"] * ratio, 6)
        changed += 1
    return changed


def process_entry(entry, params, act_original, act_aligned) -> dict:
    """Return the remapped entry with an `alignment` provenance block."""
    result = copy.deepcopy(entry)

    def by_label():
        return {
            label: [t for t in result["turns"] if t["speaker"] == label]
            for label in MAPPINGS["identity"]
        }

    turns_by_label = by_label()
    mapping_verdict = decide_mapping(turns_by_label, act_original, act_aligned)
    mapping = MAPPINGS[mapping_verdict["label_mapping"]]

    reference = params["reference_channel"]
    target = "b" if reference == "a" else "a"
    ratio = params.get("resample_ratio") or 1.0

    target_turns = [t for t in result["turns"] if mapping[t["speaker"]] == target]
    timeline_verdict = decide_timeline(target_turns, act_aligned, target, ratio)

    # Largest correction this entry would receive; also the bound on the error
    # if the timeline verdict is wrong.
    max_shift = (ratio - 1.0) * max((t["endTime"] for t in target_turns), default=0.0)

    before = score_hypothesis(turns_by_label, act_aligned, mapping)

    if not mapping_verdict["confident"]:
        action, changed = "passed_through_ambiguous_channel_mapping", 0
    elif ratio == 1.0:
        action, changed = "no_correction_needed_zero_drift", 0
    elif timeline_verdict["source_timeline"] == "mixed_aligned":
        action, changed = "no_correction_needed_already_aligned", 0
    elif not timeline_verdict["confident"] and max_shift > IMMATERIAL_SHIFT_SEC:
        action, changed = "passed_through_ambiguous_timeline", 0
    else:
        changed = remap_entry(result, mapping, target, ratio)
        action = (
            "remapped"
            if timeline_verdict["confident"]
            else "remapped_immaterial_timeline_margin"
        )

    after = score_hypothesis(by_label(), act_aligned, mapping)
    reordered = result["turns"] != sorted(result["turns"], key=lambda t: t["startTime"])
    result["turns"].sort(key=lambda t: t["startTime"])

    result["alignment"] = {
        "action": action,
        "label_mapping": mapping_verdict["label_mapping"],
        "channel_for_speaker_a": mapping["speaker_a"],
        "channel_for_speaker_b": mapping["speaker_b"],
        "source_timeline": timeline_verdict["source_timeline"],
        "reference_channel": reference,
        "target_channel": target,
        "resample_ratio": ratio,
        "max_shift_sec": round(max_shift, 6),
        "turns_remapped": changed,
        "turns_reordered": reordered,
        "evidence": {
            "channel_mapping": mapping_verdict,
            "source_timeline": timeline_verdict,
        },
        "validation": {
            "overlap_vs_mixed_before": round(before, 6),
            "overlap_vs_mixed_after": round(after, 6),
            "improved": after > before + 1e-9,
            "regressed": after < before - 1e-9,
        },
    }
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Remap manual transcriptions onto the mixed audio timeline."
    )
    parser.add_argument("--corpus-root", required=True, help="CLARIN corpus root.")
    parser.add_argument("--transcript-root", required=True, help="v2 transcript root.")
    parser.add_argument("--output-root", required=True, help="Pipeline output root.")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: <output-root>/manual_transcripts_aligned.json).",
    )
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).expanduser().resolve()
    transcript_root = Path(args.transcript_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else output_root / "manual_transcripts_aligned.json"
    )

    for label, path in [
        ("corpus-root", corpus_root),
        ("transcript-root", transcript_root),
        ("output-root", output_root),
    ]:
        if not path.is_dir():
            print(f"Error: {label} not found: {path}", file=sys.stderr)
            sys.exit(1)

    source = next(corpus_root.rglob("manual_transcripts.json"), None)
    if source is None:
        print(
            "Error: manual_transcripts.json not found under corpus-root",
            file=sys.stderr,
        )
        sys.exit(1)

    with source.open(encoding="utf-8") as fh:
        manual = json.load(fh)

    entries = []
    skipped = []
    activity_cache = {}

    print(
        f"{'transcript':16} {'map':9} {'timeline':17} {'ratio':>9} {'action':34} before->after"
    )
    for entry in sorted(
        manual.get("conversations", []),
        key=lambda c: (c["session_id"], c["transcript_id"]),
    ):
        session_id = entry["session_id"]
        params_path = output_root / session_id / "session_params.json"
        if not params_path.exists():
            skipped.append(f"{entry['transcript_id']} (no session_params.json)")
            continue
        with params_path.open(encoding="utf-8") as fh:
            params = json.load(fh)

        if session_id not in activity_cache:
            activity_cache[session_id] = (
                original_activity(transcript_root, corpus_root, session_id),
                aligned_activity(output_root, session_id),
            )
        act_original, act_aligned = activity_cache[session_id]

        result = process_entry(entry, params, act_original, act_aligned)
        entries.append(result)

        a = result["alignment"]
        print(
            f"  {result['transcript_id']:14} {a['label_mapping']:9} "
            f"{a['source_timeline']:17} {a['resample_ratio']:9.6f} {a['action']:34} "
            f"{a['validation']['overlap_vs_mixed_before']:.3f}->"
            f"{a['validation']['overlap_vs_mixed_after']:.3f}"
        )

    out = {
        "name": "manual_transcripts_aligned.json",
        "metadata": {
            **manual.get("metadata", {}),
            "derived_from": source.name,
            "description": (
                "Manual transcripts with turn timestamps remapped onto the mixed "
                "audio timeline (<session_id>_mixed.wav) produced by the "
                "Spjallromur mixing pipeline."
            ),
            "pipeline_version": PIPELINE_VERSION,
            "method": (
                "Per entry, the speaker-label-to-channel mapping and the source "
                "timeline are chosen by scoring manual turns against "
                "forced-alignment speech activity on both candidate timelines. "
                "Turns on the target (shorter, resampled) channel are scaled by "
                "resample_ratio; reference-channel turns are unchanged. See the "
                "per-entry `alignment` block for evidence and validation."
            ),
        },
        "conversations": entries,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    remapped = sum(1 for e in entries if e["alignment"]["turns_remapped"] > 0)
    regressed = [
        e["transcript_id"] for e in entries if e["alignment"]["validation"]["regressed"]
    ]
    print(f"\nWritten {out_path}")
    print(f"  entries        : {len(entries)}")
    print(f"  remapped       : {remapped}")
    print(f"  left unchanged : {len(entries) - remapped}")
    from collections import Counter

    for action, n in sorted(Counter(e["alignment"]["action"] for e in entries).items()):
        print(f"      {action:42} {n}")
    if skipped:
        print(f"  skipped        : {len(skipped)} -> {', '.join(skipped)}")
    overshoot = []
    for e in entries:
        params_path = output_root / e["session_id"] / "session_params.json"
        with params_path.open(encoding="utf-8") as fh:
            params = json.load(fh)
        mixed_duration = max(params["duration_a_sec"], params["duration_b_sec"])
        last = max(t["endTime"] for t in e["turns"])
        if last > mixed_duration:
            overshoot.append(f"{e['transcript_id']} (+{last - mixed_duration:.3f}s)")
    if overshoot:
        print(
            f"  WARNING: turns extend past the mixed audio for: "
            f"{', '.join(overshoot)}",
            file=sys.stderr,
        )
    else:
        print("  structure      : no turn extends past the end of the mixed audio")

    if regressed:
        print(
            f"  WARNING: agreement with the mixed timeline regressed for: "
            f"{', '.join(regressed)}",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  validation     : no entry regressed against the mixed timeline")


if __name__ == "__main__":
    main()
