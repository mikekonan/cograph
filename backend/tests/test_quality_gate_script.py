from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "quality_gate.py"


def test_quality_gate_script_lists_known_change_types() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--list"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout.splitlines() == [
        "wiki-llm",
        "api-mcp",
        "frontend-wiki",
        "deployment-config",
    ]


def test_quality_gate_json_contains_api_mcp_contract_checks() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "api-mcp", "--format", "json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    commands = "\n".join(gate["command"] for gate in payload["api-mcp"])

    assert "backend/tests/test_retrieval_api.py" in commands
    assert "backend/tests/test_mcp_transport.py" in commands


def test_quality_gate_referenced_test_files_exist() -> None:
    module = _load_script_module()
    commands = "\n".join(
        gate.command for bundle in module.GATE_BUNDLES.values() for gate in bundle
    )

    for path in [
        "backend/tests/test_app.py",
        "backend/tests/test_mcp_transport.py",
        "backend/tests/test_retrieval_api.py",
        "backend/tests/unit/wiki",
    ]:
        assert path in commands
        assert (ROOT / path).exists()


def _load_script_module():
    spec = importlib.util.spec_from_file_location("quality_gate", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
