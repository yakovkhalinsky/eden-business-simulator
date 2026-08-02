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
    assert "gym" in result.output
    assert "logistics" in result.output
    assert "field_service" in result.output
    assert "clinic" in result.output


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


def test_run_field_service_fast():
    result = runner.invoke(
        app,
        [
            "run",
            "field_service",
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
    assert "Emitted" in result.output


def test_run_clinic_fast():
    result = runner.invoke(
        app,
        [
            "run",
            "clinic",
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
    assert "Emitted" in result.output


def test_run_unknown_business():
    result = runner.invoke(app, ["run", "healthcare"])
    assert result.exit_code != 0
    assert result.exception is not None
    assert "Unknown business_type" in str(result.exception)


def test_run_gym_fast():
    result = runner.invoke(
        app,
        [
            "run",
            "gym",
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
    assert "Emitted" in result.output


def test_run_logistics_fast():
    result = runner.invoke(
        app,
        [
            "run",
            "logistics",
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
    assert "Emitted" in result.output


def test_daemon_and_replay(tmp_path):
    db_path = tmp_path / "stream.db"
    result = runner.invoke(
        app,
        [
            "daemon",
            "gym",
            "--storage",
            "sqlite",
            "--storage-uri",
            str(db_path),
            "--stream-id",
            "daemon_test_stream",
            "--rate",
            "100",
            "--seed",
            "1",
            "--no-realtime",
            "--output",
            "none",
            "--checkpoint-events",
            "5",
            "--max-events",
            "10",
        ],
    )
    assert result.exit_code == 0
    assert "daemon_test_stream" in result.output

    replay_result = runner.invoke(
        app,
        [
            "replay",
            "daemon_test_stream",
            "--storage",
            "sqlite",
            "--storage-uri",
            str(db_path),
            "--output",
            "ndjson",
        ],
    )
    assert replay_result.exit_code == 0
    assert "Replayed" in replay_result.output
    assert "10 events" in replay_result.output


def test_checkpoint_command(tmp_path):
    db_path = tmp_path / "cp.db"
    result = runner.invoke(
        app,
        [
            "checkpoint",
            "gym",
            "cp_stream",
            "--storage",
            "sqlite",
            "--storage-uri",
            str(db_path),
            "--seed",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert "Checkpointed stream 'cp_stream'" in result.output
