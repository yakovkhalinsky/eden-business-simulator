"""Lightweight checks that documentation files exist and reference the code correctly."""

from __future__ import annotations

import pathlib

import pytest

from eden_business_simulator.businesses import list_business_types, load_simulator
from eden_business_simulator.config import SimulatorConfig


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
BUSINESS_DOCS_DIR = DOCS_DIR / "businesses"
README_PATH = REPO_ROOT / "README.md"


@pytest.mark.parametrize(
    "path",
    [
        DOCS_DIR / "setup.md",
        DOCS_DIR / "architecture.md",
        DOCS_DIR / "adding_a_business.md",
    ],
)
def test_core_documentation_files_exist(path: pathlib.Path) -> None:
    assert path.exists(), f"Missing documentation file: {path}"


def test_readme_links_to_documentation() -> None:
    readme = README_PATH.read_text()
    for expected in [
        "docs/setup.md",
        "docs/businesses/",
        "docs/architecture.md",
        "docs/adding_a_business.md",
    ]:
        assert expected in readme, f"README.md does not link to {expected}"


@pytest.mark.parametrize("business_type", list_business_types())
def test_business_documentation_file_exists(business_type: str) -> None:
    doc_path = BUSINESS_DOCS_DIR / f"{business_type}.md"
    assert doc_path.exists(), f"Missing business documentation: {doc_path}"


@pytest.mark.parametrize("business_type", list_business_types())
def test_business_documentation_lists_all_event_types(business_type: str) -> None:
    doc_path = BUSINESS_DOCS_DIR / f"{business_type}.md"
    doc_text = doc_path.read_text()

    config = SimulatorConfig(business_type=business_type, duration_seconds=1.0, events_per_second=1.0, seed=1)
    simulator = load_simulator(business_type)
    simulator.configure(config)
    simulator.initialize(config.seed)

    for event_type in simulator.available_event_types():
        # Event type should appear as a markdown code span in the doc.
        assert f"`{event_type}`" in doc_text, (
            f"{doc_path} does not document event type {event_type}"
        )
