"""SQLite indexing and background job APIs for Mail-Scope."""

__author__ = "Bálint Szabolcs"

from .database import IndexDatabase
from .service import IndexingJob, IndexingService

__all__ = ["IndexDatabase", "IndexingJob", "IndexingService"]
