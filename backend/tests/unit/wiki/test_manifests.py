"""Tests for `wiki.manifests` — six grounded-fact extractors.

Each filesystem-backed extractor exercises one repo layout (Go module,
Node project, Python project, etc.). The DB-backed `extract_public_api`
hits the same `db_session` fixture other unit tests use. Manifests are
the structural backbone of the planner prompt — if these silently
return empty, the wiki regresses to vague file-tree guesses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.wiki.manifests import (
    build_repo_manifests,
    extract_config_keys,
    extract_dependencies,
    extract_error_types,
    extract_exported_types,
    extract_public_api,
    extract_run_commands,
    extract_runtimes,
    extract_use_cases,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_extract_runtimes_from_go_mod(tmp_path: Path) -> None:
    _write(
        tmp_path / "go.mod",
        "module github.com/acme/widget\n\ngo 1.22.4\n\nrequire ( github.com/foo/bar v1.0.0 )\n",
    )
    runtimes = extract_runtimes(tmp_path)
    assert any(r.name == "go" and r.version == "1.22.4" for r in runtimes)
    go = next(r for r in runtimes if r.name == "go")
    assert go.evidence.source_file_path == "go.mod"
    assert go.evidence.source_lines is not None


def test_extract_runtimes_from_pyproject(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "demo"\nrequires-python = ">=3.12"\n',
    )
    runtimes = extract_runtimes(tmp_path)
    assert any(r.name == "python" and r.version == ">=3.12" for r in runtimes)


def test_extract_runtimes_from_dockerfile(tmp_path: Path) -> None:
    _write(
        tmp_path / "Dockerfile",
        "FROM python:3.12-slim AS base\nRUN echo hi\n",
    )
    runtimes = extract_runtimes(tmp_path)
    assert any(r.name == "python" and r.version == "3.12-slim" for r in runtimes)


def test_extract_runtimes_returns_empty_for_unknown_layout(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Demo\n")
    assert extract_runtimes(tmp_path) == []


def test_extract_run_commands_from_makefile(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        "build:\n\tgo build ./...\n\ntest:\n\tgo test ./...\n",
    )
    commands = extract_run_commands(tmp_path)
    labels = {c.label for c in commands}
    assert "make build" in labels
    assert "make test" in labels
    assert all(c.kind == "make" for c in commands)


def test_extract_run_commands_from_npm_scripts(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        '{"name": "demo", "scripts": {"dev": "vite", "build": "vite build"}}',
    )
    commands = extract_run_commands(tmp_path)
    labels = {c.label for c in commands}
    assert "npm run dev" in labels
    assert "npm run build" in labels


def test_extract_run_commands_from_compose(tmp_path: Path) -> None:
    _write(
        tmp_path / "docker-compose.yml",
        "services:\n  web:\n    image: nginx\n  db:\n    image: postgres\n",
    )
    commands = extract_run_commands(tmp_path)
    labels = {c.label for c in commands}
    assert "docker compose up web" in labels
    assert "docker compose up db" in labels


def test_extract_run_commands_from_go_cmd(tmp_path: Path) -> None:
    _write(tmp_path / "cmd" / "server" / "main.go", "package main\n")
    _write(tmp_path / "cmd" / "worker" / "main.go", "package main\n")
    commands = extract_run_commands(tmp_path)
    labels = {c.label for c in commands}
    assert "go run ./cmd/server" in labels
    assert "go run ./cmd/worker" in labels


def test_extract_config_keys_python(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        "import os\n"
        'PORT = os.getenv("APP_PORT")\n'
        'DB = os.environ.get("DATABASE_URL")\n'
        'KEY = os.environ["SECRET_KEY"]\n',
    )
    keys = extract_config_keys(tmp_path)
    keynames = {k.key for k in keys}
    assert "APP_PORT" in keynames
    assert "DATABASE_URL" in keynames
    assert "SECRET_KEY" in keynames


def test_extract_config_keys_go_viper(tmp_path: Path) -> None:
    _write(
        tmp_path / "config.go",
        "package config\n"
        'import "github.com/spf13/viper"\n'
        'func init() { viper.GetString("database.url") }\n',
    )
    keys = extract_config_keys(tmp_path)
    assert any(k.key == "database.url" and k.kind == "config-key" for k in keys)


def test_extract_config_keys_dedupes(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", 'import os\nA = os.getenv("FOO")\n')
    _write(tmp_path / "b.py", 'import os\nB = os.getenv("FOO")\n')
    keys = extract_config_keys(tmp_path)
    foos = [k for k in keys if k.key == "FOO"]
    assert len(foos) == 1


def test_extract_config_keys_skips_vendored(tmp_path: Path) -> None:
    _write(
        tmp_path / "node_modules" / "leaky.js",
        "process.env.LEAKED_FROM_VENDOR;\n",
    )
    _write(tmp_path / "src" / "real.js", "process.env.REAL_KEY;\n")
    keys = extract_config_keys(tmp_path)
    keynames = {k.key for k in keys}
    assert "REAL_KEY" in keynames
    assert "LEAKED_FROM_VENDOR" not in keynames


def test_extract_dependencies_go_mod(tmp_path: Path) -> None:
    _write(
        tmp_path / "go.mod",
        "module github.com/acme/x\n\n"
        "go 1.22\n\n"
        "require (\n"
        "\tgithub.com/spf13/viper v1.18.2\n"
        "\tgolang.org/x/sync v0.6.0\n"
        ")\n",
    )
    deps = extract_dependencies(tmp_path)
    names = {d.name for d in deps}
    assert "github.com/spf13/viper" in names
    assert "golang.org/x/sync" in names
    assert all(d.ecosystem == "go" for d in deps)


def test_extract_dependencies_npm(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        '{"name": "demo", '
        '"dependencies": {"react": "^19.0.0", "vite": "^5.4.0"},'
        '"devDependencies": {"biome": "^1.0.0"}}',
    )
    deps = extract_dependencies(tmp_path)
    names = {d.name for d in deps}
    assert {"react", "vite", "biome"} <= names
    assert all(d.ecosystem == "npm" for d in deps)


def test_extract_dependencies_pyproject(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "demo"\n'
        'dependencies = ["fastapi>=0.110", "pydantic>=2.0,<3.0"]\n',
    )
    deps = extract_dependencies(tmp_path)
    names = {d.name for d in deps}
    assert "fastapi" in names
    assert "pydantic" in names


def test_extract_use_cases_examples_dir(tmp_path: Path) -> None:
    _write(tmp_path / "examples" / "hello.py", "print('hi')\n")
    _write(tmp_path / "examples" / "more" / "demo.py", "x = 1\n")
    cases = extract_use_cases(tmp_path)
    labels = {c.label for c in cases}
    assert any(label.startswith("examples/") for label in labels)


def test_extract_use_cases_readme_section(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "# Demo\n\nIntro paragraph.\n\n"
        "## Usage\n\nRun `demo --port 8080` to start.\n\n"
        "## License\nMIT\n",
    )
    cases = extract_use_cases(tmp_path)
    assert cases
    snippet = cases[0].evidence.snippet
    assert "Usage" in snippet or "demo" in snippet.lower()


def test_extract_use_cases_no_readme(tmp_path: Path) -> None:
    assert extract_use_cases(tmp_path) == []


@pytest.mark.asyncio
async def test_extract_public_api_filters_unexported(
    db_session: AsyncSession,
) -> None:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/manifests-fixture",
        name="manifests-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    db_session.add(repo)
    await db_session.flush()

    nodes = [
        ("github.com/acme/x.PublicHandler", "go"),
        ("github.com/acme/x.privateHelper", "go"),
        ("app.module.PublicFunc", "python"),
        ("app.module._privateFunc", "python"),
    ]
    for qn, lang in nodes:
        db_session.add(
            CodeNode(
                repository_id=repo.id,
                file_path="x.go" if lang == "go" else "module.py",
                qualified_name=qn,
                node_type=CodeNodeType.FUNCTION,
                name=qn.rsplit(".", 1)[-1],
                language=lang,
                start_line=1,
                end_line=10,
                content="def stub(): pass\n",
                content_hash="x" * 64,
            )
        )
    await db_session.flush()

    api = await extract_public_api(session=db_session, repository_id=repo.id)
    qns = {entry.qualified_name for entry in api}
    assert "github.com/acme/x.PublicHandler" in qns
    assert "app.module.PublicFunc" in qns
    assert "github.com/acme/x.privateHelper" not in qns
    assert "app.module._privateFunc" not in qns


@pytest.mark.asyncio
async def test_extract_exported_types_attaches_fields_and_methods(
    db_session: AsyncSession,
) -> None:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/types-fixture",
        name="types-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    db_session.add(repo)
    await db_session.flush()

    parent = CodeNode(
        repository_id=repo.id,
        file_path="generator/generator.go",
        qualified_name="github.com/acme/x/generator.Generator",
        node_type=CodeNodeType.STRUCT,
        name="Generator",
        language="go",
        start_line=10,
        end_line=80,
        content="type Generator struct { Config *Config; Validator Validator }",
        doc_comment="Generator drives code generation.",
        content_hash="a" * 64,
    )
    db_session.add(parent)
    await db_session.flush()

    field_a = CodeNode(
        repository_id=repo.id,
        parent_id=parent.id,
        file_path="generator/generator.go",
        qualified_name="github.com/acme/x/generator.Generator.Config",
        node_type=CodeNodeType.ATTRIBUTE,
        name="Config",
        language="go",
        start_line=11,
        end_line=11,
        content="Config *Config",
        signature="Config *Config",
        content_hash="b" * 64,
    )
    field_b = CodeNode(
        repository_id=repo.id,
        parent_id=parent.id,
        file_path="generator/generator.go",
        qualified_name="github.com/acme/x/generator.Generator.Validator",
        node_type=CodeNodeType.ATTRIBUTE,
        name="Validator",
        language="go",
        start_line=12,
        end_line=12,
        content="Validator Validator",
        signature="Validator Validator",
        content_hash="c" * 64,
    )
    method = CodeNode(
        repository_id=repo.id,
        parent_id=parent.id,
        file_path="generator/generator.go",
        qualified_name="github.com/acme/x/generator.Generator.Run",
        node_type=CodeNodeType.METHOD,
        name="Run",
        language="go",
        start_line=20,
        end_line=40,
        content="func (g *Generator) Run() error { return nil }",
        content_hash="d" * 64,
    )
    db_session.add_all([field_a, field_b, method])
    await db_session.flush()

    types = await extract_exported_types(session=db_session, repository_id=repo.id)
    assert len(types) == 1
    t = types[0]
    assert t.qualified_name == "github.com/acme/x/generator.Generator"
    assert t.kind == "struct"
    assert t.doc_comment == "Generator drives code generation."
    field_names = [f.name for f in t.fields]
    assert "Config" in field_names
    assert "Validator" in field_names
    assert any(f.type_signature == "*Config" for f in t.fields if f.name == "Config")
    assert "github.com/acme/x/generator.Generator.Run" in t.methods


@pytest.mark.asyncio
async def test_extract_exported_types_skips_unexported(
    db_session: AsyncSession,
) -> None:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/types-priv-fixture",
        name="types-priv-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    db_session.add(repo)
    await db_session.flush()

    db_session.add(
        CodeNode(
            repository_id=repo.id,
            file_path="x.go",
            qualified_name="github.com/acme/x.privateThing",
            node_type=CodeNodeType.STRUCT,
            name="privateThing",
            language="go",
            start_line=1,
            end_line=5,
            content="type privateThing struct{}",
            content_hash="z" * 64,
        )
    )
    await db_session.flush()

    types = await extract_exported_types(session=db_session, repository_id=repo.id)
    assert types == []


@pytest.mark.asyncio
async def test_extract_error_types_go_and_python(
    db_session: AsyncSession,
) -> None:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/errors-fixture",
        name="errors-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    db_session.add(repo)
    await db_session.flush()

    nodes = [
        # Go: ValidationError matches; Generator does not.
        CodeNode(
            repository_id=repo.id,
            file_path="errors/errors.go",
            qualified_name="github.com/acme/x/errors.ValidationError",
            node_type=CodeNodeType.STRUCT,
            name="ValidationError",
            language="go",
            start_line=1,
            end_line=5,
            content="type ValidationError struct{ Msg string }",
            content_hash="1" * 64,
        ),
        CodeNode(
            repository_id=repo.id,
            file_path="generator/generator.go",
            qualified_name="github.com/acme/x/generator.Generator",
            node_type=CodeNodeType.STRUCT,
            name="Generator",
            language="go",
            start_line=1,
            end_line=5,
            content="type Generator struct{}",
            content_hash="2" * 64,
        ),
        # Python: leaf-name match.
        CodeNode(
            repository_id=repo.id,
            file_path="app/errors.py",
            qualified_name="app.errors.ParseError",
            node_type=CodeNodeType.CLASS,
            name="ParseError",
            language="python",
            start_line=1,
            end_line=10,
            content="class ParseError(Exception): pass",
            signature="class ParseError(Exception):",
            content_hash="3" * 64,
        ),
        # Python: signature-based detection (no `Error` in name).
        CodeNode(
            repository_id=repo.id,
            file_path="app/errors.py",
            qualified_name="app.errors.Boom",
            node_type=CodeNodeType.CLASS,
            name="Boom",
            language="python",
            start_line=20,
            end_line=25,
            content="class Boom(Exception): pass",
            signature="class Boom(Exception):",
            content_hash="4" * 64,
        ),
    ]
    db_session.add_all(nodes)
    await db_session.flush()

    errors = await extract_error_types(session=db_session, repository_id=repo.id)
    qns = {e.qualified_name for e in errors}
    assert "github.com/acme/x/errors.ValidationError" in qns
    assert "app.errors.ParseError" in qns
    assert "app.errors.Boom" in qns
    assert "github.com/acme/x/generator.Generator" not in qns


@pytest.mark.asyncio
async def test_build_repo_manifests_handles_missing_checkout(
    db_session: AsyncSession,
) -> None:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/empty-fixture",
        name="empty-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    db_session.add(repo)
    await db_session.flush()

    manifests = await build_repo_manifests(
        session=db_session,
        repository_id=repo.id,
        checkout_path=None,
    )
    assert manifests.runtimes == []
    assert manifests.run_commands == []
    assert manifests.config_keys == []
    assert manifests.dependencies == []
    assert manifests.public_api == []
    assert manifests.exported_types == []
    assert manifests.error_types == []
    assert manifests.use_cases == []


@pytest.mark.asyncio
async def test_build_repo_manifests_combines_filesystem_and_db(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/combined-fixture",
        name="combined-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    db_session.add(repo)
    await db_session.flush()

    db_session.add(
        CodeNode(
            repository_id=repo.id,
            file_path="app.py",
            qualified_name="app.PublicFunc",
            node_type=CodeNodeType.FUNCTION,
            name="PublicFunc",
            language="python",
            start_line=1,
            end_line=10,
            content="def stub(): pass\n",
            content_hash="x" * 64,
        )
    )
    await db_session.flush()

    _write(tmp_path / "go.mod", "module x\n\ngo 1.22\n")
    _write(tmp_path / "Makefile", "build:\n\tgo build\n")

    manifests = await build_repo_manifests(
        session=db_session,
        repository_id=repo.id,
        checkout_path=tmp_path,
    )
    assert any(r.name == "go" for r in manifests.runtimes)
    assert any(c.label == "make build" for c in manifests.run_commands)
    assert any(e.qualified_name == "app.PublicFunc" for e in manifests.public_api)
