"""Walk a repository checkout and tally bytes per language.

Issue #66 — the Overview chart used to come from `source_files`, which only
covers files the graph parsers understand (Python, Go). For mixed-language
repos that produced misleading 100% numbers. This scanner walks the whole
checkout once per sync and persists a `Dict[str, int]` keyed by canonical
language name (lowercased GitHub Linguist names), so the API can surface the
true composition.

Kept deliberately minimal: extension → language map, with a curated set of
vendored / generated directories skipped. Not a Linguist clone — we don't try
to detect language from shebangs or file content. The map covers the
languages we care about for the Overview chart; everything else is dropped
silently rather than mis-labelled.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Maximum file size we'll count (bytes). Larger files are almost always
# vendored data / generated assets, and including them skews the chart.
_MAX_FILE_BYTES = 1_024 * 1_024  # 1 MiB

# Top-level / nested directory names we never descend into. The scan happens
# in a checkout that already excludes `.git`, but we still defend against it
# in case the caller hands us a non-bare clone or a working tree with
# vendored code.
_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "bower_components",
        "vendor",
        "third_party",
        "third-party",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".output",
        ".turbo",
        ".cache",
        "coverage",
        ".coverage",
        ".terraform",
        "Pods",
        "DerivedData",
    }
)

# Extension → canonical language name. Lowercased keys, lowercased values.
# Values intentionally match the FE `Language` union where overlap exists
# (`javascript`, `typescript`, `csharp`, etc.) so the same key flows through
# to the chart's color map without translation.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    # systems / app
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".m": "objectivec",
    ".mm": "objectivec",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".rb": "ruby",
    ".php": "php",
    ".pl": "perl",
    ".pm": "perl",
    ".lua": "lua",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".r": "r",
    ".jl": "julia",
    ".nim": "nim",
    ".zig": "zig",
    ".groovy": "groovy",
    ".gradle": "groovy",
    # web
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".svelte": "svelte",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".styl": "stylus",
    # shell / config
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".ps1": "powershell",
    ".bat": "batch",
    ".cmd": "batch",
    # data / markup
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "restructuredtext",
    ".txt": "text",
    ".tex": "tex",
    ".org": "org",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".sql": "sql",
    ".dockerfile": "dockerfile",
    # infra
    ".tf": "terraform",
    ".tfvars": "terraform",
    ".hcl": "hcl",
    ".nix": "nix",
}

# Filenames (no extension or special-case) that map directly to a language.
_FILENAME_TO_LANGUAGE: dict[str, str] = {
    "makefile": "makefile",
    "gnumakefile": "makefile",
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "vagrantfile": "ruby",
    "cmakelists.txt": "cmake",
    "build.gradle": "groovy",
    "settings.gradle": "groovy",
}


def detect_language(file_path: Path) -> str | None:
    """Return the canonical language name for ``file_path``, or None.

    Lookup precedence: full filename match (`Makefile`) before extension match
    (`.go`). Filenames are case-insensitive — `makefile` and `Makefile` map
    the same way.
    """
    lowered_name = file_path.name.lower()
    if lowered_name in _FILENAME_TO_LANGUAGE:
        return _FILENAME_TO_LANGUAGE[lowered_name]
    suffix = file_path.suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(suffix)


def scan_languages(checkout_path: str | Path) -> dict[str, int]:
    """Walk ``checkout_path`` and return a map of language → total bytes.

    Returns an empty dict if the path doesn't exist. Files larger than
    ``_MAX_FILE_BYTES`` are skipped to avoid weighting the chart with vendored
    blobs. Symlinks are not followed.
    """
    root = Path(checkout_path)
    if not root.is_dir():
        return {}

    bytes_by_language: dict[str, int] = {}

    # os.walk-style iteration via Path so we can prune _SKIP_DIR_NAMES.
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError) as exc:
            logger.debug("Skipping unreadable directory %s: %s", current, exc)
            continue

        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in _SKIP_DIR_NAMES:
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue

            language = detect_language(entry)
            if language is None:
                continue

            try:
                size = entry.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > _MAX_FILE_BYTES:
                continue

            bytes_by_language[language] = bytes_by_language.get(language, 0) + size

    return bytes_by_language
