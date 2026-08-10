"""Tests for `cli_extractor` (Stage 0 helper).

Inline Go fixtures cover the canonical CLI frameworks the salience
scorer needs to recognize: spf13/cobra (single + nested subcommands),
urfave/cli, and the standard library `flag` package. Each scenario
asserts the surface seen by `repo_signals` matches what a real
binary's `--help` output would show.
"""

from __future__ import annotations

from backend.app.wiki.cli_extractor import extract_cli_commands


# ---------------------------------------------------------------------------
# Cobra fixtures
# ---------------------------------------------------------------------------


_COBRA_ROOT = """
package main

import (
    "github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
    Use:   "go-oas3",
    Short: "Generate Go from OpenAPI 3",
}

func main() {
    rootCmd.Execute()
}
"""

_COBRA_VALIDATE = """
package main

import (
    "github.com/spf13/cobra"
)

var validateCmd = &cobra.Command{
    Use:   "validate",
    Short: "Validate an OpenAPI 3 spec",
}

func init() {
    rootCmd.AddCommand(validateCmd)
    validateCmd.Flags().StringP("spec", "s", "", "path to OpenAPI spec")
    validateCmd.Flags().Bool("strict", false, "enable strict mode")
    validateCmd.PersistentFlags().IntVar(&verbose, "verbose", 0, "log level")
}

var verbose int
"""


def test_cobra_single_command_extracted():
    cmds = extract_cli_commands(
        [
            ("cmd/go-oas3/root.go", _COBRA_ROOT),
        ]
    )
    names = [c.name for c in cmds]
    assert "go-oas3" in names


def test_cobra_subcommand_with_flags_extracted():
    cmds = extract_cli_commands(
        [
            ("cmd/go-oas3/root.go", _COBRA_ROOT),
            ("cmd/go-oas3/validate.go", _COBRA_VALIDATE),
        ]
    )
    validate = next(c for c in cmds if c.name == "validate")
    assert validate.parent_path == "go-oas3"
    assert "spec" in validate.flags
    assert "strict" in validate.flags
    assert "verbose" in validate.flags
    assert validate.source_path == "cmd/go-oas3/validate.go"
    assert validate.source_start_line is not None
    assert validate.source_end_line is not None


def test_cobra_nested_subcommands_resolve_parent_chain():
    src_root = """
    package main
    import "github.com/spf13/cobra"
    var rootCmd = &cobra.Command{Use: "tool"}
    """
    src_repo = """
    package main
    import "github.com/spf13/cobra"
    var repoCmd = &cobra.Command{Use: "repo"}
    func init() { rootCmd.AddCommand(repoCmd) }
    """
    src_clone = """
    package main
    import "github.com/spf13/cobra"
    var cloneCmd = &cobra.Command{Use: "clone"}
    func init() {
        repoCmd.AddCommand(cloneCmd)
        cloneCmd.Flags().String("branch", "", "branch name")
    }
    """
    cmds = extract_cli_commands(
        [
            ("cmd/tool/root.go", src_root),
            ("cmd/tool/repo.go", src_repo),
            ("cmd/tool/clone.go", src_clone),
        ]
    )
    clone = next(c for c in cmds if c.name == "clone")
    assert clone.parent_path == "tool/repo"
    assert "branch" in clone.flags


# ---------------------------------------------------------------------------
# urfave/cli fixture
# ---------------------------------------------------------------------------


_URFAVE_APP = """
package main

import (
    "github.com/urfave/cli/v2"
)

func main() {
    app := &cli.App{
        Name: "uploader",
    }
    sub := cli.Command{
        Name: "push",
    }
    _ = app
    _ = sub
}
"""


def test_urfave_cli_app_and_subcommand_extracted():
    cmds = extract_cli_commands(
        [
            ("cmd/uploader/main.go", _URFAVE_APP),
        ]
    )
    names = {c.name for c in cmds}
    assert "uploader" in names
    assert "push" in names


