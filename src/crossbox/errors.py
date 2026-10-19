"""Exit codes and the one exception type verbs raise.

    0   ok
    1   refused, or findings
    2   NOT EVALUABLE: the tool could not look (never a pass)
    3   partial: a batch or multi-target verb did some of its work
    64  usage error
"""
from __future__ import annotations

OK = 0
REFUSED = 1
NOT_EVALUABLE = 2
PARTIAL = 3
USAGE = 64


class CrossboxError(Exception):
    """A verb could not do what it was asked. ``code`` is the exit code."""

    def __init__(self, message: str, code: int = REFUSED):
        super().__init__(message)
        self.message = message
        self.code = code


class NotEvaluable(CrossboxError):
    def __init__(self, message: str):
        super().__init__(message, NOT_EVALUABLE)


class UsageError(CrossboxError):
    def __init__(self, message: str):
        super().__init__(message, USAGE)
