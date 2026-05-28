# Execution Module
"""
Execution plane runs evaluation jobs asynchronously.
Contains Celery workers specialized by evaluation type.
"""

from .celery_app import app as celery_app

__all__ = ["celery_app"]
