# Control Plane Module
"""
Control plane manages all long-running operational state:
- Job registry and orchestration
- Dataset/suite versions
- Multi-tenant namespacing
- API key and auth
- Baseline comparisons
"""

from .db import get_db_session, init_db, close_db, get_engine
from .registry import (
    JobRegistry,
    DatasetRegistry,
    SuiteRegistry,
    BaselineRegistry,
    ResultRegistry,
)
from .orchestrator import JobOrchestrator, JobScheduler
from .models import Job, Suite, Dataset, Baseline, Tenant, EvaluationResult

__all__ = [
    # Database
    "get_db_session",
    "init_db",
    "close_db",
    "get_engine",
    # Registries
    "JobRegistry",
    "DatasetRegistry",
    "SuiteRegistry",
    "BaselineRegistry",
    "ResultRegistry",
    # Orchestration
    "JobOrchestrator",
    "JobScheduler",
    # Models
    "Job",
    "Suite",
    "Dataset",
    "Baseline",
    "Tenant",
    "EvaluationResult",
]
