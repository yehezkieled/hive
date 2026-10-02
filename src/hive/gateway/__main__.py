"""``python -m hive.gateway`` — serve on loopback only."""

from __future__ import annotations

import logging
import os

import uvicorn

from hive.gateway.app import create_app
from hive.gateway.settings import DEFAULT_PORT


def main() -> None:
    # One audit line per write action, to stderr (the journal under systemd).
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    audit = logging.getLogger("hive.gateway.audit")
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    port = int(os.environ.get("HIVE_GATEWAY_PORT", DEFAULT_PORT))
    # Hard-coded loopback bind: never 0.0.0.0, never configurable.
    uvicorn.run(create_app(), host="127.0.0.1", port=port, proxy_headers=False)


if __name__ == "__main__":
    main()
