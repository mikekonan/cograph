from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_SUPPORTED_GOOS = frozenset({"linux", "darwin", "windows"})
_SUPPORTED_GOARCH = frozenset({"amd64", "arm64"})
_SUPPORTED_CGO = frozenset({"cgo", "nocgo"})
_KNOWN_GOOS = frozenset(
    {
        "aix",
        "android",
        "darwin",
        "dragonfly",
        "freebsd",
        "illumos",
        "ios",
        "js",
        "linux",
        "netbsd",
        "openbsd",
        "plan9",
        "solaris",
        "wasip1",
        "windows",
    }
)
_KNOWN_GOARCH = frozenset(
    {
        "386",
        "amd64",
        "arm",
        "arm64",
        "loong64",
        "mips",
        "mips64",
        "mips64le",
        "mipsle",
        "ppc64",
        "ppc64le",
        "riscv64",
        "s390x",
        "wasm",
    }
)
_GO_BUILD_PREFIX = "//go:build "
_VERSION_PATTERN = re.compile(r"^(?:go)?(?P<major>\d+)\.(?P<minor>\d+)(?:\.\d+)?$")
_TOKEN_PATTERN = re.compile(r"\s*(&&|\|\||!|\(|\)|[A-Za-z0-9_.]+)")


class _TruthValue(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"
    NEUTRAL = "neutral"


class GoVariantSelectionError(RuntimeError):
    error_code: str = "graph_ingest_failed"


class GoBuildConstraintUnsupportedError(GoVariantSelectionError):
    error_code = "GO_BUILD_CONSTRAINT_UNSUPPORTED"


class GoBuildVariantConflictError(GoVariantSelectionError):
    error_code = "GO_BUILD_VARIANT_CONFLICT"


@dataclass(slots=True, frozen=True, kw_only=True)
class GoIndexProfile:
    effective_go_version: str
    effective_goos: str
    effective_goarch: str
    effective_cgo: bool
    version_key: tuple[int, int]


@dataclass(slots=True, frozen=True, kw_only=True)
class GoSelectedFile:
    absolute_path: Path
    relative_path: str
    source_text: str
    content_hash: str


@dataclass(slots=True, frozen=True, kw_only=True)
class GoPackageSelection:
    package_key: str
    selected_files: tuple[GoSelectedFile, ...]


def resolve_go_index_profile(root_path: Path) -> GoIndexProfile:
    effective_go_version = "go1.22"
    go_mod = root_path / "go.mod"
    if go_mod.is_file():
        try:
            lines = go_mod.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        toolchain = _first_go_mod_value(lines, "toolchain")
        directive = _first_go_mod_value(lines, "go")
        if toolchain is not None:
            effective_go_version = toolchain
        elif directive is not None:
            effective_go_version = directive

    normalized = _normalize_go_version(effective_go_version) or "go1.22"
    return GoIndexProfile(
        effective_go_version=normalized,
        effective_goos="linux",
        effective_goarch="amd64",
        effective_cgo=False,
        version_key=_parse_version_key(normalized),
    )


def select_go_package_files(
    *,
    root_path: Path,
    package_key: str,
    files: tuple[Path, ...],
    profile: GoIndexProfile,
) -> GoPackageSelection:
    selected: list[GoSelectedFile] = []
    for file_path in sorted(files):
        source_text = file_path.read_text(encoding="utf-8")
        truth = _evaluate_file_selection(
            file_path=file_path.relative_to(root_path),
            source_text=source_text,
            profile=profile,
        )
        if truth is _TruthValue.UNKNOWN:
            raise GoBuildConstraintUnsupportedError(
                f"Could not resolve Go build constraints for {file_path.relative_to(root_path).as_posix()} "
                f"under canonical profile {profile.effective_go_version}/{profile.effective_goos}/"
                f"{profile.effective_goarch}/cgo={str(profile.effective_cgo).lower()}"
            )
        if truth is _TruthValue.FALSE:
            continue
        relative_path = file_path.relative_to(root_path).as_posix()
        selected.append(
            GoSelectedFile(
                absolute_path=file_path,
                relative_path=relative_path,
                source_text=source_text,
                content_hash=_content_hash(source_text),
            )
        )

    return GoPackageSelection(
        package_key=package_key,
        selected_files=tuple(selected),
    )


def _first_go_mod_value(lines: list[str], key: str) -> str | None:
    prefix = f"{key} "
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        token = stripped.removeprefix(prefix).strip().split()[0]
        normalized = _normalize_go_version(token)
        if normalized is not None:
            return normalized
    return None


def _normalize_go_version(value: str) -> str | None:
    match = _VERSION_PATTERN.match(value.strip())
    if match is None:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    return f"go{major}.{minor}"


def _parse_version_key(value: str) -> tuple[int, int]:
    match = _VERSION_PATTERN.match(value)
    if match is None:
        return (1, 22)
    return (int(match.group("major")), int(match.group("minor")))


def _evaluate_file_selection(
    *,
    file_path: Path,
    source_text: str,
    profile: GoIndexProfile,
) -> _TruthValue:
    filename_truth = _evaluate_filename_constraints(file_path=file_path, profile=profile)
    if filename_truth is _TruthValue.FALSE:
        return _TruthValue.FALSE

    build_expr = _extract_go_build_expression(source_text)
    if build_expr is None:
        return filename_truth

    build_truth = _evaluate_build_expression(build_expr, profile=profile)
    return _tri_and(filename_truth, build_truth)


def _extract_go_build_expression(source_text: str) -> str | None:
    expressions: list[str] = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//"):
            if stripped.startswith(_GO_BUILD_PREFIX):
                expressions.append(stripped.removeprefix(_GO_BUILD_PREFIX).strip())
            continue
        break

    if not expressions:
        return None
    if len(expressions) > 1:
        raise GoBuildConstraintUnsupportedError("Multiple //go:build expressions are not supported")
    return expressions[0]


def _evaluate_filename_constraints(
    *,
    file_path: Path,
    profile: GoIndexProfile,
) -> _TruthValue:
    stem = file_path.stem
    parts = stem.split("_")
    if len(parts) == 1:
        return _TruthValue.TRUE

    index = len(parts) - 1
    if index >= 1 and parts[index] == "test":
        index -= 1
    truths: list[_TruthValue] = []

    if parts[index] in _SUPPORTED_CGO:
        truths.append(
            _truth(parts[index] == "cgo", expected=profile.effective_cgo)
        )
        index -= 1

    index, goarch_truth = _consume_filename_selector(
        file_path=file_path,
        index=index,
        parts=parts,
        supported=_SUPPORTED_GOARCH,
        known=_KNOWN_GOARCH,
        expected=profile.effective_goarch,
        selector_kind="GOARCH",
    )
    if goarch_truth is not None:
        truths.append(goarch_truth)

    index, goos_truth = _consume_filename_selector(
        file_path=file_path,
        index=index,
        parts=parts,
        supported=_SUPPORTED_GOOS,
        known=_KNOWN_GOOS,
        expected=profile.effective_goos,
        selector_kind="GOOS",
    )
    if goos_truth is not None:
        truths.append(goos_truth)

    if not truths:
        return _TruthValue.TRUE

    value = _TruthValue.TRUE
    for truth in truths:
        value = _tri_and(value, truth)
    return value


def _consume_filename_selector(
    *,
    file_path: Path,
    index: int,
    parts: list[str],
    supported: frozenset[str],
    known: frozenset[str],
    expected: str,
    selector_kind: str,
) -> tuple[int, _TruthValue | None]:
    if index < 1:
        return index, None

    token = parts[index]
    if token in supported:
        return index - 1, _truth(token, expected=expected)
    if token in known:
        raise GoBuildConstraintUnsupportedError(
            f"Unsupported Go filename {selector_kind} selector '{token}' in "
            f"{file_path.as_posix()}"
        )
    return index, None


def _evaluate_build_expression(expression: str, *, profile: GoIndexProfile) -> _TruthValue:
    tokens = _tokenize(expression)
    if not tokens:
        raise GoBuildConstraintUnsupportedError("Empty //go:build expression")
    parser = _TokenParser(tokens=tokens, profile=profile)
    value = parser.parse_expression()
    if parser.has_remaining():
        raise GoBuildConstraintUnsupportedError(
            f"Unsupported Go build expression tail: {' '.join(tokens[parser.position:])}"
        )
    return value


def _tokenize(expression: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(expression):
        match = _TOKEN_PATTERN.match(expression, position)
        if match is None:
            raise GoBuildConstraintUnsupportedError(
                f"Unsupported Go build expression token near: {expression[position:]}"
            )
        token = match.group(1)
        position = match.end()
        if token:
            tokens.append(token)
    return tokens


@dataclass(slots=True, kw_only=True)
class _TokenParser:
    tokens: list[str]
    profile: GoIndexProfile
    position: int = 0

    def parse_expression(self) -> _TruthValue:
        return self._parse_or()

    def has_remaining(self) -> bool:
        return self.position < len(self.tokens)

    def _parse_or(self) -> _TruthValue:
        value = self._parse_and()
        while self._peek() == "||":
            self._pop("||")
            value = _tri_or(value, self._parse_and())
        return value

    def _parse_and(self) -> _TruthValue:
        value = self._parse_unary()
        while self._peek() == "&&":
            self._pop("&&")
            value = _tri_and(value, self._parse_unary())
        return value

    def _parse_unary(self) -> _TruthValue:
        if self._peek() == "!":
            self._pop("!")
            return _tri_not(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> _TruthValue:
        token = self._peek()
        if token is None:
            raise GoBuildConstraintUnsupportedError("Unexpected end of //go:build expression")
        if token == "(":
            self._pop("(")
            value = self._parse_expression()
            self._pop(")")
            return value
        self.position += 1
        return _evaluate_identifier(token, profile=self.profile)

    def _peek(self) -> str | None:
        if self.position >= len(self.tokens):
            return None
        return self.tokens[self.position]

    def _pop(self, expected: str) -> None:
        token = self._peek()
        if token != expected:
            raise GoBuildConstraintUnsupportedError(
                f"Expected '{expected}' in //go:build expression, got '{token}'"
            )
        self.position += 1


def _evaluate_identifier(identifier: str, *, profile: GoIndexProfile) -> _TruthValue:
    if identifier in _SUPPORTED_GOOS:
        return _truth(identifier, expected=profile.effective_goos)
    if identifier in _SUPPORTED_GOARCH:
        return _truth(identifier, expected=profile.effective_goarch)
    if identifier == "cgo":
        return _truth(True, expected=profile.effective_cgo)
    if identifier == "nocgo":
        return _truth(False, expected=profile.effective_cgo)
    if identifier.startswith("go1."):
        version_key = _parse_version_key(identifier)
        return _truth(version_key <= profile.version_key, expected=True)
    if _is_known_unsupported_build_identifier(identifier):
        return _TruthValue.UNKNOWN
    # Custom build tags are intentionally neutral: they cannot select a canonical
    # winner, but they also must not force the whole package into an unsupported
    # failure when the supported selectors already resolve the file-set.
    return _TruthValue.NEUTRAL


def _is_known_unsupported_build_identifier(identifier: str) -> bool:
    if identifier in _KNOWN_GOOS or identifier in _KNOWN_GOARCH:
        return True
    if identifier in {"unix", "gc", "gccgo"}:
        return True
    prefix, _, _suffix = identifier.partition(".")
    return prefix in _KNOWN_GOARCH


def _truth(value: object, *, expected: object) -> _TruthValue:
    if value == expected:
        return _TruthValue.TRUE
    return _TruthValue.FALSE


def _tri_not(value: _TruthValue) -> _TruthValue:
    if value is _TruthValue.TRUE:
        return _TruthValue.FALSE
    if value is _TruthValue.FALSE:
        return _TruthValue.TRUE
    if value is _TruthValue.NEUTRAL:
        return _TruthValue.NEUTRAL
    return _TruthValue.UNKNOWN


def _tri_and(left: _TruthValue, right: _TruthValue) -> _TruthValue:
    if left is _TruthValue.FALSE or right is _TruthValue.FALSE:
        return _TruthValue.FALSE
    if left is _TruthValue.NEUTRAL:
        return right
    if right is _TruthValue.NEUTRAL:
        return left
    if left is _TruthValue.TRUE and right is _TruthValue.TRUE:
        return _TruthValue.TRUE
    return _TruthValue.UNKNOWN


def _tri_or(left: _TruthValue, right: _TruthValue) -> _TruthValue:
    if left is _TruthValue.TRUE or right is _TruthValue.TRUE:
        return _TruthValue.TRUE
    if left is _TruthValue.NEUTRAL:
        return right
    if right is _TruthValue.NEUTRAL:
        return left
    if left is _TruthValue.FALSE and right is _TruthValue.FALSE:
        return _TruthValue.FALSE
    return _TruthValue.UNKNOWN


def _content_hash(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
