"""Command-line interface for the Eden Business Simulator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import time
import typer

from eden_business_simulator.businesses import list_business_types, load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.output.http import HttpOutputAdapter
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
        return HttpOutputAdapter(config.webhook_url)
    if config.output_mode == "none":
        return None
    raise ValueError(f"Unsupported output mode: {config.output_mode}")


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
        "ndjson", "--output", "-o", help="Output adapter: 'ndjson' or 'http'."
    ),
    webhook_url: Optional[str] = typer.Option(
        None, "--webhook-url", "-w", help="Webhook URL when output mode is 'http'."
    ),
    realtime: bool = typer.Option(
        True,
        "--realtime/--no-realtime",
        help="Pace emission to real time (default) or emit as fast as possible.",
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
        "ndjson", "--output", "-o", help="Output adapter: 'ndjson', 'http', or 'none'."
    ),
    webhook_url: Optional[str] = typer.Option(
        None, "--webhook-url", help="Webhook URL when output is 'http'."
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
        storage_backend=storage,  # type: ignore[arg-type]
        storage_uri=storage_uri,
        stream_id=stream_id_value,
        checkpoint_interval_seconds=checkpoint_interval,
        checkpoint_interval_events=checkpoint_events,
    )

    simulator = load_simulator(config.business_type)
    simulator.configure(config)
    simulator.initialize(config.seed)

    storage_adapter = load_storage_adapter(storage, storage_uri, stream_id_value)

    output_adapter: NDJsonOutputAdapter | HttpOutputAdapter | None = None
    if output == "ndjson":
        output_adapter = NDJsonOutputAdapter()
    elif output == "http":
        if not webhook_url:
            raise typer.BadParameter("--webhook-url is required when output is 'http'")
        output_adapter = HttpOutputAdapter(webhook_url)

    runner = ContinuousRunner(
        config,
        simulator,
        storage_adapter,
        output=output_adapter,
        realtime=realtime,
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
def list_types() -> None:
    """Show available business types."""
    for name in list_business_types():
        typer.echo(name)


if __name__ == "__main__":
    app()
