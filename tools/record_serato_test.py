#!/usr/bin/env python3
"""Record bounded, read-only Serato observations during a human/UI test.

Only parsed state is saved. No DJ control, raw shared memory, audio content,
credentials, recognition submission, or app/controller modifications occur.
Phase labels are inert JSON lines, not instructions to the DJ or the shell.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from probes.serato_reader import ReaderError, SeratoReader, status_message


def clock_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class PhaseTail:
    """Read at most 4096 appended bytes per tick; bound line/label sizes."""

    def __init__(self, path):
        self.path, self.offset, self.identity, self.pending = path, 0, None, b""

    def read(self, now):
        try:
            with self.path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = info.st_dev, info.st_ino
                if identity != self.identity or info.st_size < self.offset:
                    self.identity, self.offset, self.pending = identity, 0, b""
                stream.seek(self.offset)
                chunk = stream.read(4096)
                self.offset += len(chunk)
        except FileNotFoundError:
            return []
        except OSError as error:
            return [{"type": "phase_file_unavailable", "detail": str(error)[:500], "sampleNs": now}]
        self.pending += chunk
        result = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                if len(line) > 4096:
                    raise ValueError("phase line exceeds 4096 bytes")
                item = json.loads(line)
                phase = item.get("phase") if isinstance(item, dict) else None
                if not isinstance(phase, str) or not 1 <= len(phase) <= 80:
                    raise ValueError("phase must contain 1..80 characters")
                timestamp = item.get("capturedNs")
                supplied = isinstance(timestamp, int) and not isinstance(timestamp, bool) and 0 < timestamp <= now + 1_000_000_000
                result.append({"type": "phase_marker", "phase": phase, "note": str(item.get("note", ""))[:1000], "capturedNs": timestamp if supplied else now, "discoveredNs": now, "timestampSource": "provided_CLOCK_MONOTONIC" if supplied else "discovery_CLOCK_MONOTONIC"})
            except (ValueError, UnicodeError) as error:
                result.append({"type": "phase_marker_rejected", "detail": str(error)[:200], "sampleNs": now})
        if len(self.pending) > 4096:
            self.pending = b""
            result.append({"type": "phase_marker_rejected", "detail": "unterminated phase line exceeds limit", "sampleNs": now})
        return result


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_program(path, now, frame=None):
    try:
        with path.open("rb") as stream:
            data = stream.read(262145)
            info = os.fstat(stream.fileno())
        if len(data) > 262144:
            raise ValueError("program.json exceeds bounded 256 KiB read")
        payload = json.loads(data)
        now = max(now, clock_ns())
        wall_ns = time.time_ns()
        export_age_ns = max(0, wall_ns - info.st_mtime_ns)
        estimated_export_ns = now - export_age_ns
        state, raw = payload.get("state", {}), payload.get("raw", {})
        if not isinstance(state, dict) or not isinstance(raw, dict):
            raise ValueError("program state/raw are not mappings")
        result = {"available": True, "mtimeNs": info.st_mtime_ns,
                  "state": {key: state.get(key) for key in ("mode", "stale", "playing", "bridgeRunning", "bridgeEpoch", "bridgeSequence", "videoFader", "faderValidUntilNs", "activeDeck", "autoSide", "selectedSource", "effectiveSource", "outputOpen", "selectionReason")},
                  "lyric": {key: state.get("lyric", {}).get(key) for key in ("visible", "progress", "quality", "sweepTiming", "waiting")}, "decks": {}}
        karaoke = state.get("lyric", {}).get("karaoke") or {}
        result["lyric"]["karaoke"] = {key: karaoke.get(key) for key in ("positionMs", "lineIndex", "waiting")}
        result["style"] = {key: state.get("style", {}).get(key) for key in ("lineMode", "outputMode", "sweepMode")}
        result["state"]["requestedSource"] = state.get("selection", {}).get("requestedSource")
        result["programReadNs"] = now
        result["exportAgeMs"] = export_age_ns / 1e6
        result["estimatedExportNs"] = estimated_export_ns
        result["export_time_method"] = "file mtime mapped by current realtime/CLOCK_MONOTONIC pair; estimate only"
        result["missing_decks"] = [key for key in ("A", "B") if key not in raw]
        observed = {"AB"[deck["deck_index"]]: deck for deck in frame["decks"]} if frame else {}
        for key in ("A", "B"):
            item = raw.get(key)
            if not isinstance(item, dict):
                continue
            captured, valid_until = item.get("capturedNs"), item.get("validUntilNs")
            fresh = isinstance(captured, int) and isinstance(valid_until, int) and captured <= now <= valid_until
            deck = {field: item.get(field) for field in ("path", "positionMs", "speed", "playing", "capturedNs", "validUntilNs", "sourceIdentity", "identityReady")}
            deck.update(fresh=fresh, ageMs=(now - captured) / 1e6 if isinstance(captured, int) else None)
            expired_at_read = isinstance(valid_until, int) and now > valid_until
            expired_at_estimated_export = isinstance(valid_until, int) and estimated_export_ns > valid_until
            deck["estimated_export_age_ms"] = (estimated_export_ns - captured) / 1e6 if isinstance(captured, int) else None
            deck["ttl_classification"] = ("expired_at_estimated_export" if expired_at_estimated_export else "expired_between_export_and_observation") if expired_at_read else "fresh_at_observation" if fresh else "clock_or_metadata_invalid"
            display = state.get("deck" + key, {})
            deck["display"] = {field: display.get(field) for field in ("positionMs", "valid", "positionText", "cacheState")} if isinstance(display, dict) else None
            actual = observed.get(key)
            if actual is not None:
                same_path = item.get("path") == actual["audio_path"]
                same_identity = item.get("sourceIdentity") == actual["source_identity"]
                comparison = {"path_matches": same_path, "source_identity_matches": same_identity}
                if same_path and same_identity and finite(item.get("positionMs")) and finite(item.get("speed")) and isinstance(captured, int):
                    delta = item["positionMs"] / 1000 - actual["position_seconds"]
                    dt = (frame["capturedNs"] - captured) / 1e9
                    comparison.update(raw_position_delta_seconds=delta, sample_time_offset_seconds=dt,
                                      time_aligned_delta_seconds=delta + item["speed"] * dt,
                                      velocity_difference=item["speed"] - actual["velocity"])
                deck["comparison"] = comparison
            result["decks"][key] = deck
        if frame is not None and finite(state.get("videoFader")):
            result["fader_comparison"] = {"difference": state["videoFader"] - frame["fader"]["normalized"],
                                          "fresh": isinstance(state.get("faderValidUntilNs"), int) and now <= state["faderValidUntilNs"]}
        return result
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return {"available": False, "detail": str(error)[:500]}


class Observations:
    def __init__(self, jump_threshold):
        self.previous = None
        self.backward = {}
        self.events = []
        self.jump_threshold = jump_threshold

    def accept(self, frame, phase):
        events = []
        if self.previous is not None:
            old = self.previous
            if frame["capturedNs"] <= old["capturedNs"]:
                events.append({"kind": "nonincreasing_capture_timestamp", "previousNs": old["capturedNs"], "currentNs": frame["capturedNs"]})
            if frame["epoch"] == old["epoch"] and frame["sequence"] <= old["sequence"]:
                events.append({"kind": "nonincreasing_sequence"})
            if frame["epoch"] != old["epoch"]:
                events.append({"kind": "new_reader_epoch"})
                self.backward = {}
            else:
                dt = (frame["capturedNs"] - old["capturedNs"]) / 1e9
                for previous, current in zip(old["decks"], frame["decks"]):
                    deck_index = current["deck_index"]
                    if (previous["audio_path"], previous["source_identity"]) != (current["audio_path"], current["source_identity"]):
                        events.append({"kind": "track_identity_changed", "deck_index": deck_index})
                        self.backward.pop(deck_index, None)
                        continue
                    if previous["velocity"] != current["velocity"]:
                        events.append({"kind": "velocity_changed", "deck_index": deck_index, "from": previous["velocity"], "to": current["velocity"]})
                    delta = current["position_seconds"] - previous["position_seconds"]
                    residual = delta - previous["velocity"] * dt
                    short_backward = delta < -.03 and previous["velocity"] >= 0 and current["velocity"] >= 0
                    if dt > 0 and (abs(residual) >= self.jump_threshold or short_backward):
                        event = {"kind": "position_discontinuity", "deck_index": deck_index, "from_seconds": previous["position_seconds"], "to_seconds": current["position_seconds"], "observed_delta_seconds": delta, "previous_velocity_prediction_residual_seconds": residual, "elapsed_seconds": dt}
                        events.append(event)
                        if short_backward:
                            event["direction"] = "backward"
                            prior = self.backward.get(deck_index)
                            if prior and abs(prior["from_seconds"] - event["from_seconds"]) <= .5 and abs(prior["to_seconds"] - event["to_seconds"]) <= .5:
                                events.append({"kind": "repeated_backward_wrap_candidate", "deck_index": deck_index, "from_seconds": event["from_seconds"], "to_seconds": event["to_seconds"], "period_seconds": (frame["capturedNs"] - prior["capturedNs"]) / 1e9, "meaning": "observed repeated wrap; loop activation requires UI evidence"})
                            self.backward[deck_index] = dict(event, capturedNs=frame["capturedNs"])
                if frame["fader"] != old["fader"]:
                    events.append({"kind": "video_fader_changed", "from": old["fader"], "to": frame["fader"]})
        self.previous = frame
        for event in events:
            event.update(phase=phase, capturedNs=frame["capturedNs"])
        self.events.extend(events)
        return events


def phase_stats():
    return {"accepted": 0, "refused": 0, "refusal_codes": Counter(), "positions": {}, "velocities": {}, "fader_min": None, "fader_max": None, "program_available": 0, "program_unavailable": 0, "program_fresh_decks": Counter(), "program_stale_decks": Counter(), "alignment_absolute_errors_seconds": {"A": [], "B": []}, "max_program_age_ms": None}


def range_add(mapping, key, value):
    if key not in mapping:
        mapping[key] = {"min": value, "max": value}
    else:
        mapping[key]["min"] = min(mapping[key]["min"], value)
        mapping[key]["max"] = max(mapping[key]["max"], value)


def summarize(phases, detector, started_ns, ended_ns, reason, identity, marker_events):
    summary = {"startedNs": started_ns, "endedNs": ended_ns, "duration_seconds": (ended_ns - started_ns) / 1e9, "finished_at": utc_now(), "termination_reason": reason, "read_only": True, "clock": "CLOCK_MONOTONIC", "phases": {}, "event_counts": dict(Counter(item["kind"] for item in detector.events)), "events": detector.events, "phase_markers": marker_events, "identity": identity,
               "limits": ["position discontinuities are observed candidates, not inferred DJ commands", "repeated backward wraps require independent UI evidence to confirm loop activation", "program comparison aligns two independently captured observations using the program velocity; transitions can show temporary residuals", "no assertion about audio routing, audible mix, recognition, or cloud behavior"]}
    for name, stats in phases.items():
        target = dict(stats)
        target.pop("alignment_absolute_errors_seconds")
        target["alignment"] = {}
        for deck, errors in stats["alignment_absolute_errors_seconds"].items():
            ordered = sorted(errors)
            target["alignment"][deck] = {"samples": len(ordered), "median_absolute_error_seconds": ordered[len(ordered) // 2] if ordered else None, "p95_absolute_error_seconds": ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else None, "max_absolute_error_seconds": ordered[-1] if ordered else None}
        summary["phases"][name] = target
    return summary


def self_test():
    detector = Observations(.25)
    def frame(sequence, position, captured, velocity=1):
        return {"epoch": "one", "sequence": sequence, "capturedNs": captured, "decks": [{"deck_index": 0, "source_identity": 1, "audio_path": "fixture.wav", "position_seconds": position, "velocity": velocity}], "fader": {"normalized": .5}}
    assert not detector.accept(frame(1, 10, 1_000_000_000), "synthetic")
    assert not detector.accept(frame(2, 10.05, 1_050_000_000), "synthetic")
    assert detector.accept(frame(3, 20, 1_100_000_000), "synthetic")[0]["kind"] == "position_discontinuity"
    detector.accept(frame(4, 18, 1_150_000_000), "synthetic")
    detector.accept(frame(5, 20, 3_150_000_000), "synthetic")
    assert any(item["kind"] == "repeated_backward_wrap_candidate" for item in detector.accept(frame(6, 18, 3_200_000_000), "synthetic"))
    print(json.dumps({"self_test": "passed", "native_or_DJ_access": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=120, help="bounded seconds, 1..600")
    parser.add_argument("--interval", type=float, default=.05, help="sample seconds, .02..1")
    parser.add_argument("--run-id", default=datetime.now().strftime("run-%H%M%S"))
    parser.add_argument("--output-root", type=Path, default=ROOT / "runtime/test-20261009/live")
    parser.add_argument("--phase-file", type=Path, default=ROOT / "runtime/test-20261009/live/phases.jsonl")
    parser.add_argument("--program", type=Path, default=ROOT / "runtime/program.json")
    parser.add_argument("--stop-file", type=Path, default=ROOT / "runtime/test-20261009/live/stop")
    parser.add_argument("--jump-threshold", type=float, default=.25, help="seconds, .1..5")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not 1 <= args.duration <= 600 or not .02 <= args.interval <= 1 or not .1 <= args.jump_threshold <= 5:
        parser.error("duration/interval/jump-threshold out of bounded range")
    if Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
        parser.error("run-id must be a directory name")
    destination = args.output_root / args.run_id
    destination.mkdir(parents=True, exist_ok=False)
    recorder = (destination / "observations.jsonl").open("x", encoding="utf-8")
    tail, detector = PhaseTail(args.phase_file), Observations(args.jump_threshold)
    started_ns, phase, reader, next_attach, identity = clock_ns(), "baseline", None, 0, None
    phases, marker_events = {}, []
    last_progress_ns, ready_printed, reason = 0, False, "duration_completed"
    try:
        while clock_ns() - started_ns < args.duration * 1e9:
            tick_ns = clock_ns()
            if args.stop_file.exists():
                reason = "stop_file"
                break
            for marker in tail.read(tick_ns):
                marker_events.append(marker)
                recorder.write(json.dumps(marker, ensure_ascii=False) + "\n")
                if marker["type"] == "phase_marker":
                    phase = marker["phase"]
            stats = phases.setdefault(phase, phase_stats())
            frame, failure = None, None
            try:
                if reader is None:
                    if tick_ns < next_attach:
                        time.sleep(args.interval)
                        continue
                    reader = SeratoReader()
                    identity = reader.identity_report()
                frame = reader.read_frame()
                stats["accepted"] += 1
                events = detector.accept(frame, phase)
                for deck in frame["decks"]:
                    key = "AB"[deck["deck_index"]]
                    range_add(stats["positions"], key, deck["position_seconds"])
                    range_add(stats["velocities"], key, deck["velocity"])
                value = frame["fader"]["normalized"]
                stats["fader_min"] = value if stats["fader_min"] is None else min(stats["fader_min"], value)
                stats["fader_max"] = value if stats["fader_max"] is None else max(stats["fader_max"], value)
            except (ReaderError, OSError, ValueError) as error:
                failure = status_message(error)
                stats["refused"] += 1
                stats["refusal_codes"][failure["code"]] += 1
                if reader is not None and (not isinstance(error, ReaderError) or error.terminal):
                    reader.close()
                    reader = None
                if reader is None:
                    next_attach = clock_ns() + 2_000_000_000
                events = []
            sampled_ns = clock_ns()
            program = read_program(args.program, sampled_ns, frame)
            stats["program_available" if program["available"] else "program_unavailable"] += 1
            for key, deck in program.get("decks", {}).items():
                stats["program_fresh_decks" if deck["fresh"] else "program_stale_decks"][key] += 1
                age = deck.get("ageMs")
                if finite(age):
                    stats["max_program_age_ms"] = age if stats["max_program_age_ms"] is None else max(stats["max_program_age_ms"], age)
                comparison = deck.get("comparison", {})
                aligned = comparison.get("time_aligned_delta_seconds")
                if deck["fresh"] and finite(aligned):
                    stats["alignment_absolute_errors_seconds"][key].append(abs(aligned))
            observation = {"type": "accepted" if frame else "refused", "sampleNs": sampled_ns, "phase": phase, "frame": frame, "failure": failure, "events": events, "program": program}
            recorder.write(json.dumps(observation, ensure_ascii=False, allow_nan=False) + "\n")
            recorder.flush()
            if frame is not None and not ready_printed:
                print(json.dumps({"type": "recorder_ready", "run_directory": str(destination), "phase_file": str(args.phase_file), "duration_seconds": args.duration, "startedNs": started_ns, "baseline_decks": frame["decks"], "baseline_fader": frame["fader"]}, ensure_ascii=False), flush=True)
                ready_printed = True
            if sampled_ns - last_progress_ns >= 1_000_000_000:
                save_json(destination / "progress.json", {"sampleNs": sampled_ns, "phase": phase, "accepted": sum(item["accepted"] for item in phases.values()), "refused": sum(item["refused"] for item in phases.values()), "event_counts": dict(Counter(event["kind"] for event in detector.events)), "latest_frame": frame, "latest_failure": failure, "latest_program": program})
                last_progress_ns = sampled_ns
            time.sleep(max(0, args.interval - (clock_ns() - tick_ns) / 1e9))
    except KeyboardInterrupt:
        reason = "keyboard_interrupt"
    finally:
        if reader is not None:
            reader.close()
        recorder.close()
        summary = summarize(phases, detector, started_ns, clock_ns(), reason, identity, marker_events)
        save_json(destination / "summary.json", summary)
        print(json.dumps({"type": "recorder_finished", "summary": str(destination / "summary.json"), "accepted": sum(item["accepted"] for item in phases.values()), "refused": sum(item["refused"] for item in phases.values()), "event_counts": summary["event_counts"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
