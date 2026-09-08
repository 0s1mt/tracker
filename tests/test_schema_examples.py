"""The recorded example under schemas/examples/ must validate against the
formal contract in schemas/incident.schema.json. This is the test that keeps
the JSON draft and the real Incident dataclass output in sync over time.
"""
import glob
import json
import os

import pytest
from jsonschema import Draft202012Validator

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "..", "schemas")

with open(os.path.join(SCHEMA_DIR, "incident.schema.json"), encoding="utf-8") as f:
    SCHEMA = json.load(f)

VALIDATOR = Draft202012Validator(SCHEMA)
EXAMPLE_FILES = sorted(glob.glob(os.path.join(SCHEMA_DIR, "examples", "*.example.json")))


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=[os.path.basename(p) for p in EXAMPLE_FILES])
def test_example_file_matches_incident_schema(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    docs = data if isinstance(data, list) else [data]
    assert docs, f"{path} contains no documents"

    for doc in docs:
        errors = list(VALIDATOR.iter_errors(doc))
        assert not errors, "\n".join(f"{e.message} at {list(e.path)}" for e in errors)


def test_at_least_one_example_file_found():
    assert len(EXAMPLE_FILES) >= 1
