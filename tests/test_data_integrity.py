"""Invariants over the committed data/releases.json dataset."""

import json
from pathlib import Path

from artifacts import _sort_key
from models import release_from_dict


def test_releases_json_is_sorted_newest_first():
    # data/releases.json is the machine-readable dataset consumers read
    # directly, and the pipeline always writes it sorted newest-first
    # (artifacts._sort_key). A manual date correction must move the row to
    # its sorted position, or the committed file silently diverges from the
    # order the next pipeline write would produce.
    raw = json.loads(Path("data/releases.json").read_text(encoding="utf-8"))
    keys = [_sort_key(release_from_dict(item)) for item in raw]
    assert keys == sorted(keys)
