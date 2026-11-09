"""Exceptions shared by the server and its service modules.

Kept outside server.py so a service raising them matches the handler's class even when the
server itself runs as __main__.
"""


class ConflictError(ValueError):
    """The browser's editable snapshot is older than persisted state."""
