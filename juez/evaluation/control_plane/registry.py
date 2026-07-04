"""
Control Plane Registry
Manages CRUD operations and queries for jobs, datasets, and suites.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
import logging
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from juez.evaluation.control_plane.models import (
    Job, JobStatusEnum, JobKindEnum, Dataset, Suite, Baseline, EvaluationResult
)

logger = logging.getLogger(__name__)


class JobRegistry:
    """
    Job orchestration registry.
    Handles creation, status tracking, and querying of evaluation jobs.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def submit_job(
        self,
        tenant_id: str,
        job_kind: JobKindEnum,
        spec_json: Dict[str, Any],
        **kwargs
    ) -> Job:
        """
        Submit a new evaluation job.
        
        Args:
            tenant_id: Tenant identifier
            job_kind: Type of job (smoke, quality, rag, etc.)
            spec_json: Full job specification
            **kwargs: Additional fields (app_id, base_url, model_version, etc.)
        
        Returns:
            Job object with job_id set
        """
        try:
            job = Job(
                tenant_id=tenant_id,
                kind=job_kind,
                status=JobStatusEnum.QUEUED,
                spec_json=spec_json,
                created_at=datetime.utcnow(),
                submitted_at=datetime.utcnow(),
                **kwargs
            )
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            logger.info(f"Job submitted: job_id={job.job_id}, kind={job_kind}, tenant={tenant_id}")
            return job
        except Exception as exc:
            logger.exception(f"Error submitting job: {exc}")
            self.db.rollback()
            raise
    
    def get_job(self, job_id: UUID) -> Optional[Job]:
        """Get job by ID"""
        return self.db.query(Job).filter(Job.job_id == job_id).first()
    
    def list_jobs(
        self,
        tenant_id: str,
        status: Optional[JobStatusEnum] = None,
        kind: Optional[JobKindEnum] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Job]:
        """
        List jobs for a tenant with optional filtering.
        
        Args:
            tenant_id: Tenant ID
            status: Filter by status (optional)
            kind: Filter by kind (optional)
            limit: Page size
            offset: Page offset
        
        Returns:
            List of Job objects
        """
        query = self.db.query(Job).filter(Job.tenant_id == tenant_id)
        
        if status:
            query = query.filter(Job.status == status)
        if kind:
            query = query.filter(Job.kind == kind)
        
        return query.order_by(desc(Job.created_at)).limit(limit).offset(offset).all()
    
    def update_job_status(
        self,
        job_id: UUID,
        status: JobStatusEnum,
        **kwargs
    ) -> Job:
        """
        Update job status and optional fields.
        
        Args:
            job_id: Job ID
            status: New status
            **kwargs: Other fields to update (phase, completed_cases, cost_usd, etc.)
        
        Returns:
            Updated Job object
        """
        try:
            job = self.get_job(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job.status = status
            
            # Update phase if provided
            if "phase" in kwargs:
                job.phase = kwargs["phase"]
            
            # Update counters
            if "completed_cases" in kwargs:
                job.completed_cases = kwargs["completed_cases"]
            if "failed_cases" in kwargs:
                job.failed_cases = kwargs["failed_cases"]
            
            # Update costs
            if "cost_usd" in kwargs:
                job.cost_usd = kwargs["cost_usd"]
            if "token_usage" in kwargs:
                job.token_usage = kwargs["token_usage"]
            
            # Update timestamps
            job.last_heartbeat_at = datetime.utcnow()
            if status == JobStatusEnum.RUNNING and not job.started_at:
                job.started_at = datetime.utcnow()
            elif status in (JobStatusEnum.SUCCEEDED, JobStatusEnum.FAILED):
                job.completed_at = datetime.utcnow()
            
            self.db.commit()
            self.db.refresh(job)
            logger.info(f"Job status updated: job_id={job_id}, status={status}")
            return job
        except Exception as exc:
            logger.exception(f"Error updating job status: {exc}")
            self.db.rollback()
            raise
    
    def save_job_results(self, job_id: UUID, summary_json: Dict[str, Any]) -> Job:
        """Save evaluation results for a job"""
        try:
            job = self.get_job(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job.summary_json = summary_json
            self.db.commit()
            logger.info(f"Job results saved: job_id={job_id}")
            return job
        except Exception as exc:
            logger.exception(f"Error saving job results: {exc}")
            self.db.rollback()
            raise
    
    def get_recent_jobs(self, tenant_id: str, days: int = 7, limit: int = 50) -> List[Job]:
        """Get recent jobs for a tenant"""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        return self.db.query(Job).filter(
            and_(
                Job.tenant_id == tenant_id,
                Job.created_at >= cutoff
            )
        ).order_by(desc(Job.created_at)).limit(limit).all()
    
    def count_jobs_by_status(self, tenant_id: str) -> Dict[str, int]:
        """Get job count by status for a tenant"""
        from sqlalchemy import func
        
        results = self.db.query(
            Job.status,
            func.count(Job.job_id).label('count')
        ).filter(Job.tenant_id == tenant_id).group_by(Job.status).all()
        
        return {status.value: count for status, count in results}


class DatasetRegistry:
    """
    Dataset registry.
    Manages test dataset versions and metadata.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def register_dataset(
        self,
        dataset_id: str,
        tenant_id: str,
        version: str,
        cases_count: int,
        hash: str,
        **kwargs
    ) -> Dataset:
        """Register a new dataset version"""
        try:
            dataset = Dataset(
                dataset_id=dataset_id,
                tenant_id=tenant_id,
                version=version,
                cases_count=cases_count,
                hash=hash,
                created_at=datetime.utcnow(),
                **kwargs
            )
            self.db.add(dataset)
            self.db.commit()
            self.db.refresh(dataset)
            logger.info(f"Dataset registered: {dataset_id} v{version}")
            return dataset
        except Exception as exc:
            logger.exception(f"Error registering dataset: {exc}")
            self.db.rollback()
            raise
    
    def get_dataset(self, dataset_id: str, version: Optional[str] = None) -> Optional[Dataset]:
        """Get dataset by ID and optional version"""
        query = self.db.query(Dataset).filter(Dataset.dataset_id == dataset_id)
        if version:
            query = query.filter(Dataset.version == version)
        return query.first()
    
    def list_datasets(self, tenant_id: str) -> List[Dataset]:
        """List all datasets for a tenant"""
        return self.db.query(Dataset).filter(
            Dataset.tenant_id == tenant_id
        ).order_by(desc(Dataset.created_at)).all()
    
    def get_dataset_by_hash(self, hash: str) -> Optional[Dataset]:
        """Get dataset by content hash (for deduplication)"""
        return self.db.query(Dataset).filter(Dataset.hash == hash).first()


class SuiteRegistry:
    """
    Test suite registry.
    Manages suite definitions and thresholds.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def register_suite(
        self,
        suite_id: str,
        tenant_id: str,
        kind: str,
        version: str,
        **kwargs
    ) -> Suite:
        """Register a new suite"""
        try:
            suite = Suite(
                suite_id=suite_id,
                tenant_id=tenant_id,
                kind=kind,
                version=version,
                created_at=datetime.utcnow(),
                **kwargs
            )
            self.db.add(suite)
            self.db.commit()
            self.db.refresh(suite)
            logger.info(f"Suite registered: {suite_id} ({kind}) v{version}")
            return suite
        except Exception as exc:
            logger.exception(f"Error registering suite: {exc}")
            self.db.rollback()
            raise
    
    def load_suite(self, suite_id: str) -> Optional[Suite]:
        """Load suite configuration"""
        return self.db.query(Suite).filter(Suite.suite_id == suite_id).first()
    
    def list_suites(self, tenant_id: str, kind: Optional[str] = None) -> List[Suite]:
        """List suites for a tenant, optionally by kind"""
        query = self.db.query(Suite).filter(Suite.tenant_id == tenant_id)
        if kind:
            query = query.filter(Suite.kind == kind)
        return query.order_by(desc(Suite.created_at)).all()
    
    def get_suite_by_kind(self, tenant_id: str, kind: str) -> Optional[Suite]:
        """Get latest suite version by kind"""
        return self.db.query(Suite).filter(
            and_(
                Suite.tenant_id == tenant_id,
                Suite.kind == kind
            )
        ).order_by(desc(Suite.version)).first()


class BaselineRegistry:
    """
    Baseline registry for version comparison.
    Stores baseline metrics for release gates.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_baseline(
        self,
        tenant_id: str,
        kind: str,
        metrics: Dict[str, Any],
        versions: Dict[str, str],
        num_cases: int
    ) -> Baseline:
        """Create a new baseline from evaluation results"""
        try:
            baseline = Baseline(
                tenant_id=tenant_id,
                kind=kind,
                created_at=datetime.utcnow(),
                num_cases=num_cases,
                **versions,
                **metrics
            )
            self.db.add(baseline)
            self.db.commit()
            self.db.refresh(baseline)
            logger.info(f"Baseline created: tenant={tenant_id}, kind={kind}")
            return baseline
        except Exception as exc:
            logger.exception(f"Error creating baseline: {exc}")
            self.db.rollback()
            raise
    
    def get_latest_baseline(self, tenant_id: str, kind: str) -> Optional[Baseline]:
        """Get latest baseline for comparison"""
        return self.db.query(Baseline).filter(
            and_(
                Baseline.tenant_id == tenant_id,
                Baseline.kind == kind
            )
        ).order_by(desc(Baseline.created_at)).first()
    
    def list_baselines(self, tenant_id: str, kind: Optional[str] = None) -> List[Baseline]:
        """List baselines for a tenant"""
        query = self.db.query(Baseline).filter(Baseline.tenant_id == tenant_id)
        if kind:
            query = query.filter(Baseline.kind == kind)
        return query.order_by(desc(Baseline.created_at)).all()


class ResultRegistry:
    """
    Evaluation result registry.
    Stores individual case results.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def save_result(self, job_id: UUID, case_data: Dict[str, Any]) -> EvaluationResult:
        """Save individual case result"""
        try:
            result = EvaluationResult(
                job_id=job_id,
                created_at=datetime.utcnow(),
                **case_data
            )
            self.db.add(result)
            self.db.commit()
            self.db.refresh(result)
            return result
        except Exception as exc:
            logger.exception(f"Error saving result: {exc}")
            self.db.rollback()
            raise
    
    def get_job_results(self, job_id: UUID) -> List[EvaluationResult]:
        """Get all results for a job"""
        return self.db.query(EvaluationResult).filter(
            EvaluationResult.job_id == job_id
        ).all()
    
    def get_passed_results(self, job_id: UUID) -> List[EvaluationResult]:
        """Get passing results for a job"""
        return self.db.query(EvaluationResult).filter(
            and_(
                EvaluationResult.job_id == job_id,
                EvaluationResult.passed == True
            )
        ).all()
    
    def aggregate_job_metrics(self, job_id: UUID) -> Dict[str, float]:
        """Calculate aggregated metrics for a job"""
        
        results = self.db.query(EvaluationResult).filter(
            EvaluationResult.job_id == job_id
        ).all()
        
        if not results:
            return {}
        
        metrics = {}
        metric_fields = [
            'exact_match', 'format_compliance', 'answer_relevancy',
            'faithfulness', 'task_success', 'completeness', 'consistency',
            'rag_precision_at_k', 'rag_recall_at_k', 'rag_document_safety'
        ]
        
        for field in metric_fields:
            values = [getattr(r, field) for r in results if getattr(r, field) is not None]
            if values:
                metrics[field] = sum(values) / len(values)
        
        return metrics
