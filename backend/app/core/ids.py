from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUID v7 per RFC 9562.

    Layout: 48 bits ms timestamp + 4 bits version + 12 bits random
    + 2 bits variant + 62 bits random.

    Timestamp prefix makes btree index locality and WAL traffic
    meaningfully better than v4 on write-heavy tables.
    """
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_bytes = os.urandom(10)

    b = bytearray(16)
    b[0] = (timestamp_ms >> 40) & 0xFF
    b[1] = (timestamp_ms >> 32) & 0xFF
    b[2] = (timestamp_ms >> 24) & 0xFF
    b[3] = (timestamp_ms >> 16) & 0xFF
    b[4] = (timestamp_ms >> 8) & 0xFF
    b[5] = timestamp_ms & 0xFF

    b[6] = 0x70 | (rand_bytes[0] & 0x0F)
    b[7] = rand_bytes[1]

    b[8] = 0x80 | (rand_bytes[2] & 0x3F)
    b[9] = rand_bytes[3]
    b[10] = rand_bytes[4]
    b[11] = rand_bytes[5]
    b[12] = rand_bytes[6]
    b[13] = rand_bytes[7]
    b[14] = rand_bytes[8]
    b[15] = rand_bytes[9]

    return uuid.UUID(bytes=bytes(b))
