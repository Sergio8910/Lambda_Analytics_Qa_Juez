"""
Shared pytest fixtures for all tests.
"""

import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Use in-memory SQLite for tests (fast, isolated)
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_db_engine():
    """Create test database engine (session-scoped)"""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create all tables
    from evaluation.control_plane.models import Base
    Base.metadata.create_all(bind=engine)
    
    yield engine
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def test_db_session(test_db_engine):
    """Create test database session (function-scoped, isolated)"""
    TestSessionLocal = sessionmaker(bind=test_db_engine)
    session = TestSessionLocal()
    
    yield session
    
    # Cleanup
    session.rollback()
    session.close()


@pytest.fixture
def sample_tenant(test_db_session):
    """Create a sample tenant for testing"""
    from evaluation.control_plane.models import Tenant
    
    tenant = Tenant(
        tenant_id="test-tenant",
        name="Test Tenant",
        status="active",
        rate_limit_jobs_per_minute=600,
        rate_limit_api_per_minute=6000,
    )
    test_db_session.add(tenant)
    test_db_session.commit()
    return tenant


@pytest.fixture
def sample_dataset(test_db_session, sample_tenant):
    """Create a sample dataset"""
    from evaluation.control_plane.models import Dataset
    
    dataset = Dataset(
        dataset_id="test-dataset-v1",
        tenant_id=sample_tenant.tenant_id,
        version="1.0",
        cases_count=50,
        hash="abc123def456",
        description="Test dataset",
        scenarios=["happy_path", "edge_case"],
    )
    test_db_session.add(dataset)
    test_db_session.commit()
    return dataset


@pytest.fixture
def sample_suite(test_db_session, sample_tenant):
    """Create a sample suite"""
    from evaluation.control_plane.models import Suite
    
    suite = Suite(
        suite_id="test-quality-suite",
        tenant_id=sample_tenant.tenant_id,
        kind="quality",
        version="1.0",
        datasets=["test-dataset-v1"],
        scenarios=["quality", "consistency"],
        sample_size=50,
        thresholds={
            "answer_relevancy": 0.8,
            "faithfulness": 0.9,
            "task_success": 0.85,
        },
        release_gate_enabled=True,
        release_gate_rule={
            "pass_rate_min": 0.9,
            "quality_delta_max": 0.05,
        },
    )
    test_db_session.add(suite)
    test_db_session.commit()
    return suite


# Mark all tests in tests/test_control_plane as unit tests
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
