"""
Hand-maintained venue -> kind registry (Story 19.6). Deliberately not Nautilus's
`Venue.is_dex()`: that only fires on a "<Chain>:<DexType>" venue string behind the `defi`
feature, and none of dYdX/Bybit/Hyperliquid use that format, so it would answer wrongly for all
three. A new venue is one more line here.
"""

VENUE_KINDS: dict[str, str] = {"DYDX": "dex", "HYPERLIQUID": "dex", "BYBIT": "cex"}


def venue_kind(venue: str) -> str:
    """"cex" | "dex", or "unknown" for a venue not yet registered (never raises)."""
    return VENUE_KINDS.get(venue, "unknown")
