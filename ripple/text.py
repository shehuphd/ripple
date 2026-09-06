"""Small display formatters shared by the pages and the services behind them."""

from __future__ import annotations

from datetime import datetime


def when_label(moment: datetime) -> str:
    """A timestamp as the lists show it: day, month, hour and minute."""
    return moment.strftime("%d %b %H:%M")
