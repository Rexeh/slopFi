"""Today's date, asked for in one place so the browser tests can pin it to the demo's reference day."""
from datetime import date


def today() -> date:
    return date.today()
