from __future__ import annotations

import pytest


@pytest.mark.integration
def test_database_loader_requires_configured_environment():
    pytest.skip("Requires a configured SQL Server test environment and DB connection string.")
