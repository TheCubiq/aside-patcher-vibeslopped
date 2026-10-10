"""Custom exceptions for refused patch operations."""


class Refusal(RuntimeError):
    """An input or state did not meet the patcher's safety requirements."""
