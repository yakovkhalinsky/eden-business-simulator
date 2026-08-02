"""Tests for the CLI."""

from typer.testing import CliRunner

from eden_business_simulator.cli import app

runner = CliRunner()


def test_list_types():
    result = runner.invoke(app, ["list-types"])
    assert result.exit_code == 0
    assert "ecommerce" in result.output
    assert "saas" in result.output
    assert "cafe" in result.output


def test_run_cafe_fast():
    result = runner.invoke(
        app,
        [
            "run",
            "cafe",
            "--duration",
            "0.2",
            "--rate",
            "10",
            "--seed",
            "1",
            "--no-realtime",
        ],
    )
    assert result.exit_code == 0
    assert "Emitted 2 events" in result.output


def test_run_ecommerce_fast():
    result = runner.invoke(
        app,
        [
            "run",
            "ecommerce",
            "--duration",
            "0.2",
            "--rate",
            "10",
            "--seed",
            "1",
            "--no-realtime",
        ],
    )
    assert result.exit_code == 0
    assert "Emitted 2 events" in result.output


def test_run_unknown_business():
    result = runner.invoke(app, ["run", "healthcare"])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "Unknown business_type" in str(result.exception)