# ---------------------------------------------------------------------------
# Standard library `flag` package
# ---------------------------------------------------------------------------


_FLAG_MAIN = """
package main

import (
    "flag"
    "fmt"
)

func main() {
    spec := flag.String("spec", "", "path to OpenAPI spec")
    var out string
    flag.StringVar(&out, "out", "gen/", "output dir")
    verbose := flag.Bool("verbose", false, "verbose logging")
    flag.Parse()
    fmt.Println(*spec, out, *verbose)
}
"""


def test_flag_package_synthesizes_command_per_cmd_dir():
    cmds = extract_cli_commands(
        [
            ("cmd/go-oas3/main.go", _FLAG_MAIN),
        ]
    )
    binary = next(c for c in cmds if c.name == "go-oas3")
    assert binary.parent_path == ""
    assert "spec" in binary.flags
    assert "out" in binary.flags
    assert "verbose" in binary.flags


def test_flag_package_outside_cmd_dir_is_ignored():
    """A `flag.*` call inside an internal package should NOT synthesize
    a phantom command — only `cmd/<bin>/` files do."""
    cmds = extract_cli_commands(
        [
            ("internal/util/parse.go", _FLAG_MAIN),
        ]
    )
    assert cmds == []


# ---------------------------------------------------------------------------
# Mixed / robustness
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_list():
    assert extract_cli_commands([]) == []


def test_non_cli_go_source_yields_no_commands():
    src = """
    package main
    import "fmt"
    func main() { fmt.Println("hello") }
    """
    assert extract_cli_commands([("cmd/x/main.go", src)]) == []


def test_blank_source_is_skipped():
    cmds = extract_cli_commands(
        [
            ("cmd/x/main.go", ""),
            ("cmd/x/root.go", _COBRA_ROOT),
        ]
    )
    assert any(c.name == "go-oas3" for c in cmds)


def test_disambiguation_validate_subcommand_vs_internal_validator():
    """The S2 disambiguation acceptance test: a `validate` Cobra
    subcommand must be detected even when the repo also has an
    `internal/validator/` package. The CLI extractor doesn't classify
    salience — it just emits CLI evidence — but the presence of the
    `validate` command in the output is the linchpin that prevents the
    salience scorer from promoting `internal/validator` over the real
    user-facing surface."""
    cmds = extract_cli_commands(
        [
            ("cmd/go-oas3/root.go", _COBRA_ROOT),
            ("cmd/go-oas3/validate.go", _COBRA_VALIDATE),
            (
                "internal/validator/validator.go",
                """
                package validator
                import "flag"
                var _ = flag.Bool("strict", false, "")
                """,
            ),
        ]
    )
    names = [c.name for c in cmds]
    # go-oas3 root is detected.
    assert "go-oas3" in names
    # validate subcommand is detected with parent chain.
    validate = next(c for c in cmds if c.name == "validate")
    assert validate.parent_path == "go-oas3"
    # internal/validator's bare `flag.Bool` does NOT synthesize a
    # phantom command (it's outside `cmd/`).
    assert all(c.source_path != "internal/validator/validator.go" for c in cmds)


def test_results_are_deterministic():
    inputs = [
        ("cmd/go-oas3/root.go", _COBRA_ROOT),
        ("cmd/go-oas3/validate.go", _COBRA_VALIDATE),
    ]
    a = extract_cli_commands(inputs)
    b = extract_cli_commands(inputs)
    assert [(c.parent_path, c.name) for c in a] == [(c.parent_path, c.name) for c in b]


def test_flags_are_deduplicated_and_sorted():
    src = """
    package main
    import "github.com/spf13/cobra"
    var c = &cobra.Command{Use: "x"}
    func init() {
        c.Flags().String("alpha", "", "")
        c.Flags().String("beta", "", "")
        c.Flags().String("alpha", "", "duplicate registration")
    }
    """
    cmds = extract_cli_commands([("cmd/x/x.go", src)])
    x = next(c for c in cmds if c.name == "x")
    assert x.flags == ["alpha", "beta"]
