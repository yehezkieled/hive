"""Lead-worker round-trip — retired in Ticket 018.

This file used to exercise a worker emitting a ``hive_actions`` reply to
its lead via the dotted naming convention. Ticket 018 deleted the
persistent **Worker** entity entirely: ``spawn_worker`` is gone from the
leaf path, the ``Worker`` model is removed, and the ``worker`` permission
branches no longer exist. Leaf work now runs as ephemeral **Leaf agents**
inside a Lead's Workflow run, which has no Hive lifecycle or mailbox to
round-trip through.

The worker-specific test was therefore removed rather than rewritten —
there is no replacement constructor and no worker peer-messaging path to
assert against. The file is kept (empty of tests) so it remains
importable/collectable without ImportError.
"""

from __future__ import annotations
