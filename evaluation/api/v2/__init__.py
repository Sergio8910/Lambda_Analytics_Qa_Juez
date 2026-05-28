# API v2 Module
"""
API v2: Modern async-first evaluation API.

Contracts follow RFC 9457 (Problem Details for HTTP APIs).
All mutations return 202 Accepted with job tracking.
Results delivered via webhooks or polling.
"""

from .app import create_app

__all__ = ["create_app"]
