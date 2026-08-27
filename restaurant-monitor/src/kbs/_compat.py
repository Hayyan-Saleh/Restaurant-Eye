"""
_compat.py
----------
experta (last released 2021) does `from collections import Mapping`, which
was removed in Python 3.10 (moved to collections.abc). Must run before any
`import experta` anywhere in the process. Previously only run_live.py
applied this patch inline, so importing src.kbs.role_engine or
src.kbs.table_kbs directly (as tests, the Event Engine, and the API layer
all now do) crashed on Python >=3.10. Centralized here so every importer
gets it for free.
"""

import collections
import collections.abc

if not hasattr(collections, "Mapping"):
    collections.Mapping = collections.abc.Mapping  # type: ignore[attr-defined]
