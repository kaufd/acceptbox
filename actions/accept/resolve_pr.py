#!/usr/bin/env python3
"""Resolve AcceptBox PR parameters for autonomous CI execution (fail-closed).

Determines:
- policy ("bugfix" | "refactor") from explicit input, PR labels, or commit trailers;
- repro SHA (for bugfix policy) from explicit input, `AcceptBox-Role: repro` trailer,
  or first commit in `base..head`;
- mode ("command" | "witnessbox" | None) and invariants (`--inv`);
- trusted `ebt_sha` and `approved_tree` from `origin/<base-ref>` when running in
  witnessbox mode without explicit overrides.

Uses only the Python standard library. Exit code 0 on success, 2 on error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TRAILER_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s*:\s*(.+?)\s*$")


class ResolveError(ValueError):
    """Fail-closed resolution failure (exit code 2)."""


def _clean_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        env=_clean_git_env(), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        msg = detail[-1] if detail else f"exit {proc.returncode}"
        raise ResolveError(f"git {' '.join(args[:2])}: {msg}")
    return proc.stdout


def _validate_sha(value: str, label: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise ResolveError(f"{label}: expected full 40/64-hex SHA, got {value!r}")
    return value.lower()


def _commits_in_range(repo: Path, base: str, head: str) -> list[str]:
    out = _git(repo, "rev-list", "--first-parent", "--reverse", f"{base}..{head}")
    return [line.strip().lower() for line in out.splitlines() if line.strip()]


def _commit_trailers(repo: Path, sha: str) -> dict[str, list[str]]:
    body = _git(repo, "show", "-s", "--format=%B", sha)
    trailers: dict[str, list[str]] = {}
    for line in body.splitlines():
        match = _TRAILER_RE.match(line)
        if match:
            key = match.group(1).lower()
            trailers.setdefault(key, []).append(match.group(2).strip())
    return trailers


def _parse_inv_from_text(text: str) -> list[str]:
    invs: list[str] = []
    for line in text.splitlines():
        match = _TRAILER_RE.match(line)
        if match and match.group(1).lower() == "acceptbox-inv":
            for item in match.group(2).split(","):
                cleaned = item.strip()
                if cleaned and cleaned not in invs:
                    invs.append(cleaned)
    return invs


def resolve_plan(*, repo: Path, base: str, head: str, base_ref: str,
                 policy_input: str = "auto", mode_input: str = "auto",
                 repro_input: str = "", repro_strategy: str = "first-commit",
                 inv_input: str = "", ebt_sha_input: str = "",
                 approved_tree_input: str = "", pr_labels: list[str] | None = None,
                 pr_body: str = "", acceptbox_bin: str = "acceptbox") -> dict:
    base_sha = _validate_sha(base.strip(), "base")
    head_sha = _validate_sha(head.strip(), "head")
    commits = _commits_in_range(repo, base_sha, head_sha)
    if not commits:
        raise ResolveError(f"no commits found in {base_sha[:12]}..{head_sha[:12]}")

    all_trailers = {sha: _commit_trailers(repo, sha) for sha in commits}

    # 1. Policy resolution ("bugfix" | "refactor").
    if policy_input in ("bugfix", "refactor"):
        policy = policy_input
    elif policy_input == "auto":
        labels = {lbl.strip().lower() for lbl in (pr_labels or []) if lbl.strip()}
        trailer_policies = {
            val.lower()
            for t in all_trailers.values()
            for val in t.get("acceptbox-policy", ())
        }
        invalid_policies = trailer_policies - {"bugfix", "refactor"}
        if invalid_policies:
            raise ResolveError(
                f"invalid AcceptBox-Policy trailer value(s): {sorted(invalid_policies)}"
            )
        if len(trailer_policies) > 1:
            raise ResolveError(
                f"conflicting AcceptBox-Policy trailers across commits: {sorted(trailer_policies)}"
            )
        if trailer_policies:
            policy = next(iter(trailer_policies))
        elif "refactor" in labels or "acceptbox:refactor" in labels:
            policy = "refactor"
        else:
            policy = "bugfix"
    else:
        raise ResolveError(f"invalid policy {policy_input!r}; expected auto, bugfix, or refactor")

    # 2. Repro resolution (required for bugfix, forbidden for refactor).
    repro_sha: str | None = None
    repro_source: str | None = None
    if policy == "refactor":
        if repro_input.strip():
            raise ResolveError("repro input cannot be set when policy is refactor")
    else:
        if repro_input.strip():
            repro_sha = _validate_sha(repro_input.strip(), "repro")
            repro_source = "input"
        else:
            marked = [
                sha for sha in commits
                if any(v.lower() == "repro" for v in all_trailers[sha].get("acceptbox-role", ()))
            ]
            if len(marked) > 1:
                raise ResolveError(
                    f"multiple commits carry AcceptBox-Role: repro: {', '.join(s[:12] for s in marked)}"
                )
            if len(marked) == 1:
                repro_sha = marked[0]
                repro_source = "trailer"
            elif repro_strategy == "first-commit":
                repro_sha = commits[0]
                repro_source = "first-commit"
            elif repro_strategy == "trailer-only":
                raise ResolveError(
                    "no commit carries 'AcceptBox-Role: repro' trailer (repro-strategy=trailer-only)"
                )
            else:
                raise ResolveError(
                    f"invalid repro-strategy {repro_strategy!r}; expected first-commit or trailer-only"
                )

    # 3. Invariants and mode resolution.
    if inv_input.strip():
        invs = [item.strip() for item in inv_input.split(",") if item.strip()]
    else:
        invs = []
        for sha in commits:
            for val in all_trailers[sha].get("acceptbox-inv", ()):
                for item in val.split(","):
                    cleaned = item.strip()
                    if cleaned and cleaned not in invs:
                        invs.append(cleaned)
        if not invs and pr_body:
            invs = _parse_inv_from_text(pr_body)

    if mode_input in ("command", "witnessbox"):
        mode: str | None = mode_input
    elif mode_input == "auto":
        mode = "witnessbox" if invs else None
    else:
        raise ResolveError(f"invalid mode {mode_input!r}; expected auto, command, or witnessbox")

    # 4. Trusted EBT pinning when mode == "witnessbox".
    ebt_sha: str | None = ebt_sha_input.strip() or None
    approved_tree: str | None = approved_tree_input.strip() or None
    if bool(ebt_sha) != bool(approved_tree):
        raise ResolveError("ebt-sha and approved-tree must either both be set or both be omitted")
    if ebt_sha is not None:
        ebt_sha = _validate_sha(ebt_sha, "ebt-sha")
    if approved_tree is not None:
        if not _SHA256_RE.fullmatch(approved_tree):
            raise ResolveError(f"approved-tree: expected 64-hex SHA-256, got {approved_tree!r}")
        approved_tree = approved_tree.lower()

    if mode == "witnessbox" and ebt_sha is None:
        if not base_ref.strip():
            raise ResolveError("base-ref is required to auto-pin ebt/ in witnessbox mode")
        ref = base_ref.strip()
        refspec = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
        _git(repo, "-c", "core.hooksPath=/dev/null", "fetch", "--no-tags", "--",
             "origin", f"+{refspec}:refs/acceptbox-ci/ebt-ref")
        ebt_sha = _git(repo, "rev-parse", "--verify", "refs/acceptbox-ci/ebt-ref^{commit}").strip().lower()
        pin_proc = subprocess.run(
            [acceptbox_bin, "pin", "--ebt-repo", str(repo), "--ebt-sha", ebt_sha],
            env=_clean_git_env(), capture_output=True, text=True, check=False,
        )
        if pin_proc.returncode != 0:
            raise ResolveError(
                f"acceptbox pin failed for {ebt_sha}: {pin_proc.stderr.strip() or pin_proc.stdout.strip()}"
            )
        approved_tree = pin_proc.stdout.strip().lower()
        if not _SHA256_RE.fullmatch(approved_tree):
            raise ResolveError(f"acceptbox pin returned unexpected hash: {approved_tree!r}")

    return {
        "policy": policy,
        "mode": mode,
        "base": base_sha,
        "head": head_sha,
        "repro": repro_sha,
        "repro_source": repro_source,
        "base_ref": base_ref.strip(),
        "inv": ",".join(invs) if invs else "",
        "ebt_sha": ebt_sha or "",
        "approved_tree": approved_tree or "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve AcceptBox PR inputs for CI")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--policy", default="auto")
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--repro", default="")
    parser.add_argument("--repro-strategy", default="first-commit")
    parser.add_argument("--inv", default="")
    parser.add_argument("--ebt-sha", default="")
    parser.add_argument("--approved-tree", default="")
    parser.add_argument("--pr-labels-json", default="[]")
    parser.add_argument("--pr-body-file", type=Path, default=None)
    parser.add_argument("--acceptbox-bin", default="acceptbox")
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        raw_labels = json.loads(args.pr_labels_json or "[]")
        if not isinstance(raw_labels, list):
            raise ResolveError("--pr-labels-json must be a JSON array")
        labels = [
            item["name"] if isinstance(item, dict) and isinstance(item.get("name"), str)
            else str(item)
            for item in raw_labels
        ]
        pr_body = args.pr_body_file.read_text(encoding="utf-8") if args.pr_body_file and args.pr_body_file.is_file() else ""
        plan = resolve_plan(
            repo=args.repo,
            base=args.base,
            head=args.head,
            base_ref=args.base_ref,
            policy_input=args.policy,
            mode_input=args.mode,
            repro_input=args.repro,
            repro_strategy=args.repro_strategy,
            inv_input=args.inv,
            ebt_sha_input=args.ebt_sha,
            approved_tree_input=args.approved_tree,
            pr_labels=labels,
            pr_body=pr_body,
            acceptbox_bin=args.acceptbox_bin,
        )
    except (ResolveError, OSError, json.JSONDecodeError) as error:
        print(f"resolve_pr: {error}", file=sys.stderr)
        return 2

    rendered = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.output_json:
        args.output_json.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
