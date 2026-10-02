"""Gateway for the Hive website over Tailscale (ADR 0030).

Loopback-only HTTP server. It trusts only the ``Tailscale-User-Login`` header that
``tailscale serve`` adds, and it holds no work logic: every page is built from
firstmate's own ``fm-fleet-snapshot.sh --json``, and every write is one fixed
firstmate script (see ``actions``). It never edits a backlog or any firstmate file.
"""
