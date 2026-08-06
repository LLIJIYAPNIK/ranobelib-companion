"""The only place in this application allowed to construct ``RanobeLib(...)``."""

from ranobelib import RanobeLib


def get_client(url: str) -> RanobeLib:
    """Build a `RanobeLib` client for a title URL or `{id}--{slug}` identifier."""
    return RanobeLib(url)
