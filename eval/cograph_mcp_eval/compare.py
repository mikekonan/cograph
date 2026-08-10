"""Print baseline ↔ after diff against H1-H7 targets.

Usage:
    python -m eval.cograph_mcp_eval.compare \\
        --baseline=results/baseline.summary.json \\
        --after=results/after.summary.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Targets per H1-H7 (see plan §"Hypothesis verification").
#
# H7 — too_early_giveup_rate — uses `must_be_zero` rather than
# `lower_or_equal` because the playbook explicitly forbids giving up
# before ≥3 distinct attempts; a non-zero value on `after` means the
# rule isn't landing and the merge should not ship. We also accept a
# zero baseline (= the prior behavior already didn't bail early) — the
# constraint isn't "improve" here, it's "stay at zero".
TARGETS = {
    "median_tool_calls": {"direction": "lower", "min_delta_pct": 30, "label": "tool calls / answer"},
    "median_tokens_estimate": {"direction": "lower", "min_delta_pct": 40, "label": "tokens / answer"},
    "cites_provenance_rate": {"direction": "higher", "min_delta_pp": 10, "min_abs": 0.80, "label": "cites provenance"},
    "correctness_rate": {"direction": "higher_or_equal", "tol_pp": 2, "label": "correctness"},
    "silent_fallback_rate": {"direction": "lower_or_equal", "label": "silent fallback"},
    "too_early_giveup_rate": {"direction": "must_be_zero", "label": "early give-up (H7)"},
}


def _fmt(value: float, kind: str) -> str:
    if kind == "rate":
        return f"{value * 100:.1f}%"
    return f"{value:.1f}"


def _evaluate(metric: str, baseline: float, after: float) -> tuple[str, str]:
    spec = TARGETS[metric]
    direction = spec["direction"]
    delta = after - baseline

    if direction == "lower":
        # Conventional delta semantics: negative = went down (improvement here).
        delta_pct = (after / baseline - 1) * 100 if baseline else 0
        improvement_pct = -delta_pct
        passed = improvement_pct >= spec["min_delta_pct"]
        return f"{delta_pct:+.1f}%", "✅" if passed else "❌"

    if direction == "higher":
        delta_pp = delta * 100
        passed = delta_pp >= spec["min_delta_pp"] and after >= spec["min_abs"]
        return f"{delta_pp:+.1f}pp", "✅" if passed else "❌"

    if direction == "higher_or_equal":
        delta_pp = delta * 100
        passed = delta_pp >= -spec["tol_pp"]
        return f"{delta_pp:+.1f}pp", "✅" if passed else "❌"

    if direction == "lower_or_equal":
        delta_pp = delta * 100
        passed = after <= baseline + 1e-9
        return f"{delta_pp:+.1f}pp", "✅" if passed else "❌"

    if direction == "must_be_zero":
        # Hard zero — any positive rate fails. We still print the delta
        # so the operator sees whether the change made it worse or
        # better when both baseline and after are non-zero.
        delta_pp = delta * 100
        passed = after < 1e-9
        return f"{delta_pp:+.1f}pp", "✅" if passed else "❌"

    return "?", "?"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True)
    p.add_argument("--after", required=True)
    args = p.parse_args()

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))

    rows = []
    all_pass = True
    for metric, spec in TARGETS.items():
        b = baseline.get(metric, 0)
        a = after.get(metric, 0)
        kind = "rate" if "rate" in metric else "raw"
        delta, status = _evaluate(metric, b, a)
        if status == "❌":
            all_pass = False
        rows.append(
            (spec["label"], _fmt(b, kind), _fmt(a, kind), delta, status)
        )

    width_label = max(len(r[0]) for r in rows) + 2
    print(f"{'metric':<{width_label}} {'baseline':>10} {'after':>10} {'delta':>10}  {'pass'}")
    print("-" * (width_label + 38))
    for label, b, a, delta, status in rows:
        print(f"{label:<{width_label}} {b:>10} {a:>10} {delta:>10}  {status}")

    print()
    print(f"OVERALL: {'✅ ship it' if all_pass else '❌ hold the merge'}")
    print(f"baseline coverage: {baseline.get('coverage', '?')}")
    print(f"after coverage:    {after.get('coverage', '?')}")


if __name__ == "__main__":
    main()
