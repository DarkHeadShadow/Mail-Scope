"""SQLite indexing and background job APIs for Mail-Scope."""

__author__ = "Bálint Szabolcs"

from .database import DatabaseBusyError, IndexDatabase
from .people import Person, PersonDirectory, PersonSuggestion
from .service import IndexingJob, IndexingService

__all__ = [
    "DatabaseBusyError",
    "IndexDatabase",
    "IndexingJob",
    "IndexingService",
    "Person",
    "PersonDirectory",
    "PersonSuggestion",
]
