from __future__ import annotations

import time
import uuid

from backend.app.core.ids import uuid7


def test_uuid7_returns_valid_uuid_version_7():
    value = uuid7()
    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_time_ordered_across_1ms_boundary():
    earlier = uuid7()
    time.sleep(0.002)
    later = uuid7()
    assert earlier.int < later.int


def test_uuid7_batch_is_monotonically_increasing_across_millisecond_boundaries():
    values = []
    for _ in range(5):
        values.append(uuid7())
        time.sleep(0.002)
    assert values == sorted(values, key=lambda candidate: candidate.int)


def test_uuid7_values_are_unique():
    values = [uuid7() for _ in range(1000)]
    assert len(set(values)) == len(values)
