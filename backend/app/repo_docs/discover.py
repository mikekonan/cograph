from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath

SUPPORTED_REPO_DOC_SUFFIXES = {".md", ".mdx", ".rst"}
_WORKFLOW_SUFFIXES = {".yml", ".yaml"}
_EXAMPLE_SUFFIXES = {
    ".go",
    ".js",
    ".json",
    ".md",
    ".mdx",
    ".py",
    ".rst",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_TEST_SUFFIXES = {".go", ".js", ".md", ".mdx", ".py", ".rst", ".ts"}
_CONFIG_SUFFIXES = {".cfg", ".conf", ".env", ".ini", ".json", ".toml", ".yaml", ".yml"}
_EXAMPLE_DIRS = {"example", "examples", "sample", "samples"}
_TEST_DIRS = {"test", "tests"}
_DEPLOY_DIRS = {".devcontainer", "deploy", "deployment", "helm", "k8s", "ops"}
_ALLOWED_HIDDEN_DIRS = {".devcontainer", ".github"}
_IGNORED_PARENT_DIRS = {
    ".cache",
    ".cograph",
    ".codexpotter",
    ".entire",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_ROOT_CONFIG_FILES = {
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "dockerfile",
    "go.mod",
    "go.work",
    "makefile",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
}


class RepoDocumentKind(StrEnum):
    REPO_DOC = "Repo Doc"
    EXAMPLE = "Example"
    TEST = "Test"
    CONFIG = "Config"
    WORKFLOW = "Workflow"


def classify_repo_document(
    relative_path: str | PurePosixPath,
) -> RepoDocumentKind | None:
    path = PurePosixPath(relative_path)
    lower_parts = tuple(part.lower() for part in path.parts)
    name = path.name
    lower_name = name.lower()
    suffix = path.suffix.lower()

    if suffix in SUPPORTED_REPO_DOC_SUFFIXES:
        return RepoDocumentKind.REPO_DOC

    if (
        len(lower_parts) >= 2
        and lower_parts[0] == ".github"
        and lower_parts[1] == "workflows"
    ):
        if suffix in _WORKFLOW_SUFFIXES:
            return RepoDocumentKind.WORKFLOW

    if any(part in _EXAMPLE_DIRS for part in lower_parts):
        if suffix in _EXAMPLE_SUFFIXES:
            return RepoDocumentKind.EXAMPLE

    if any(part in _TEST_DIRS for part in lower_parts):
        if suffix in SUPPORTED_REPO_DOC_SUFFIXES or (
            suffix in _TEST_SUFFIXES
            and (lower_name.startswith("test_") or lower_name.endswith("_test.go"))
        ):
            return RepoDocumentKind.TEST

    if lower_name in _ROOT_CONFIG_FILES or name in {"Dockerfile", "Makefile"}:
        return RepoDocumentKind.CONFIG

    if lower_name.startswith(".env") or suffix == ".env":
        return RepoDocumentKind.CONFIG

    if lower_parts and lower_parts[0] in _DEPLOY_DIRS and suffix in _CONFIG_SUFFIXES:
        return RepoDocumentKind.CONFIG

    return None


class RepoDocumentDiscoverer:
    def discover(self, root_path: Path) -> tuple[Path, ...]:
        if not root_path.exists():
            raise FileNotFoundError(f"Checkout path not found: {root_path}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"Checkout path is not a directory: {root_path}")

        return tuple(
            sorted(
                path
                for path in root_path.rglob("*")
                if path.is_file()
                and not _has_ignored_parent(path.relative_to(root_path))
                and classify_repo_document(path.relative_to(root_path).as_posix())
                is not None
            )
        )


def _has_ignored_parent(relative_path: PurePosixPath) -> bool:
    for part in relative_path.parts[:-1]:
        if part in _ALLOWED_HIDDEN_DIRS:
            continue
        if part in _IGNORED_PARENT_DIRS or part.startswith("."):
            return True
    return False
