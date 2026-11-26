"""Command-line interface for the Eden Business Simulator."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import time
import typer

from eden_business_simulator.businesses import list_business_types, load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.output.http import HttpOutputAdapter
from eden_business_simulator.output.http_batch import HttpBatchOutputAdapter
from eden_business_simulator.output.ndjson import NDJsonOutputAdapter
from eden_business_simulator.runner import Runner
from eden_business_simulator.storage import load_storage_adapter

app = typer.Typer(
    name="eden-business-simulator",
    help="Generate realistic business event streams for tool evaluation.",
)


def _build_adapter(config: SimulatorConfig):
    if config.output_mode == "ndjson":
        return NDJsonOutputAdapter()
    if config.output_mode == "http":
        return HttpOutputAdapter(
            config.webhook_url,
            max_retries=config.output_max_retries,
            dead_letter_path=config.dead_letter_path,
        )
    if config.output_mode == "http-batch":
        return HttpBatchOutputAdapter(
            config.webhook_url,
            batch_size=config.output_batch_size,
            flush_interval_seconds=config.output_flush_interval_seconds,
            max_retries=config.output_max_retries,
            dead_letter_path=config.dead_letter_path,
        )
    if config.output_mode == "none":
        return None
    raise ValueError(f"Unsupported output mode: {config.output_mode}")


def _scenario_schedule(names: List[str], duration: Optional[int]):
    """Build a SimulatorConfig scenario schedule from CLI flags (Phase 6).

    Every ``--scenario NAME`` starts at run start (offset 0) with the
    pack's default duration, or ``--scenario-duration`` when given.
    Unknown names are a clean parameter error, not a traceback.
    """
    from eden_business_simulator.framework.scenario import (
        ScenarioScheduleEntry,
        load_scenarios,
    )

    packs = load_scenarios()
    entries = []
    for name in names:
        if name not in packs:
            available = ", ".join(sorted(packs))
            raise typer.BadParameter(
                f"unknown scenario '{name}'. Available: {available}"
            )
        entries.append(
            ScenarioScheduleEntry(
                name=name,
                start_offset_minutes=0,
                duration_minutes=duration,
            )
        )
    return entries


def _make_stream_id(business_type: str, seed: int, explicit: str | None) -> str:
    if explicit:
        return explicit
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{business_type}_seed{seed}_{now}"


@app.command()
def run(
    business_type: str = typer.Argument(
        ..., help="Business domain to simulate (e.g. ecommerce, saas)."
    ),
    duration: float = typer.Option(
        60.0, "--duration", "-d", help="How many simulated seconds to run."
    ),
    rate: float = typer.Option(
        2.0, "--rate", "-r", help="Events per simulated second."
    ),
    max_events: int = typer.Option(
        0, "--max-events", "-n", help="Maximum events to emit (0 = unlimited)."
    ),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed for determinism."),
    output: str = typer.Option(
        "ndjson",
        "--output",
        "-o",
        help="Output adapter: 'ndjson', 'http', or 'http-batch'. HTTP outputs "
        "send 'Authorization: Bearer <token>' when the EDEN_WEBHOOK_TOKEN "
        "environment variable is set.",
    ),
    webhook_url: Optional[str] = typer.Option(
        None,
        "--webhook-url",
        "-w",
        help="Webhook URL when output mode is 'http' or 'http-batch'.",
    ),
    batch_size: int = typer.Option(
        50, "--batch-size", help="Events per POST for 'http-batch' output."
    ),
    flush_interval: float = typer.Option(
        5.0,
        "--flush-interval",
        help="Seconds before a partial 'http-batch' buffer is flushed.",
    ),
    max_retries: int = typer.Option(
        3, "--max-retries", help="Total delivery attempts for HTTP outputs."
    ),
    dead_letter_path: str = typer.Option(
        "eden-dead-letter.ndjson",
        "--dead-letter-path",
        help="File undeliverable HTTP events are appended to.",
    ),
    realtime: bool = typer.Option(
        True,
        "--realtime/--no-realtime",
        help="Pace emission to real time (default) or emit as fast as possible.",
    ),
    period_summaries: bool = typer.Option(
        True,
        "--period-summaries/--no-period-summaries",
        help="Emit period_summary KPI events at simulated day boundaries.",
    ),
    scenario: List[str] = typer.Option(
        [],
        "--scenario",
        help="Scenario pack to activate at run start (repeatable).",
    ),
    scenario_duration: Optional[int] = typer.Option(
        None,
        "--scenario-duration",
        help="Duration in simulated minutes for all --scenario packs "
        "(default: each pack's own default).",
    ),
) -> None:
    """Run a business simulator and stream events."""
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=duration,
        events_per_second=rate,
        max_events=max_events,
        seed=seed,
        output_mode=output,  # type: ignore[arg-type]
        webhook_url=webhook_url,
        output_batch_size=batch_size,
        output_flush_interval_seconds=flush_interval,
        output_max_retries=max_retries,
        dead_letter_path=dead_letter_path,
        period_summaries_enabled=period_summaries,
        scenario_schedule=_scenario_schedule(scenario, scenario_duration),
    )

    simulator = load_simulator(config.business_type)
    simulator.configure(config)
    simulator.initialize(config.seed)

    adapter = _build_adapter(config)
    if adapter is None:
        raise typer.BadParameter("output mode 'none' is only supported for daemon mode")
    runner = Runner(config, simulator, adapter, realtime=realtime)
    count = runner.run()

    typer.echo(f"Emitted {count} events for '{business_type}'.", err=True)


@app.command()
def daemon(
    business_type: str = typer.Argument(..., help="Business domain to simulate."),
    duration: float = typer.Option(
        0.0, "--duration", "-d", help="Stop after this many simulated seconds (0 = unlimited)."
    ),
    rate: float = typer.Option(
        2.0, "--rate", "-r", help="Events per simulated second."
    ),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed for determinism."),
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite', 'ndjson', 'memory'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
    stream_id: Optional[str] = typer.Option(
        None, "--stream-id", help="Stream identifier for replay/resume."
    ),
    output: str = typer.Option(
        "ndjson",
        "--output",
        "-o",
        help="Output adapter: 'ndjson', 'http', 'http-batch', or 'none'. HTTP "
        "outputs send 'Authorization: Bearer <token>' when the "
        "EDEN_WEBHOOK_TOKEN environment variable is set.",
    ),
    webhook_url: Optional[str] = typer.Option(
        None,
        "--webhook-url",
        help="Webhook URL when output is 'http' or 'http-batch'.",
    ),
    batch_size: int = typer.Option(
        50, "--batch-size", help="Events per POST for 'http-batch' output."
    ),
    flush_interval: float = typer.Option(
        5.0,
        "--flush-interval",
        help="Seconds before a partial 'http-batch' buffer is flushed.",
    ),
    max_retries: int = typer.Option(
        3, "--max-retries", help="Total delivery attempts for HTTP outputs."
    ),
    dead_letter_path: str = typer.Option(
        "eden-dead-letter.ndjson",
        "--dead-letter-path",
        help="File undeliverable HTTP events are appended to.",
    ),
    checkpoint_interval: float = typer.Option(
        30.0, "--checkpoint-interval", help="Seconds between checkpoints."
    ),
    checkpoint_events: int = typer.Option(
        100, "--checkpoint-events", help="Events between checkpoints."
    ),
    max_events: int = typer.Option(
        0, "--max-events", help="Stop after this many events (0 = unlimited)."
    ),
    position_snapshot_every_events: int = typer.Option(
        0,
        "--position-snapshot-every-events",
        help="Emit a state_snapshot_recorded position event every N events (0 = off).",
    ),
    period_summaries: bool = typer.Option(
        True,
        "--period-summaries/--no-period-summaries",
        help="Emit period_summary KPI events at simulated day boundaries.",
    ),
    scenario: List[str] = typer.Option(
        [],
        "--scenario",
        help="Scenario pack to activate at run start (repeatable).",
    ),
    scenario_duration: Optional[int] = typer.Option(
        None,
        "--scenario-duration",
        help="Duration in simulated minutes for all --scenario packs "
        "(default: each pack's own default).",
    ),
    realtime: bool = typer.Option(
        True,
        "--realtime/--no-realtime",
        help="Pace generation against wall-clock time.",
    ),
) -> None:
    """Run a simulator continuously, persisting events until SIGTERM/SIGINT or max-events."""
    stream_id_value = _make_stream_id(business_type, seed, stream_id)
    if storage_uri is None:
        if storage == "sqlite":
            storage_uri = f"{stream_id_value}.db"
        elif storage == "ndjson":
            storage_uri = f"{stream_id_value}.jsonl"

    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=duration,
        events_per_second=rate,
        max_events=max_events,
        seed=seed,
        output_mode=output,  # type: ignore[arg-type]
        webhook_url=webhook_url,
        output_batch_size=batch_size,
        output_flush_interval_seconds=flush_interval,
        output_max_retries=max_retries,
        dead_letter_path=dead_letter_path,
        storage_backend=storage,  # type: ignore[arg-type]
        storage_uri=storage_uri,
        stream_id=stream_id_value,
        checkpoint_interval_seconds=checkpoint_interval,
        checkpoint_interval_events=checkpoint_events,
        position_snapshot_every_events=position_snapshot_every_events,
        period_summaries_enabled=period_summaries,
        scenario_schedule=_scenario_schedule(scenario, scenario_duration),
    )

    simulator = load_simulator(config.business_type)
    simulator.configure(config)
    simulator.initialize(config.seed)

    storage_adapter = load_storage_adapter(storage, storage_uri, stream_id_value)

    if output in ("http", "http-batch") and not webhook_url:
        raise typer.BadParameter(
            f"--webhook-url is required when output is '{output}'"
        )
    output_adapter = _build_adapter(config)

    runner = ContinuousRunner(
        config,
        simulator,
        storage_adapter,
        output=output_adapter,
        realtime=realtime,
        # Phase 5: a live daemon polls its storage command table
        # between ticks (run id = stream id) so external operators
        # (Beth via the `command` verb) can drive it.
        command_polling=True,
    )
    count = runner.run()
    typer.echo(
        f"Daemon emitted {count} events for '{business_type}' stream '{stream_id_value}'.",
        err=True,
    )


@app.command()
def replay(
    stream_id: str = typer.Argument(..., help="Stream identifier to replay."),
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite' or 'ndjson'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
    from_sequence: Optional[int] = typer.Option(
        None, "--from-sequence", help="First sequence to replay (inclusive)."
    ),
    to_sequence: Optional[int] = typer.Option(
        None, "--to-sequence", help="Last sequence to replay (inclusive)."
    ),
    speed: float = typer.Option(
        1.0, "--speed", help="Replay speed multiplier (1.0 = real time)."
    ),
    output: str = typer.Option(
        "ndjson", "--output", "-o", help="Output adapter: 'ndjson' or 'http'."
    ),
    webhook_url: Optional[str] = typer.Option(
        None, "--webhook-url", help="Webhook URL when output is 'http'."
    ),
) -> None:
    """Replay stored events at original or scaled speed."""
    if storage_uri is None:
        if storage == "sqlite":
            storage_uri = f"{stream_id}.db"
        elif storage == "ndjson":
            storage_uri = f"{stream_id}.jsonl"

    storage_adapter = load_storage_adapter(storage, storage_uri or "memory://", stream_id)

    output_adapter: NDJsonOutputAdapter | HttpOutputAdapter | None = None
    if output == "ndjson":
        output_adapter = NDJsonOutputAdapter()
    elif output == "http":
        if not webhook_url:
            raise typer.BadParameter("--webhook-url is required when output is 'http'")
        output_adapter = HttpOutputAdapter(webhook_url)

    if speed <= 0:
        raise typer.BadParameter("--speed must be greater than 0")

    output_count = 0
    previous_timestamp = None
    try:
        for record in storage_adapter.read_from(from_sequence=from_sequence):
            if to_sequence is not None and record.sequence > to_sequence:
                break
            if previous_timestamp is not None:
                delta = (record.envelope.timestamp - previous_timestamp).total_seconds()
                sleep_for = max(0.0, delta) / speed
                if sleep_for > 0:
                    time.sleep(sleep_for)
            if output_adapter is not None:
                output_adapter.write(record.envelope)
            output_count += 1
            previous_timestamp = record.envelope.timestamp
    finally:
        if output_adapter is not None:
            try:
                output_adapter.close()
            except Exception:
                pass
        storage_adapter.close()

    typer.echo(f"Replayed {output_count} events for stream '{stream_id}'.", err=True)


@app.command()
def status(
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite' or 'ndjson'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
) -> None:
    """List streams and their latest sequences."""
    if storage_uri is None:
        if storage == "sqlite":
            storage_uri = "eden_business_simulator.db"
        elif storage == "ndjson":
            typer.echo("--storage-uri is required for ndjson status.", err=True)
            raise typer.Exit(code=1)
        else:
            storage_uri = "memory://"

    adapter = load_storage_adapter(storage, storage_uri, "")
    try:
        stream_ids = adapter.stream_ids()
        if not stream_ids:
            typer.echo("No streams found.")
            return
        for stream_id in stream_ids:
            per_stream = load_storage_adapter(storage, storage_uri, stream_id)
            try:
                latest_sequence = per_stream.latest_sequence()
                checkpoint = per_stream.read_checkpoint()
                cp_seq = checkpoint["last_sequence"] if checkpoint else "none"
                typer.echo(
                    f"stream={stream_id} latest_sequence={latest_sequence} checkpoint={cp_seq}"
                )
            finally:
                per_stream.close()
    finally:
        adapter.close()


@app.command()
def checkpoint(
    business_type: str = typer.Argument(..., help="Business domain to simulate."),
    stream_id: str = typer.Argument(..., help="Stream identifier to checkpoint."),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed for determinism."),
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite' or 'ndjson'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
) -> None:
    """Force a snapshot/checkpoint for an existing stream."""
    if storage_uri is None:
        if storage == "sqlite":
            storage_uri = f"{stream_id}.db"
        elif storage == "ndjson":
            storage_uri = f"{stream_id}.jsonl"

    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=0.0,
        events_per_second=1.0,
        seed=seed,
        storage_backend=storage,  # type: ignore[arg-type]
        storage_uri=storage_uri,
        stream_id=stream_id,
    )

    simulator = load_simulator(config.business_type)
    simulator.configure(config)
    simulator.initialize(config.seed)

    storage_adapter = load_storage_adapter(storage, storage_uri, stream_id)
    try:
        last_sequence = storage_adapter.latest_sequence()
        snapshot_data = {
            "seed": config.seed,
            "business_type": config.business_type,
            "data": simulator.state_snapshot(),
        }
        storage_adapter.write_snapshot("latest", snapshot_data)
        storage_adapter.write_checkpoint(last_sequence)
        typer.echo(
            f"Checkpointed stream '{stream_id}' at sequence {last_sequence}.",
            err=True,
        )
    finally:
        storage_adapter.close()


@app.command()
def position(
    stream_id: str = typer.Option(
        ..., "--stream-id", help="Stream identifier to read the position for."
    ),
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite', 'ndjson', or 'memory'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
) -> None:
    """Print the stored current position for a stream as JSON.

    Reads the ``position`` snapshot written by the daemon at each
    checkpoint (Phase 1 state read API).
    """
    if storage_uri is None:
        if storage == "sqlite":
            storage_uri = "eden_business_simulator.db"
        elif storage == "ndjson":
            typer.echo("--storage-uri is required for ndjson position.", err=True)
            raise typer.Exit(code=1)
        else:
            storage_uri = "memory://"

    adapter = load_storage_adapter(storage, storage_uri, stream_id)
    try:
        stored = adapter.read_snapshot(ContinuousRunner.POSITION_KEY)
        if stored is None:
            typer.echo(
                f"No position stored for stream '{stream_id}'. "
                "Run the daemon for that stream first so it checkpoints.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(json.dumps(stored, indent=2, default=str))
    finally:
        adapter.close()


@app.command()
def verify(
    stream_id: str = typer.Option(
        ..., "--stream-id", help="Stream identifier to verify."
    ),
    storage: str = typer.Option(
        "sqlite",
        "--storage",
        help="Storage backend: 'sqlite' or 'ndjson' (memory runs are "
        "per-process and cannot be verified after the fact).",
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
    check_determinism: bool = typer.Option(
        False,
        "--check-determinism",
        help="Also re-run the business from its recorded seed and diff "
        "the streams (skipped, with the reason, when the run's inputs "
        "are not recoverable from storage).",
    ),
) -> None:
    """Verify a stored run by independent recomputation (Phase 8).

    Recomputes period-summary KPIs and stream-derivable position
    figures from the raw stored events and compares them against what
    the run stored about itself. Prints one PASS/FAIL/SKIP line per
    check and exits non-zero if any check fails.
    """
    from eden_business_simulator.framework.verify import verify_run

    if storage_uri is None:
        if storage == "sqlite":
            storage_uri = "eden_business_simulator.db"
        elif storage == "ndjson":
            typer.echo("--storage-uri is required for ndjson verify.", err=True)
            raise typer.Exit(code=1)

    try:
        report = verify_run(
            storage,
            storage_uri,
            stream_id,
            check_determinism_flag=check_determinism,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    for result in report.checks:
        typer.echo(f"{result.status} {result.name} — {result.detail}")
    for field_name in report.unverifiable_fields:
        from eden_business_simulator.framework.verify import unverifiable_reason

        typer.echo(
            f"UNVERIFIABLE position.{field_name} — "
            f"{unverifiable_reason(report.business_type, field_name)}"
        )
    verdict = "PASS" if report.ok else "FAIL"
    typer.echo(f"VERDICT: {verdict}")
    if not report.ok:
        raise typer.Exit(code=1)


def _command_storage_uri(storage: str, storage_uri: Optional[str]) -> str:
    """Resolve the storage URI for the command verbs.

    Mirrors the ``position`` verb's conventions: SQLite defaults to
    the shared ``eden_business_simulator.db`` file; NDJSON needs an
    explicit ``--storage-uri`` (the daemon's events file).
    """
    if storage_uri is not None:
        return storage_uri
    if storage == "sqlite":
        return "eden_business_simulator.db"
    if storage == "ndjson":
        typer.echo("--storage-uri is required for ndjson commands.", err=True)
        raise typer.Exit(code=1)
    return "memory://"


@app.command(name="command")
def submit_command(
    business_type: str = typer.Argument(
        ..., help="Business domain the run belongs to (e.g. cafe)."
    ),
    run_id: str = typer.Option(
        ..., "--run-id", help="Run (stream) identifier the command targets."
    ),
    action_type: str = typer.Option(
        ..., "--type", help="Action type to apply (e.g. place_supplier_order)."
    ),
    params: str = typer.Option(
        "{}", "--params", help="Action parameters as a JSON object string."
    ),
    requested_by: Optional[str] = typer.Option(
        None, "--requested-by", help="Who is asking for the action."
    ),
    approved_by: Optional[str] = typer.Option(
        None,
        "--approved-by",
        help="Approval stamp; required for actions flagged requires_approval.",
    ),
    action_id: Optional[str] = typer.Option(
        None,
        "--action-id",
        help="Idempotency key (defaults to the generated command id).",
    ),
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite', 'ndjson', or 'memory'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
) -> None:
    """Submit a management command to a daemon run's command table.

    The command is stored as ``pending``; a running daemon for that
    run picks it up between ticks, applies or rejects it through the
    normal action pipeline, and records the outcome on the record.
    """
    if business_type not in list_business_types():
        available = ", ".join(list_business_types())
        typer.echo(
            f"Unknown business_type '{business_type}'. Available: {available}",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        parsed_params = json.loads(params)
    except json.JSONDecodeError as exc:
        typer.echo(f"--params is not valid JSON: {exc}", err=True)
        raise typer.Exit(code=1)
    if not isinstance(parsed_params, dict):
        typer.echo("--params must be a JSON object.", err=True)
        raise typer.Exit(code=1)

    uri = _command_storage_uri(storage, storage_uri)
    adapter = load_storage_adapter(storage, uri, run_id)
    try:
        command_id = adapter.submit_command(
            run_id,
            action_type,
            parsed_params,
            requested_by=requested_by,
            approved_by=approved_by,
            action_id=action_id,
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        record = adapter.list_commands(run_id)[-1]
        typer.echo(
            f"command={command_id} run_id={run_id} "
            f"action_id={record['action_id']} status=pending"
        )
    finally:
        adapter.close()


@app.command(name="commands")
def list_commands(
    business_type: str = typer.Argument(
        ..., help="Business domain the run belongs to (e.g. cafe)."
    ),
    run_id: str = typer.Option(
        ..., "--run-id", help="Run (stream) identifier to list commands for."
    ),
    status: Optional[str] = typer.Option(
        None, "--status", help="Filter: 'pending', 'applied', or 'rejected'."
    ),
    storage: str = typer.Option(
        "sqlite", "--storage", help="Storage backend: 'sqlite', 'ndjson', or 'memory'."
    ),
    storage_uri: Optional[str] = typer.Option(
        None, "--storage-uri", help="Storage URI or file path."
    ),
) -> None:
    """List a run's commands in submission order."""
    if business_type not in list_business_types():
        available = ", ".join(list_business_types())
        typer.echo(
            f"Unknown business_type '{business_type}'. Available: {available}",
            err=True,
        )
        raise typer.Exit(code=1)
    if status is not None and status not in ("pending", "applied", "rejected"):
        typer.echo(
            f"--status must be one of: pending, applied, rejected (got '{status}').",
            err=True,
        )
        raise typer.Exit(code=1)

    uri = _command_storage_uri(storage, storage_uri)
    adapter = load_storage_adapter(storage, uri, run_id)
    try:
        records = adapter.list_commands(run_id, status=status)
        if not records:
            typer.echo("No commands found.")
            return
        for record in records:
            line = (
                f"{record['command_id']} action_id={record['action_id']} "
                f"type={record['action_type']} status={record['status']}"
            )
            result = record.get("result") or {}
            if record["status"] == "rejected" and result.get("reason"):
                line += f" reason={result['reason']}"
            typer.echo(line)
    finally:
        adapter.close()


@app.command()
def list_types() -> None:
    """Show available business types."""
    for name in list_business_types():
        typer.echo(name)


if __name__ == "__main__":
    app()
