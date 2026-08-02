"""Command-line interface for the Eden Business Simulator."""

from __future__ import annotations

from typing import Optional

import typer

from eden_business_simulator.businesses import list_business_types, load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.output.http import HttpOutputAdapter
from eden_business_simulator.output.ndjson import NDJsonOutputAdapter
from eden_business_simulator.runner import Runner

app = typer.Typer(
    name="eden-business-simulator",
    help="Generate realistic business event streams for tool evaluation.",
)


def _build_adapter(config: SimulatorConfig):
    if config.output_mode == "ndjson":
        return NDJsonOutputAdapter()
    if config.output_mode == "http":
        return HttpOutputAdapter(config.webhook_url)
    raise ValueError(f"Unsupported output mode: {config.output_mode}")


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
    runner = Runner(config, simulator, adapter, realtime=realtime)
    count = runner.run()

    typer.echo(f"Emitted {count} events for '{business_type}'.", err=True)


@app.command()
def list_types() -> None:
    """Show available business types."""
    for name in list_business_types():
        typer.echo(name)


if __name__ == "__main__":
    app()
