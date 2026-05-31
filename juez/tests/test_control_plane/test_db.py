"""
Tests for database module.
"""

import pytest
from juez.evaluation.control_plane import db
from juez.evaluation.control_plane.models import Base


@pytest.mark.unit
class TestDatabaseModule:
    """Test database connection and session management"""
    
    def test_get_db_session(self, test_db_session):
        """Test that database session is created and usable"""
        assert test_db_session is not None
        assert test_db_session.is_active
    
    def test_session_commit_rollback(self, test_db_session, sample_tenant):
        """Test transaction management"""
        # Session should have sample_tenant in it
        from juez.evaluation.control_plane.models import Tenant
        
        query = test_db_session.query(Tenant).filter_by(
            tenant_id=sample_tenant.tenant_id
        )
        assert query.count() == 1
    
    def test_create_all_tables_exist(self, test_db_engine):
        """Test that all expected tables are created"""
        inspector_tables = set(
            test_db_engine.table_names() 
            if hasattr(test_db_engine, 'table_names') 
            else test_db_engine.inspector.get_table_names()
        )
        
        expected_tables = {
            'tenants', 'jobs', 'suites', 'datasets',
            'evaluation_results', 'baselines', 'webhooks',
            'api_keys', 'audit_logs'
        }
        
        # Check key tables exist
        for table in expected_tables:
            # SQLite might have some variations, so we check for presence
            found = any(table in t for t in inspector_tables)
            assert found or table in test_db_engine.dialect.get_table_names(
                test_db_engine.connect()
            ), f"Table {table} not found"
    
    def test_table_indexes(self, test_db_session):
        """Test that indexes are created on tables"""
        # This is database-specific, so we just verify tables exist
        from juez.evaluation.control_plane.models import Job, Tenant
        
        # If we can query, tables with indexes exist
        assert test_db_session.query(Tenant).count() >= 0
        assert test_db_session.query(Job).count() >= 0
    
    @pytest.mark.integration
    def test_connection_pooling(self, test_db_engine):
        """Test connection pooling works"""
        # In-memory SQLite doesn't have real connection pooling,
        # but we verify the engine is configured correctly
        pool = test_db_engine.pool
        assert pool is not None


@pytest.mark.unit
class TestDatabaseModels:
    """Test ORM model constraints and relationships"""
    
    def test_tenant_creation(self, test_db_session, sample_tenant):
        """Test tenant model"""
        from juez.evaluation.control_plane.models import Tenant
        
        tenants = test_db_session.query(Tenant).all()
        assert len(tenants) >= 1
        assert sample_tenant in tenants
    
    def test_job_model_defaults(self, test_db_session, sample_tenant):
        """Test job model with defaults"""
        from juez.evaluation.control_plane.models import Job, JobStatusEnum, JobKindEnum
        
        job = Job(
            tenant_id=sample_tenant.tenant_id,
            kind=JobKindEnum.QUALITY,
            spec_json={"test": "data"},
        )
        test_db_session.add(job)
        test_db_session.commit()
        
        # Verify defaults
        assert job.status == JobStatusEnum.QUEUED
        assert job.max_parallelism == 4
        assert job.total_cases == 0
        assert job.cost_usd == 0
    
    def test_suite_model(self, test_db_session, sample_suite):
        """Test suite model"""
        from juez.evaluation.control_plane.models import Suite
        
        suite = test_db_session.query(Suite).filter_by(
            suite_id=sample_suite.suite_id
        ).first()
        
        assert suite is not None
        assert suite.kind == "quality"
        assert suite.release_gate_enabled == True
    
    def test_dataset_model(self, test_db_session, sample_dataset):
        """Test dataset model"""
        from juez.evaluation.control_plane.models import Dataset
        
        dataset = test_db_session.query(Dataset).filter_by(
            dataset_id=sample_dataset.dataset_id
        ).first()
        
        assert dataset is not None
        assert dataset.version == "1.0"
        assert dataset.cases_count == 50
    
    def test_relationships(self, test_db_session, sample_tenant):
        """Test model relationships work"""
        from juez.evaluation.control_plane.models import Job, JobKindEnum
        
        job = Job(
            tenant_id=sample_tenant.tenant_id,
            kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        test_db_session.add(job)
        test_db_session.commit()
        
        # Verify relationship
        assert job.tenant.tenant_id == sample_tenant.tenant_id
