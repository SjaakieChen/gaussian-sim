"""Compatibility shim for local testing without the TestMaster Python package."""

from __future__ import annotations

try:
    from tmpython.statement import TMPythonStatementJ
except ImportError:

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Fallback base class used outside the TestMaster machine."""

        pass
