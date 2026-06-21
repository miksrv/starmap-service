"""Service exceptions."""


class ValidationError(Exception):
    """A command failed validation.

    The message is safe to send back to the bot as the `error` field — it must
    not contain internal details or tracebacks.
    """
