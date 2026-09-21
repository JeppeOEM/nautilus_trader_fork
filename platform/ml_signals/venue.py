"""
`venue` is derived from Nautilus's own `"{SYMBOL}.{VENUE}"` InstrumentId convention
(Story 19.1) -- never stored as a separate column.
"""


class MalformedInstrumentId(ValueError):
    """An instrument_id without a `.VENUE` suffix -- never produced by Nautilus itself."""


def venue_of(instrument_id: str) -> str:
    symbol, dot, venue = instrument_id.rpartition(".")
    if not (symbol and dot and venue):
        raise MalformedInstrumentId(f"instrument_id has no venue suffix: {instrument_id!r}")
    return venue
