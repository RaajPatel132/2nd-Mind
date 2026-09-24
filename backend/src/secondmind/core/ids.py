"""Time-ordered identifiers (UUIDv7, RFC 9562).

Turn ids double as trace ids, so they must be valid 128-bit ids; v7 also keeps them roughly
sorted by creation time, which suits the turns index and cursor paging.
"""

import os
import time
import uuid


def new_id() -> uuid.UUID:
    """Return a new UUIDv7: 48-bit unix-ms timestamp, version/variant bits, 74 random bits."""
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (unix_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76  # version 7
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand & 0x3FFF_FFFF_FFFF_FFFF  # rand_b (62 bits)
    return uuid.UUID(int=value)
