"""
Job Orchestrator
Dispatches evaluation jobs to appropriate Celery workers.
"""

from typing import Dict, Any, Optional
from uuid import UUID
import logging
from sqlalchemy.orm import Session

from juez.evaluation.control_plane.models import Job, JobKindEnum, JobStatusEnum
from juez.evaluation.control_plane.registry import JobRegistry
from juez.evaluation.execution.tasks import (
    run_functional_eval,
    run_rag_eval,
    run_security_eval,
    run_performance_eval,
    run_drift_eval,
)

logger = logging.getLogger(__name__)

# Mapping de job kind a Celery task
TASK_MAPPING = {
    JobKindEnum.SMOKE: run_functional_eval,
    JobKindEnum.QUALITY: run_functional_eval,
    JobKindEnum.RAG: run_rag_eval,
    JobKindEnum.SECURITY: run_security_eval,
    JobKindEnum.PERFORMANCE: run_performance_eval,
    JobKindEnum.CHAOS: run_performance_eval,
    JobKindEnum.DRIFT: run_drift_eval,
}


class JobOrchestrator:
    """
    Orchestrates evaluation job execution.
    Routes jobs to appropriate workers based on type.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.registry = JobRegistry(db)
    
    def dispatch_job(
        self,
        job_id: UUID,
        tenant_id: str,
        job_kind: JobKindEnum,
        cases: list,
        config: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Dispatch a job to the appropriate worker.
        
        Args:
            job_id: Job ID
            tenant_id: Tenant ID
            job_kind: Type of job
            cases: Test cases to evaluate
            config: Job configuration
            **kwargs: Additional parameters
        
        Returns:
            Task info with celery_task_id
        """
        try:
            # Validate job exists and is in queued state
            job = self.registry.get_job(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            if job.status != JobStatusEnum.QUEUED:
                raise ValueError(f"Job not in queued state: {job_id}")
            
            # Get the task function
            task_func = TASK_MAPPING.get(job_kind)
            if not task_func:
                raise ValueError(f"Unknown job kind: {job_kind}")
            
            # Update job status to running
            self.registry.update_job_status(
                job_id,
                JobStatusEnum.RUNNING,
                phase="dispatched"
            )
            
            # Dispatch to Celery
            celery_task = task_func.apply_async(
                args=[str(job_id), tenant_id, cases],
                kwargs=config,
                task_id=str(job_id),  # Use job_id as task_id for correlation
                queue=self._get_queue_for_kind(job_kind),
                priority=kwargs.get("priority", 5),  # 0-10, higher = more urgent
            )
            
            logger.info(
                f"Job dispatched to worker: job_id={job_id}, kind={job_kind}, "
                f"celery_task_id={celery_task.id}, queue={self._get_queue_for_kind(job_kind)}"
            )
            
            return {
                "job_id": str(job_id),
                "celery_task_id": celery_task.id,
                "status": "dispatched",
                "queue": self._get_queue_for_kind(job_kind),
            }
        
        except Exception as exc:
            logger.exception(f"Error dispatching job: {exc}")
            # Update job status to failed
            try:
                self.registry.update_job_status(
                    job_id,
                    JobStatusEnum.FAILED,
                    phase="dispatch_error"
                )
            except:
                pass
            raise
    
    def get_job_status(self, job_id: UUID) -> Dict[str, Any]:
        """
        Get current status of a job.
        Includes Celery task status if available.
        """
        job = self.registry.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")
        
        from juez.evaluation.execution.celery_app import app as celery_app
        
        status_info = {
            "job_id": str(job.job_id),
            "status": job.status.value,
            "phase": job.phase,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "progress": {
                "completed_cases": job.completed_cases,
                "total_cases": job.total_cases,
                "percent": (job.completed_cases / job.total_cases * 100) if job.total_cases > 0 else 0,
            },
            "cost_usd": float(job.cost_usd) if job.cost_usd else 0,
            "token_usage": job.token_usage or 0,
        }
        
        return status_info
    
    def cancel_job(self, job_id: UUID) -> Dict[str, Any]:
        """Cancel a running job"""
        try:
            job = self.registry.get_job(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            if job.status not in (JobStatusEnum.QUEUED, JobStatusEnum.RUNNING):
                raise ValueError(f"Cannot cancel job with status: {job.status}")
            
            # Revoke Celery task if running
            if job.status == JobStatusEnum.RUNNING:
                from juez.evaluation.execution.celery_app import app as celery_app
                celery_app.control.revoke(str(job_id), terminate=True)
            
            # Update status
            self.registry.update_job_status(job_id, JobStatusEnum.CANCELLED)
            
            logger.info(f"Job cancelled: {job_id}")
            return {"job_id": str(job_id), "status": "cancelled"}
        
        except Exception as exc:
            logger.exception(f"Error cancelling job: {exc}")
            raise
    
    def get_worker_stats(self, queue: Optional[str] = None) -> Dict[str, Any]:
        """Get worker statistics"""
        from juez.evaluation.execution.celery_app import app as celery_app
        
        stats = app.control.inspect().stats()
        
        if not stats:
            return {"message": "No workers available"}
        
        if queue:
            # Filter by queue if specified
            stats = {w: s for w, s in stats.items() if queue in str(s)}
        
        return stats
    
    @staticmethod
    def _get_queue_for_kind(job_kind: JobKindEnum) -> str:
        """
        Get the Celery queue name for a job kind.
        Maps job kinds to specialized worker queues.
        """
        queue_mapping = {
            JobKindEnum.SMOKE: "functional",
            JobKindEnum.QUALITY: "functional",
            JobKindEnum.RAG: "rag",
            JobKindEnum.SECURITY: "security",
            JobKindEnum.PERFORMANCE: "performance",
            JobKindEnum.CHAOS: "performance",
            JobKindEnum.DRIFT: "drift",
        }
        return queue_mapping.get(job_kind, "default")


class JobScheduler:
    """
    Schedules periodic jobs (drift detection, baseline comparisons, etc).
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.registry = JobRegistry(db)
        self.orchestrator = JobOrchestrator(db)
    
    def schedule_nightly_drift_check(self, tenant_id: str) -> Dict[str, Any]:
        """
        Schedule a nightly drift detection job for a tenant.
        Compares current behavior against baseline.
        """
        try:
            logger.info(f"Scheduling nightly drift check for tenant: {tenant_id}")
            
            # Create drift job
            job = self.registry.submit_job(
                tenant_id=tenant_id,
                job_kind=JobKindEnum.DRIFT,
                spec_json={
                    "kind": "drift",
                    "baseline_type": "latest_release",
                    "check_inputs_drift": True,
                    "check_outputs_drift": True,
                    "check_cost_drift": True,
                }
            )
            
            # Dispatch it
            result = self.orchestrator.dispatch_job(
                job_id=job.job_id,
                tenant_id=tenant_id,
                job_kind=JobKindEnum.DRIFT,
                cases=[],
                config={"baseline_type": "latest_release"}
            )
            
            return result
        except Exception as exc:
            logger.exception(f"Error scheduling drift check: {exc}")
            raise
    
    def schedule_daily_quality_check(self, tenant_id: str, dataset_id: str) -> Dict[str, Any]:
        """Schedule a daily quality evaluation against a dataset"""
        try:
            logger.info(f"Scheduling daily quality check for tenant: {tenant_id}")
            
            job = self.registry.submit_job(
                tenant_id=tenant_id,
                job_kind=JobKindEnum.QUALITY,
                spec_json={
                    "kind": "quality",
                    "dataset_id": dataset_id,
                    "scenarios": ["quality", "consistency"],
                }
            )
            
            result = self.orchestrator.dispatch_job(
                job_id=job.job_id,
                tenant_id=tenant_id,
                job_kind=JobKindEnum.QUALITY,
                cases=[],
                config={"dataset_id": dataset_id}
            )
            
            return result
        except Exception as exc:
            logger.exception(f"Error scheduling quality check: {exc}")
            raise
