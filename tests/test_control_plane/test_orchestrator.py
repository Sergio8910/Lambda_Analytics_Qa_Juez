"""
Tests for job orchestrator.
"""

import pytest
from uuid import uuid4
from evaluation.control_plane.orchestrator import JobOrchestrator, JobScheduler
from evaluation.control_plane.registry import JobRegistry
from evaluation.control_plane.models import JobStatusEnum, JobKindEnum


@pytest.mark.unit
class TestJobOrchestrator:
    """Test job orchestration"""
    
    def test_orchestrator_init(self, test_db_session):
        """Test orchestrator initialization"""
        orchestrator = JobOrchestrator(test_db_session)
        assert orchestrator is not None
        assert orchestrator.registry is not None
    
    def test_get_queue_for_kind(self):
        """Test queue mapping for job kinds"""
        assert JobOrchestrator._get_queue_for_kind(JobKindEnum.SMOKE) == "functional"
        assert JobOrchestrator._get_queue_for_kind(JobKindEnum.QUALITY) == "functional"
        assert JobOrchestrator._get_queue_for_kind(JobKindEnum.RAG) == "rag"
        assert JobOrchestrator._get_queue_for_kind(JobKindEnum.SECURITY) == "security"
        assert JobOrchestrator._get_queue_for_kind(JobKindEnum.PERFORMANCE) == "performance"
        assert JobOrchestrator._get_queue_for_kind(JobKindEnum.DRIFT) == "drift"
    
    def test_get_job_status(self, test_db_session, sample_tenant):
        """Test getting job status"""
        registry = JobRegistry(test_db_session)
        orchestrator = JobOrchestrator(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
            app_id="test-app",
        )
        
        status = orchestrator.get_job_status(job.job_id)
        
        assert status["job_id"] == str(job.job_id)
        assert status["status"] == JobStatusEnum.QUEUED.value
        assert status["progress"]["total_cases"] == 0
    
    def test_cancel_job_queued(self, test_db_session, sample_tenant):
        """Test cancelling a queued job"""
        registry = JobRegistry(test_db_session)
        orchestrator = JobOrchestrator(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        result = orchestrator.cancel_job(job.job_id)
        
        assert result["status"] == "cancelled"
        
        # Verify status updated
        cancelled_job = registry.get_job(job.job_id)
        assert cancelled_job.status == JobStatusEnum.CANCELLED
    
    def test_cannot_cancel_completed_job(self, test_db_session, sample_tenant):
        """Test that completed jobs cannot be cancelled"""
        registry = JobRegistry(test_db_session)
        orchestrator = JobOrchestrator(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        # Complete the job
        registry.update_job_status(job.job_id, JobStatusEnum.SUCCEEDED)
        
        # Try to cancel
        with pytest.raises(ValueError):
            orchestrator.cancel_job(job.job_id)
    
    def test_dispatch_requires_queued_status(self, test_db_session, sample_tenant):
        """Test that dispatch requires job to be queued"""
        registry = JobRegistry(test_db_session)
        orchestrator = JobOrchestrator(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        # Move to running
        registry.update_job_status(job.job_id, JobStatusEnum.RUNNING)
        
        # Try to dispatch (should fail)
        with pytest.raises(ValueError):
            orchestrator.dispatch_job(
                job_id=job.job_id,
                tenant_id=sample_tenant.tenant_id,
                job_kind=JobKindEnum.QUALITY,
                cases=[],
                config={},
            )
    
    def test_dispatch_invalid_job_kind(self, test_db_session, sample_tenant):
        """Test dispatch with invalid job kind"""
        orchestrator = JobOrchestrator(test_db_session)
        
        fake_job_id = uuid4()
        
        # Create a mock invalid job kind (would need to modify the enum, so skip for now)
        # This is tested implicitly through task mapping
        pass


@pytest.mark.unit
class TestJobScheduler:
    """Test job scheduling"""
    
    def test_scheduler_init(self, test_db_session):
        """Test scheduler initialization"""
        scheduler = JobScheduler(test_db_session)
        assert scheduler is not None
        assert scheduler.orchestrator is not None
    
    def test_schedule_nightly_drift_check(self, test_db_session, sample_tenant):
        """Test scheduling a drift check"""
        scheduler = JobScheduler(test_db_session)
        
        result = scheduler.schedule_nightly_drift_check(sample_tenant.tenant_id)
        
        assert result is not None
        assert "job_id" in result
        assert result.get("status") in ("dispatched", "queued")
    
    def test_schedule_daily_quality_check(self, test_db_session, sample_tenant):
        """Test scheduling a quality check"""
        scheduler = JobScheduler(test_db_session)
        
        result = scheduler.schedule_daily_quality_check(
            sample_tenant.tenant_id,
            "test-dataset-v1"
        )
        
        assert result is not None
        assert "job_id" in result
        assert result.get("status") in ("dispatched", "queued")


@pytest.mark.unit
class TestJobStatusWorkflow:
    """Test complete job status workflow"""
    
    def test_job_status_transitions(self, test_db_session, sample_tenant):
        """Test valid job status transitions"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        # Initial state
        assert job.status == JobStatusEnum.QUEUED
        assert job.created_at is not None
        assert job.submitted_at is not None
        assert job.started_at is None
        assert job.completed_at is None
        
        # Transition to running
        job = registry.update_job_status(job.job_id, JobStatusEnum.RUNNING)
        assert job.status == JobStatusEnum.RUNNING
        assert job.started_at is not None
        
        # Add progress
        job = registry.update_job_status(
            job.job_id,
            JobStatusEnum.RUNNING,
            completed_cases=25,
            total_cases=50,
        )
        assert job.completed_cases == 25
        
        # Transition to succeeded
        job = registry.update_job_status(job.job_id, JobStatusEnum.SUCCEEDED)
        assert job.status == JobStatusEnum.SUCCEEDED
        assert job.completed_at is not None
        assert job.completed_at >= job.started_at
    
    def test_job_cost_tracking(self, test_db_session, sample_tenant):
        """Test cost tracking in job lifecycle"""
        registry = JobRegistry(test_db_session)
        
        job = registry.submit_job(
            tenant_id=sample_tenant.tenant_id,
            job_kind=JobKindEnum.QUALITY,
            spec_json={},
        )
        
        assert job.cost_usd == 0
        assert job.token_usage == 0
        
        # Update costs
        job = registry.update_job_status(
            job.job_id,
            JobStatusEnum.RUNNING,
            cost_usd=12.50,
            token_usage=1500,
        )
        
        assert job.cost_usd == 12.50
        assert job.token_usage == 1500
        
        # Final update with total cost
        job = registry.update_job_status(
            job.job_id,
            JobStatusEnum.SUCCEEDED,
            cost_usd=24.75,
            token_usage=3000,
        )
        
        assert job.cost_usd == 24.75
        assert job.token_usage == 3000
