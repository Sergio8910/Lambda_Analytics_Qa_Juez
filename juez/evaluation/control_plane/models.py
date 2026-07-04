"""
Control Plane Models (SQLAlchemy)
Defines all ORM models for job orchestration and metadata.
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, 
    ARRAY, JSON, UUID, ForeignKey, Index, Enum, Text, INET
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum
import uuid

Base = declarative_base()


class JobStatusEnum(str, enum.Enum):
    """Job execution status"""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKindEnum(str, enum.Enum):
    """Types of evaluation jobs"""
    SMOKE = "smoke"
    QUALITY = "quality"
    RAG = "rag"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CHAOS = "chaos"
    DRIFT = "drift"


class Tenant(Base):
    """Multi-tenant registry"""
    __tablename__ = "tenants"
    
    tenant_id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), default="active")  # active, suspended, deleted
    
    # Rate limiting
    rate_limit_jobs_per_minute = Column(Integer, default=60)
    rate_limit_api_per_minute = Column(Integer, default=600)
    
    # Quotas
    monthly_job_limit = Column(Integer, default=10000)
    monthly_cost_limit = Column(Float)
    
    # Metadata
    metadata = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    jobs = relationship("Job", back_populates="tenant", cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="tenant", cascade="all, delete-orphan")
    suites = relationship("Suite", back_populates="tenant", cascade="all, delete-orphan")
    datasets = relationship("Dataset", back_populates="tenant", cascade="all, delete-orphan")
    baselines = relationship("Baseline", back_populates="tenant", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="tenant", cascade="all, delete-orphan")


class Job(Base):
    """Master record for evaluation job"""
    __tablename__ = "jobs"
    
    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(255), ForeignKey("tenants.tenant_id"), nullable=False)
    run_id = Column(String(255))
    
    # Job type and status
    kind = Column(Enum(JobKindEnum), nullable=False)
    status = Column(Enum(JobStatusEnum), default=JobStatusEnum.QUEUED, nullable=False)
    phase = Column(String(100))  # Sub-phase of execution
    
    # Target configuration
    app_id = Column(String(255))
    environment = Column(String(50))
    base_url = Column(Text)
    
    # Version tracking (for reproducibility)
    spec_version = Column(String(50))
    model_version = Column(String(100))
    rag_index_version = Column(String(100))
    dataset_version = Column(String(100))
    suite_version = Column(String(100))
    
    # Execution parameters
    max_parallelism = Column(Integer, default=4)
    deadline_s = Column(Integer, default=1800)
    
    # Progress counters
    total_cases = Column(Integer, default=0)
    completed_cases = Column(Integer, default=0)
    failed_cases = Column(Integer, default=0)
    
    # Costs
    cost_usd = Column(Float, default=0)
    token_usage = Column(Integer, default=0)
    
    # Webhooks
    webhook_url = Column(Text)
    webhook_secret = Column(String(255))
    webhook_events = Column(ARRAY(String), default=[])
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    submitted_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    last_heartbeat_at = Column(DateTime)
    
    # Full specifications (for audit)
    spec_json = Column(JSON)
    summary_json = Column(JSON)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="jobs")
    results = relationship("EvaluationResult", back_populates="job", cascade="all, delete-orphan")
    webhooks = relationship("Webhook", back_populates="job", cascade="all, delete-orphan")
    
    # Indices
    __table_args__ = (
        Index('idx_jobs_tenant_id', 'tenant_id'),
        Index('idx_jobs_status', 'status'),
        Index('idx_jobs_kind', 'kind'),
        Index('idx_jobs_created_at', 'created_at'),
        Index('idx_jobs_tenant_status', 'tenant_id', 'status'),
    )


class Suite(Base):
    """Test suite definitions"""
    __tablename__ = "suites"
    
    suite_id = Column(String(255), primary_key=True)
    tenant_id = Column(String(255), ForeignKey("tenants.tenant_id"), nullable=False)
    kind = Column(String(50), nullable=False)
    version = Column(String(50), nullable=False)
    
    # Definition
    datasets = Column(ARRAY(String))
    scenarios = Column(ARRAY(String))
    sample_size = Column(Integer)
    
    # Thresholds
    thresholds = Column(JSON)
    
    # Release gate
    release_gate_enabled = Column(Boolean, default=True)
    release_gate_rule = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="suites")
    
    __table_args__ = (
        Index('idx_suites_tenant_id', 'tenant_id'),
        Index('idx_suites_kind', 'kind'),
    )


class Dataset(Base):
    """Test dataset registry"""
    __tablename__ = "datasets"
    
    dataset_id = Column(String(255), primary_key=True)
    tenant_id = Column(String(255), ForeignKey("tenants.tenant_id"), nullable=False)
    version = Column(String(50), nullable=False)
    
    cases_count = Column(Integer)
    hash = Column(String(64))  # SHA256 for reproducibility
    
    description = Column(Text)
    scenarios = Column(ARRAY(String))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="datasets")
    
    __table_args__ = (
        Index('idx_datasets_tenant_id', 'tenant_id'),
        Index('idx_datasets_hash', 'hash'),
    )


class EvaluationResult(Base):
    """Individual case evaluation result"""
    __tablename__ = "evaluation_results"
    
    result_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=False)
    case_id = Column(String(255), nullable=False)
    
    # Input
    input_text = Column(Text)
    expected_behavior = Column(Text)
    retrieval_context = Column(ARRAY(Text))
    
    # Agent output
    agent_response = Column(Text)
    response_latency_ms = Column(Integer)
    
    # Metrics (flat for queries)
    exact_match = Column(Float)
    format_compliance = Column(Float)
    answer_relevancy = Column(Float)
    faithfulness = Column(Float)
    task_success = Column(Float)
    completeness = Column(Float)
    consistency = Column(Float)
    
    # RAG metrics
    rag_precision_at_k = Column(Float)
    rag_recall_at_k = Column(Float)
    rag_document_safety = Column(Float)
    
    # Security
    injection_success = Column(Boolean)
    exfiltration_success = Column(Boolean)
    
    # Analysis
    passed = Column(Boolean)
    failure_reason = Column(Text)
    feedback = Column(Text)
    
    # Telemetry
    trace_id = Column(String(255))
    span_id = Column(String(255))
    
    cost_usd = Column(Float)
    tokens_used = Column(Integer)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    job = relationship("Job", back_populates="results")
    
    __table_args__ = (
        Index('idx_eval_results_job_id', 'job_id'),
        Index('idx_eval_results_case_id', 'case_id'),
        Index('idx_eval_results_passed', 'passed'),
        Index('idx_eval_results_trace_id', 'trace_id'),
    )


class Baseline(Base):
    """Release baseline for comparison"""
    __tablename__ = "baselines"
    
    baseline_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(255), ForeignKey("tenants.tenant_id"), nullable=False)
    kind = Column(String(50), nullable=False)
    
    # Version information
    model_version = Column(String(100))
    rag_index_version = Column(String(100))
    prompt_version = Column(String(100))
    
    # Aggregated metrics
    quality_score = Column(Float)
    answer_relevancy = Column(Float)
    faithfulness = Column(Float)
    task_success = Column(Float)
    
    latency_p50 = Column(Integer)
    latency_p95 = Column(Integer)
    latency_p99 = Column(Integer)
    
    cost_usd = Column(Float)
    pass_rate = Column(Float)
    
    num_cases = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="baselines")
    
    __table_args__ = (
        Index('idx_baselines_tenant_kind', 'tenant_id', 'kind'),
    )


class Webhook(Base):
    """Webhook delivery log"""
    __tablename__ = "webhooks"
    
    webhook_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.job_id"), nullable=False)
    event_type = Column(String(100))
    
    status = Column(String(50), default="pending")  # pending, delivered, failed
    attempt = Column(Integer, default=0)
    max_attempts = Column(Integer, default=5)
    
    payload = Column(JSON)
    response_code = Column(Integer)
    response_body = Column(Text)
    
    scheduled_at = Column(DateTime)
    delivered_at = Column(DateTime)
    next_retry_at = Column(DateTime)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    job = relationship("Job", back_populates="webhooks")
    
    __table_args__ = (
        Index('idx_webhooks_job_id', 'job_id'),
        Index('idx_webhooks_status', 'status'),
        Index('idx_webhooks_next_retry', 'next_retry_at'),
    )


class ApiKey(Base):
    """API key management"""
    __tablename__ = "api_keys"
    
    key_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(255), ForeignKey("tenants.tenant_id"), nullable=False)
    
    # Key (hashed, never plaintext)
    key_hash = Column(String(255), nullable=False, unique=True)
    key_prefix = Column(String(20))
    
    # Scopes
    scopes = Column(ARRAY(String), default=[])
    
    status = Column(String(50), default="active")
    
    # Rotation
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    last_used_at = Column(DateTime)
    rotated_at = Column(DateTime)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="api_keys")


class AuditLog(Base):
    """Audit trail"""
    __tablename__ = "audit_logs"
    
    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(255), ForeignKey("tenants.tenant_id"), nullable=False)
    
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100))
    resource_id = Column(String(255))
    
    actor = Column(String(255))
    ip_address = Column(INET)
    
    changes = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="audit_logs")
    
    __table_args__ = (
        Index('idx_audit_logs_tenant_id', 'tenant_id'),
        Index('idx_audit_logs_action', 'action'),
        Index('idx_audit_logs_created_at', 'created_at'),
    )
