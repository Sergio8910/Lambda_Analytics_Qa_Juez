"""
Celery Application Configuration
Configures the distributed task queue for async evaluation jobs.
"""

import os
from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

# Create Celery app
app = Celery("juez")

# Load configuration from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
POSTGRES_URL = os.getenv("DATABASE_URL", "postgresql://juez_user:juez_pass_dev@localhost/juez")

# Configure Celery
app.conf.update(
    # Broker and backend
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    
    # Task configuration
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Retry configuration
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    
    # Max retries for different task types
    task_max_retries=3,
    task_default_retry_delay=60,  # 1 minute
    
    # Worker configuration
    worker_prefetch_multiplier=1,  # Don't prefetch tasks
    worker_max_tasks_per_child=1000,
    
    # Result configuration
    result_expires=3600,  # Results expire after 1 hour
    result_extended=True,  # Store more result metadata
    
    # Task routing
    task_routes={
        "evaluation.execution.tasks.run_functional_eval": {"queue": "functional"},
        "evaluation.execution.tasks.run_rag_eval": {"queue": "rag"},
        "evaluation.execution.tasks.run_security_eval": {"queue": "security"},
        "evaluation.execution.tasks.run_performance_eval": {"queue": "performance"},
        "evaluation.execution.tasks.run_drift_eval": {"queue": "drift"},
        "evaluation.execution.tasks.deliver_webhook": {"queue": "webhooks"},
    },
    
    # Queue configuration
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "functional": {"exchange": "functional", "routing_key": "functional"},
        "rag": {"exchange": "rag", "routing_key": "rag"},
        "security": {"exchange": "security", "routing_key": "security"},
        "performance": {"exchange": "performance", "routing_key": "performance"},
        "drift": {"exchange": "drift", "routing_key": "drift"},
        "webhooks": {"exchange": "webhooks", "routing_key": "webhooks"},
        "critical": {"exchange": "critical", "routing_key": "critical"},
        "dead_letter": {"exchange": "dead_letter", "routing_key": "dead_letter"},
    },
)

# Import tasks to register them
from . import tasks  # noqa: F401


# Signals for observability
@before_task_publish.connect
def task_sent_handler(sender=None, body=None, **kwargs):
    """Log task submission for observability"""
    task_name = body.get("task") if isinstance(body, dict) else "unknown"
    # TODO: Add OpenTelemetry span
    pass


@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, **kwargs):
    """Before task execution"""
    # TODO: Add OpenTelemetry span start
    pass


@task_postrun.connect
def task_postrun_handler(sender=None, task_id=None, task=None, retval=None, state=None, **kwargs):
    """After task execution"""
    # TODO: Add OpenTelemetry span end with status
    pass
