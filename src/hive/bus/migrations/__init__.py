"""Versioned SQL migrations for the Hive PostgreSQL schema."""

from hive.bus.migrations.runner import run_migrations

__all__ = ["run_migrations"]
