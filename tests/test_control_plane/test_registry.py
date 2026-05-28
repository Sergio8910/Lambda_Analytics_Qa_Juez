"""
Tests for control plane registry.
"""

import pytest
from evaluation.control_plane.registry import (
    JobRegistry,
    DatasetRegistry,
    SuiteRegistry,
    BaselineRegistry,
    ResultRegistry,
)
from evaluation.control_plane.models import JobStatusEnum, JobKindEnum


@pytest.mark.unit
class TestJobRegistry:
    """Test job registry operations"""
    
    def test_submit_job(self, test_db_session, sample_tenant):
        """Test submitting a new job"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={"test": "spec"},
            app_id="test-app",
            base_url="http://localhost",
        )
        
        assert job.job_id is not None
        assert job.status == JobStatusEnum.QUEUED
        assert job.app_id == "test-app"
        assert job.kind == JobKindEnum.QUALITY
    
    def test_get_job(self, test_db_session, sample_tenant):
        """Test retrieving a job"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.SMOKE,
            spec_json={},
        )
        
        retrieved = registry.get_job(job.job_id)
        assert retrieved is not None
        assert retrieved.job_id == job.job_id
    
    def test_list_jobs(self, test_db_session, sample_tenant):
        """Test listing jobs"""
        registry = JobRegistry(test_db_session)
        
        # Create multiple jobs
        job1 = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        job2 = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.RAG,
            spec_json={},
        )
        
        jobs = registry.list_jobs(tenant_id=sample_tenant.tenant_id)
        assert len(jobs) >= 2
        job_ids = [j.job_id for j in jobs]
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids
    
    def test_list_jobs_by_status(self, test_db_session, sample_tenant):
        """Test filtering jobs by status"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        queued = registry.list_jobs(
            tenant_id=sample_tenant.tenant_id,
            status=JobStatusEnum.QUEUED
        )
        assert len(queued) >= 1
        
        running = registry.list_jobs(
            tenant_id=sample_tenant.tenant_id,
            status=JobStatusEnum.RUNNING
        )
        assert job.job_id not in [j.job_id for j in running]
    
    def test_update_job_status(self, test_db_session, sample_tenant):
        """Test updating job status"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        updated = registry.update_job_status(
            job.job_id,
            JobStatusEnum.RUNNING,
            phase="evaluation",
            completed_cases=10,
            total_cases=50,
        )
        
        assert updated.status == JobStatusEnum.RUNNING
        assert updated.phase == "evaluation"
        assert updated.completed_cases == 10
        assert updated.started_at is not None
    
    def test_job_timestamps(self, test_db_session, sample_tenant):
        """Test job timestamp updates"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        created_at = job.created_at
        submitted_at = job.submitted_at
        
        # Move to running
        registry.update_job_status(job.job_id, JobStatusEnum.RUNNING)
        job = registry.get_job(job.job_id)
        assert job.started_at is not None
        assert job.started_at >= created_at
        
        # Move to completed
        registry.update_job_status(job.job_id, JobStatusEnum.SUCCEEDED)
        job = registry.get_job(job.job_id)
        assert job.completed_at is not None
        assert job.completed_at >= job.started_at


@pytest.mark.unit
class TestDatasetRegistry:
    """Test dataset registry operations"""
    
    def test_register_dataset(self, test_db_session, sample_tenant):
        """Test registering a dataset"""
        registry = DatasetRegistry(test_db_session)
        
        dataset = registry.register_dataset(
            dataset_id="test-ds-1",
            tenant_id=sample_tenant.tenant_id,
            version="1.0",
            cases_count=100,
            hash="sha256xyz",
            description="Test dataset",
        )
        
        assert dataset.dataset_id == "test-ds-1"
        assert dataset.cases_count == 100
    
    def test_get_dataset(self, test_db_session, sample_dataset):
        """Test retrieving a dataset"""
        registry = DatasetRegistry(test_db_session)
        
        retrieved = registry.get_dataset(
            sample_dataset.dataset_id,
            version=sample_dataset.version
        )
        
        assert retrieved is not None
        assert retrieved.cases_count == sample_dataset.cases_count
    
    def test_list_datasets(self, test_db_session, sample_tenant, sample_dataset):
        """Test listing datasets"""
        registry = DatasetRegistry(test_db_session)
        
        datasets = registry.list_datasets(sample_tenant.tenant_id)
        assert len(datasets) >= 1
        
        dataset_ids = [d.dataset_id for d in datasets]
        assert sample_dataset.dataset_id in dataset_ids
    
    def test_get_dataset_by_hash(self, test_db_session, sample_dataset):
        """Test retrieving dataset by hash"""
        registry = DatasetRegistry(test_db_session)
        
        retrieved = registry.get_dataset_by_hash(sample_dataset.hash)
        assert retrieved is not None
        assert retrieved.dataset_id == sample_dataset.dataset_id


@pytest.mark.unit
class TestSuiteRegistry:
    """Test suite registry operations"""
    
    def test_register_suite(self, test_db_session, sample_tenant):
        """Test registering a suite"""
        registry = SuiteRegistry(test_db_session)
        
        suite = registry.register_suite(
            suite_id="test-suite-1",
            tenant_id=sample_tenant.tenant_id,
            kind="quality",
            version="1.0",
            datasets=["ds-1", "ds-2"],
            scenarios=["happy_path", "edge"],
            sample_size=50,
            thresholds={"answer_relevancy": 0.8},
        )
        
        assert suite.suite_id == "test-suite-1"
        assert suite.kind == "quality"
    
    def test_load_suite(self, test_db_session, sample_suite):
        """Test loading a suite"""
        registry = SuiteRegistry(test_db_session)
        
        loaded = registry.load_suite(sample_suite.suite_id)
        assert loaded is not None
        assert loaded.kind == sample_suite.kind
    
    def test_list_suites(self, test_db_session, sample_tenant, sample_suite):
        """Test listing suites"""
        registry = SuiteRegistry(test_db_session)
        
        suites = registry.list_suites(sample_tenant.tenant_id)
        assert len(suites) >= 1
        
        suite_ids = [s.suite_id for s in suites]
        assert sample_suite.suite_id in suite_ids
    
    def test_get_suite_by_kind(self, test_db_session, sample_suite):
        """Test getting suite by kind"""
        registry = SuiteRegistry(test_db_session)
        
        suite = registry.get_suite_by_kind(
            sample_suite.tenant_id,
            "quality"
        )
        
        assert suite is not None
        assert suite.kind == "quality"


@pytest.mark.unit
class TestBaselineRegistry:
    """Test baseline registry operations"""
    
    def test_create_baseline(self, test_db_session, sample_tenant):
        """Test creating a baseline"""
        registry = BaselineRegistry(test_db_session)
        
        baseline = registry.create_baseline(
            tenant_id=sample_tenant.tenant_id,
            kind="quality",
            metrics={
                "quality_score": 0.92,
                "answer_relevancy": 0.88,
                "faithfulness": 0.95,
                "latency_p95": 1200,
            },
            versions={
                "model_version": "gpt-4o-2026-04",
                "rag_index_version": "v1",
            },
            num_cases=50,
        )
        
        assert baseline.quality_score == 0.92
        assert baseline.latency_p95 == 1200
    
    def test_get_latest_baseline(self, test_db_session, sample_tenant):
        """Test getting latest baseline"""
        registry = BaselineRegistry(test_db_session)
        
        baseline1 = registry.create_baseline(
            tenant_id=sample_tenant.tenant_id,
            kind="quality",
            metrics={"quality_score": 0.85},
            versions={},
            num_cases=50,
        )
        
        baseline2 = registry.create_baseline(
            tenant_id=sample_tenant.tenant_id,
            kind="quality",
            metrics={"quality_score": 0.92},
            versions={},
            num_cases=60,
        )
        
        latest = registry.get_latest_baseline(
            sample_tenant.tenant_id,
            "quality"
        )
        
        # Latest should be the most recently created
        assert latest is not None
        assert latest.quality_score == 0.92
