"""Tests for the insights workflow."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from eden_business_simulator import cli
from eden_business_simulator.insights import analyze_stream, write_artifacts
from eden_business_simulator.insights.analyzer import StreamAnalyzer
from eden_business_simulator.insights.kpi import calculate_kpis
from eden_business_simulator.insights.models import InsightsReport
from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.base import StoredRecord
from eden_business_simulator.storage.memory import MemoryStorageAdapter
from eden_business_simulator.storage.ndjson import NdjsonStorageAdapter


runner = CliRunner()


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    sequence: int,
    business_type: str = "ecommerce",
    stream_id: str = "test_stream",
    seconds: float = 0.0,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{sequence:04d}",
        timestamp=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds),
        business_type=business_type,
        event_type=event_type,
        payload=payload,
        stream_id=stream_id,
        sequence=sequence,
    )


def _make_adapter(business_type: str = "ecommerce", stream_id: str = "test_stream"):
    return MemoryStorageAdapter("memory://", stream_id)


def _append(adapter: MemoryStorageAdapter, *envelopes: EventEnvelope) -> None:
    for envelope in envelopes:
        adapter.append(envelope)


def test_analyze_stream_overview_and_aggregates():
    adapter = _make_adapter()
    _append(
        adapter,
        _envelope("customer_created", {"customer_id": "c1"}, 0, seconds=0),
        _envelope("product_viewed", {"customer_id": "c1", "product_id": "p1"}, 1, seconds=1),
        _envelope("cart_updated", {"customer_id": "c1", "items": [{"product_id": "p1"}]}, 2, seconds=2),
        _envelope("order_placed", {"order_id": "o1", "customer_id": "c1", "total": 99.0}, 3, seconds=3),
        _envelope("payment_processed", {"order_id": "o1", "amount": 99.0, "status": "approved"}, 4, seconds=4),
    )

    report = analyze_stream(adapter, window_minutes=1)

    assert report.stream_id == "test_stream"
    assert report.overview.business_type == "ecommerce"
    assert report.overview.event_count == 5
    assert report.overview.duration_seconds == 4.0
    assert report.aggregates.event_type_counts["payment_processed"] == 1
    assert report.aggregates.events_per_second == pytest.approx(1.25)


def test_entity_cardinality_extraction():
    adapter = _make_adapter()
    _append(
        adapter,
        _envelope(
            "order_placed",
            {
                "order_id": "o1",
                "customer_id": "c1",
                "items": [
                    {"product_id": "p1", "sku": "S1"},
                    {"product_id": "p2", "sku": "S2"},
                ],
            },
            0,
        ),
        _envelope(
            "order_placed",
            {"order_id": "o2", "customer_id": "c1", "items": [{"product_id": "p1"}]},
            1,
        ),
    )

    report = analyze_stream(adapter)

    assert report.entity_cardinality.overall_event_ids == 2
    order_cardinality = report.entity_cardinality.per_event_type["order_placed"]
    assert order_cardinality["order_id"] == 2
    assert order_cardinality["customer_id"] == 1
    assert order_cardinality["product_id"] == 2


def test_windowed_summaries():
    adapter = _make_adapter()
    _append(
        adapter,
        _envelope("product_viewed", {"customer_id": "c1", "product_id": "p1"}, 0, seconds=0),
        _envelope("product_viewed", {"customer_id": "c2", "product_id": "p2"}, 1, seconds=30),
        _envelope("order_placed", {"order_id": "o1", "customer_id": "c1", "total": 10}, 2, seconds=90),
        _envelope("order_placed", {"order_id": "o2", "customer_id": "c2", "total": 20}, 3, seconds=150),
    )

    report = analyze_stream(adapter, window_minutes=1)

    assert len(report.windows) == 3
    assert report.windows[0].total == 2
    assert report.windows[0].by_event_type["product_viewed"] == 2
    assert report.windows[1].total == 1
    assert report.windows[1].by_event_type["order_placed"] == 1
    assert report.windows[2].total == 1
    assert report.windows[2].by_event_type["order_placed"] == 1


def test_anomaly_flags():
    adapter = _make_adapter(stream_id="anomaly_stream")
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Valid ecommerce events plus an unknown event type and an empty payload.
    records = [
        StoredRecordFactory("customer_created", {"customer_id": "c1"}, 0, base),
        StoredRecordFactory("order_placed", {"order_id": "o1"}, 1, base + timedelta(seconds=1)),
        StoredRecordFactory("payment_processed", {"amount": 10, "status": "approved"}, 2, base + timedelta(seconds=2)),
        StoredRecordFactory("alien_event", {"order_id": "o1"}, 5, base + timedelta(seconds=5)),  # unknown + gap
        StoredRecordFactory("customer_created", {}, 5, base + timedelta(seconds=6)),  # duplicate seq + empty payload
        StoredRecordFactory("customer_created", {"customer_id": "c2"}, 3, base + timedelta(seconds=3)),  # out of order + ts inversion
    ]
    adapter._records = records

    report = analyze_stream(adapter)

    assert report.anomalies.sequence_gap_count > 0
    assert report.anomalies.duplicate_sequence_count == 1
    assert report.anomalies.out_of_order_sequence_count > 0
    assert report.anomalies.empty_payload_count >= 1
    assert report.anomalies.timestamp_inversion_count > 0
    assert "alien_event" in report.anomalies.unknown_event_types
    assert report.anomalies.missing_field_counts.get("payload", 0) >= 1


def test_replay_fidelity():
    adapter = _make_adapter()
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    records = [
        StoredRecordFactory("order_placed", {"order_id": "o1"}, 0, base),
        StoredRecordFactory("order_placed", {"order_id": "o2"}, 1, base + timedelta(seconds=1)),
        StoredRecordFactory("order_placed", {"order_id": "o3"}, 3, base + timedelta(seconds=3)),
        StoredRecordFactory("order_placed", {"order_id": "o4"}, 3, base + timedelta(seconds=4)),
        StoredRecordFactory("order_placed", {"order_id": "o5"}, 5, base + timedelta(seconds=2)),  # ts inversion
    ]
    adapter._records = records

    report = analyze_stream(adapter)
    fidelity = report.replay_fidelity

    assert fidelity.first_sequence == 0
    assert fidelity.last_sequence == 5
    assert fidelity.expected_count == 6
    assert fidelity.duplicate_sequences == 1
    assert fidelity.sequence_gap_count == 2
    assert 2 in fidelity.missing_sequences
    assert 4 in fidelity.missing_sequences
    assert fidelity.timestamp_monotonic is False
    assert fidelity.non_monotonic_timestamp_count == 1


def test_kpi_ecommerce():
    events = [
        _envelope("product_viewed", {"customer_id": "c1", "product_id": "p1"}, 0),
        _envelope("order_placed", {"order_id": "o1", "customer_id": "c1", "total": 50.0}, 1),
        _envelope("payment_processed", {"order_id": "o1", "amount": 50.0, "status": "approved"}, 2),
        _envelope("payment_processed", {"order_id": "o2", "amount": 30.0, "status": "declined"}, 3),
        _envelope("refund_issued", {"order_id": "o1", "amount": 50.0}, 4),
    ]

    metrics = calculate_kpis("ecommerce", events)

    assert metrics["revenue"] == 50.0
    assert metrics["orders"] == 1
    assert metrics["average_order_value"] == 50.0
    assert metrics["approved_payments"] == 1
    assert metrics["declined_payments"] == 1
    assert metrics["payment_decline_rate"] == 0.5
    assert metrics["refunds"] == 1
    assert metrics["refund_rate"] == 1.0
    assert metrics["conversion_rate"] == 1.0


def test_kpi_gym():
    events = [
        _envelope("membership_enrolled", {"member_id": "m1", "monthly_fee": 49.0}, 0, business_type="gym"),
        _envelope("membership_enrolled", {"member_id": "m2", "monthly_fee": 79.0}, 1, business_type="gym"),
        _envelope("check_in_recorded", {"member_id": "m1"}, 2, business_type="gym"),
        _envelope("class_booked", {"member_id": "m1", "class_id": "c1"}, 3, business_type="gym"),
        _envelope("class_cancelled", {"member_id": "m1", "class_id": "c1"}, 4, business_type="gym"),
        _envelope("retail_purchase_made", {"member_id": "m2", "total": 12.0}, 5, business_type="gym"),
        _envelope("payment_failed", {"member_id": "m1"}, 6, business_type="gym"),
        _envelope("membership_renewed", {"member_id": "m1", "monthly_fee": 49.0}, 7, business_type="gym"),
        _envelope("membership_upgraded", {"member_id": "m1"}, 8, business_type="gym"),
        _envelope("membership_downgraded", {"member_id": "m2"}, 9, business_type="gym"),
        _envelope("membership_cancelled", {"member_id": "m2"}, 10, business_type="gym"),
    ]

    metrics = calculate_kpis("gym", events)

    assert metrics["enrolled_members"] == 2
    assert metrics["cancelled_members"] == 1
    assert metrics["active_members"] == 1
    assert metrics["check_in_rate_per_active_member"] == 1.0
    assert metrics["class_bookings"] == 1
    assert metrics["class_cancellation_rate"] == 1.0
    assert metrics["retail_revenue"] == 12.0
    assert metrics["payment_failures"] == 1
    assert metrics["average_monthly_fee"] == 64.0


def test_kpi_clinic():
    events = [
        _envelope("appointment_scheduled", {"appointment_id": "a1", "patient_id": "p1"}, 0, business_type="clinic"),
        _envelope("appointment_cancelled", {"appointment_id": "a2", "patient_id": "p2"}, 1, business_type="clinic"),
        _envelope("patient_checked_in", {"appointment_id": "a1", "patient_id": "p1"}, 2, business_type="clinic"),
        _envelope("encounter_started", {"encounter_id": "e1", "patient_id": "p1"}, 3, business_type="clinic"),
        _envelope("diagnosis_recorded", {"encounter_id": "e1", "patient_id": "p1"}, 4, business_type="clinic"),
        _envelope("lab_order_placed", {"lab_order_id": "lo1", "encounter_id": "e1", "patient_id": "p1"}, 5, business_type="clinic"),
        _envelope("lab_result_received", {"lab_order_id": "lo1", "patient_id": "p1"}, 6, business_type="clinic"),
        _envelope("claim_submitted", {"claim_id": "c1", "encounter_id": "e1", "patient_id": "p1", "billed_amount": 200.0}, 7, business_type="clinic"),
        _envelope("claim_adjudicated", {"claim_id": "c1", "status": "paid", "paid_amount": 180.0}, 8, business_type="clinic"),
        _envelope("payment_posted", {"claim_id": "c1", "patient_id": "p1", "amount": 20.0}, 9, business_type="clinic"),
        _envelope("referral_sent", {"referral_id": "r1", "encounter_id": "e1", "patient_id": "p1"}, 10, business_type="clinic"),
    ]

    metrics = calculate_kpis("clinic", events)

    assert metrics["appointments_scheduled"] == 1
    assert metrics["patients_checked_in"] == 1
    assert metrics["no_show_rate"] == 0.0
    assert metrics["encounters_started"] == 1
    assert metrics["diagnoses_recorded"] == 1
    assert metrics["lab_orders_placed"] == 1
    assert metrics["claims_submitted"] == 1
    assert metrics["claims_adjudicated"] == 1
    assert metrics["payments_posted"] == 1
    assert metrics["referrals_sent"] == 1
    assert metrics["total_billed_amount"] == 200.0
    assert metrics["total_paid_by_payer"] == 180.0
    assert metrics["total_patient_payments"] == 20.0


def test_kpi_logistics():
    events = [
        _envelope("shipment_created", {"shipment_id": "s1"}, 0, business_type="logistics"),
        _envelope("delivery_attempted", {"shipment_id": "s1", "attempt_id": "a1", "outcome": "delivered"}, 1, business_type="logistics"),
        _envelope("delivery_delivered", {"shipment_id": "s1", "attempt_id": "a1"}, 2, business_type="logistics"),
        _envelope("customer_feedback_received", {"shipment_id": "s1", "rating": 4}, 3, business_type="logistics"),
        _envelope("return_initiated", {"shipment_id": "s1"}, 4, business_type="logistics"),
        _envelope("fuel_stop_logged", {"vehicle_id": "v1", "liters": 30.0, "cost": 50.0}, 5, business_type="logistics"),
    ]

    metrics = calculate_kpis("logistics", events)

    assert metrics["shipments_created"] == 1
    assert metrics["delivery_attempts"] == 1
    assert metrics["deliveries_completed"] == 1
    assert metrics["delivery_success_rate"] == 1.0
    assert metrics["customer_feedback_events"] == 1
    assert metrics["average_feedback_rating"] == 4.0
    assert metrics["returns_initiated"] == 1
    assert metrics["fuel_cost"] == 50.0


def test_kpi_trades():
    events = [
        _envelope("service_ticket_created", {"ticket_id": "t1", "customer_id": "c1"}, 0, business_type="field_service"),
        _envelope("estimate_requested", {"estimate_id": "e1", "ticket_id": "t1"}, 1, business_type="field_service"),
        _envelope("estimate_approved", {"estimate_id": "e2", "ticket_id": "t1"}, 2, business_type="field_service"),
        _envelope("work_completed", {"ticket_id": "t1"}, 3, business_type="field_service"),
        _envelope("invoice_generated", {"invoice_id": "i1", "ticket_id": "t1", "total": 200.0}, 4, business_type="field_service"),
        _envelope("payment_received", {"invoice_id": "i1", "ticket_id": "t1", "amount": 200.0}, 5, business_type="field_service"),
        _envelope("parts_used", {"ticket_id": "t1", "part_id": "p1", "qty": 2}, 6, business_type="field_service"),
        _envelope("follow_up_scheduled", {"ticket_id": "t1"}, 7, business_type="field_service"),
    ]

    for business_type in ("field_service", "electrician", "plumber"):
        metrics = calculate_kpis(business_type, events)
        assert metrics["tickets_created"] == 1
        assert metrics["tickets_completed"] == 1
        assert metrics["invoices_generated"] == 1
        assert metrics["payments_received"] == 1
        assert metrics["invoiced_total"] == 200.0
        assert metrics["payments_total"] == 200.0
        assert metrics["collection_rate"] == 1.0
        assert metrics["estimates_requested"] == 1
        assert metrics["estimates_approved"] == 1
        assert metrics["estimate_approval_rate"] == 1.0
        assert metrics["parts_quantity"] == 2


def test_artifacts_written(tmp_path: Path):
    adapter = _make_adapter()
    _append(
        adapter,
        _envelope("order_placed", {"order_id": "o1", "total": 10.0}, 0),
        _envelope("payment_processed", {"amount": 10.0, "status": "approved"}, 1),
    )
    report = analyze_stream(adapter, storage_backend="memory", storage_uri="memory://")

    written = write_artifacts(report, tmp_path, format="both")

    assert "json" in written
    assert "windows" in written
    assert "event_type_counts" in written
    assert "entity_cardinality" in written
    assert "anomalies" in written
    assert "kpis" in written
    assert "replay_fidelity" in written

    json_data = json.loads(written["json"].read_text())
    assert json_data["stream_id"] == "test_stream"

    with open(written["event_type_counts"], newline="") as handle:
        rows = list(csv.DictReader(handle))
    types = {row["event_type"] for row in rows}
    assert "order_placed" in types
    assert "payment_processed" in types


def test_cli_insights_ndjson(tmp_path: Path):
    storage_path = tmp_path / "stream.jsonl"
    storage = NdjsonStorageAdapter(str(storage_path), "ndj_stream")
    storage.append(
        EventEnvelope(
            event_id="e1",
            timestamp=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
            business_type="ecommerce",
            event_type="order_placed",
            payload={"order_id": "o1", "total": 25.0},
            stream_id="ndj_stream",
            sequence=0,
        )
    )
    storage.append(
        EventEnvelope(
            event_id="e2",
            timestamp=datetime(2026, 8, 1, 12, 0, 1, tzinfo=timezone.utc),
            business_type="ecommerce",
            event_type="payment_processed",
            payload={"order_id": "o1", "amount": 25.0, "status": "approved"},
            stream_id="ndj_stream",
            sequence=1,
        )
    )
    storage.close()

    output_dir = tmp_path / "insights_out"
    with patch.object(cli, "_store_insights_memory"):
        result = runner.invoke(
            cli.app,
            [
                "insights",
                "ndj_stream",
                "--storage",
                "ndjson",
                "--storage-uri",
                str(storage_path),
                "--output-dir",
                str(output_dir),
                "--format",
                "both",
            ],
        )

    assert result.exit_code == 0, result.output + (result.exception and str(result.exception) or "")
    report_path = output_dir / "insights_report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["overview"]["event_count"] == 2
    assert data["business_kpis"]["business_type"] == "ecommerce"
    assert data["business_kpis"]["metrics"]["revenue"] == 25.0


def test_cli_insights_format_json_only(tmp_path: Path):
    storage_path = tmp_path / "stream2.jsonl"
    storage = NdjsonStorageAdapter(str(storage_path), "ndj_stream2")
    storage.append(
        EventEnvelope(
            event_id="e1",
            timestamp=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
            business_type="saas",
            event_type="account_signed_up",
            payload={"account_id": "a1"},
            stream_id="ndj_stream2",
            sequence=0,
        )
    )
    storage.close()

    output_dir = tmp_path / "insights_out2"
    with patch.object(cli, "_store_insights_memory"):
        result = runner.invoke(
            cli.app,
            [
                "insights",
                "ndj_stream2",
                "--storage",
                "ndjson",
                "--storage-uri",
                str(storage_path),
                "--output-dir",
                str(output_dir),
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output + (result.exception and str(result.exception) or "")
    assert (output_dir / "insights_report.json").exists()
    assert not (output_dir / "windows.csv").exists()


def StoredRecordFactory(
    event_type: str,
    payload: dict[str, Any],
    sequence: int,
    timestamp: datetime,
    business_type: str = "ecommerce",
    stream_id: str = "test_stream",
) -> StoredRecord:
    """Build a StoredRecord directly for anomaly and fidelity tests."""
    envelope = EventEnvelope(
        event_id=f"evt_{sequence:04d}",
        timestamp=timestamp,
        business_type=business_type,
        event_type=event_type,
        payload=payload,
        stream_id=stream_id,
        sequence=sequence,
    )
    return StoredRecord(
        offset=sequence,
        sequence=sequence,
        stream_id=stream_id,
        stored_at=timestamp.isoformat(),
        envelope=envelope,
    )
