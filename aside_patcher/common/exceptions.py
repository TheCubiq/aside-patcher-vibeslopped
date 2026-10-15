"""Custom exceptions for refused patch operations."""


class Refusal(RuntimeError):
    """An input or state did not meet the patcher's safety requirements.

    Parameters
    ----------
    message : str
        Actionable explanation of the refused operation.
    code : str
        Stable protocol error identifier.
    """

    def __init__(self, message: str, *, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code
