"""Loopback-only HTTP server for the Mail-Scope desktop application."""

__author__ = "Bálint Szabolcs"

from .app import ApiError, MailScopeApplication, create_server

__all__ = ["ApiError", "MailScopeApplication", "create_server"]
