"""Write JSON and CSV artifacts from an :class:`InsightsReport`."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from eden_business_simulator.insights.models import InsightsReport


ARTIFACT_NAMES = {
    "json": "insights_report.json",
    "windows": "windows.csv",
    "event_type_counts": "event_type_counts.csv",
    "entity_cardinality": "entity_cardinality.csv",
    "anomalies": "anomalies.csv",
    "kpis": "kpis.csv",
    "replay_fidelity": "replay_fidelity.csv",
}


def _write_json(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["json"]
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


def _write_windows_csv(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["windows"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["window_start", "window_end", "event_type", "count"])
        for window in report.windows:
            writer.writerow([window.start, window.end, "__all__", window.total])
            for event_type, count in sorted(window.by_event_type.items()):
                writer.writerow([window.start, window.end, event_type, count])
    return path


def _write_event_type_counts_csv(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["event_type_counts"]
    total = report.overview.event_count or 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["event_type", "count", "percentage"])
        for event_type, count in sorted(report.aggregates.event_type_counts.items()):
            pct = round(100 * count / total, 2) if total else 0.0
            writer.writerow([event_type, count, pct])
    return path


def _write_entity_cardinality_csv(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["entity_cardinality"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["event_type", "id_key", "unique_count"])
        writer.writerow(["__all__", "event_id", report.entity_cardinality.overall_event_ids])
        for event_type, keys in sorted(report.entity_cardinality.per_event_type.items()):
            for key, count in sorted(keys.items()):
                writer.writerow([event_type, key, count])
    return path


def _write_anomalies_csv(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["anomalies"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["anomaly", "count", "detail"])
        writer.writerow(["sequence_gaps", report.anomalies.sequence_gap_count, ""])
        writer.writerow(["duplicate_sequences", report.anomalies.duplicate_sequence_count, ""])
        writer.writerow(
            ["out_of_order_sequences", report.anomalies.out_of_order_sequence_count, ""]
        )
        writer.writerow(["empty_payloads", report.anomalies.empty_payload_count, ""])
        writer.writerow(
            ["timestamp_inversions", report.anomalies.timestamp_inversion_count, ""]
        )
        writer.writerow(
            [
                "unknown_event_types",
                len(report.anomalies.unknown_event_types),
                ";".join(report.anomalies.unknown_event_types),
            ]
        )
        writer.writerow(
            [
                "rate_spike_windows",
                len(report.anomalies.rate_spike_windows),
                json.dumps(report.anomalies.rate_spike_windows, default=str),
            ]
        )
        for field, count in sorted(report.anomalies.missing_field_counts.items()):
            writer.writerow([f"missing_{field}", count, ""])
    return path


def _write_kpis_csv(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["kpis"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["business_type", "metric", "value"])
        for metric, value in sorted(report.business_kpis.metrics.items()):
            writer.writerow([report.business_kpis.business_type, metric, value])
    return path


def _write_replay_fidelity_csv(report: InsightsReport, output_dir: Path) -> Path:
    path = output_dir / ARTIFACT_NAMES["replay_fidelity"]
    fidelity = report.replay_fidelity
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerow(["first_sequence", fidelity.first_sequence])
        writer.writerow(["last_sequence", fidelity.last_sequence])
        writer.writerow(["expected_count", fidelity.expected_count])
        writer.writerow(["duplicate_sequences", fidelity.duplicate_sequences])
        writer.writerow(["missing_sequence_count", fidelity.sequence_gap_count])
        writer.writerow(["missing_sequences_sample", ";".join(map(str, fidelity.missing_sequences))])
        writer.writerow(["timestamp_monotonic", fidelity.timestamp_monotonic])
        writer.writerow(["non_monotonic_timestamp_count", fidelity.non_monotonic_timestamp_count])
    return path


def write_artifacts(
    report: InsightsReport,
    output_dir: Path | str,
    format: str = "both",
) -> dict[str, Path]:
    """Write the requested artifacts and return the created file paths.

    ``format`` may be ``"json"``, ``"csv"``, or ``"both"``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    write_json = format in ("json", "both")
    write_csv = format in ("csv", "both")

    if write_json:
        written["json"] = _write_json(report, output_dir)

    if write_csv:
        written["windows"] = _write_windows_csv(report, output_dir)
        written["event_type_counts"] = _write_event_type_counts_csv(report, output_dir)
        written["entity_cardinality"] = _write_entity_cardinality_csv(report, output_dir)
        written["anomalies"] = _write_anomalies_csv(report, output_dir)
        written["kpis"] = _write_kpis_csv(report, output_dir)
        written["replay_fidelity"] = _write_replay_fidelity_csv(report, output_dir)

    return written
