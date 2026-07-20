import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from prefect.testing.utilities import prefect_test_harness


@pytest.fixture(scope="session", autouse=True)
def _prefect_test_fixture():
    """Run all Prefect flows against a temporary, local-only test database.

    Prevents any attempt to reach Prefect Cloud or a persistent local server
    and keeps the pipeline fully offline/ephemeral during the test suite.
    """
    with prefect_test_harness():
        yield
