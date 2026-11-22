#!/usr/bin/env python3
"""Verification of an acceptbox (or witnessbox) installation against release.json.

`wheel` checks release.json (result PASS, version, wheel SHA-256) and the wheel file itself.
`installation` compares the JSON of `<tool> --installation` with `release["installation"]`.

Exit code 0 means a match, 1 a mismatch or invalid input. Uses only Python stdlib
and runs before installation with `python -I`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path}: could not read JSON: {error}") from error


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError(f"{path}: could not read file: {error}") from error


def wheel_version(path: Path) -> str:
    """Extract version from <distribution>-<version>-<python>-<abi>-<platform>.whl."""
    name = path.name
    if not name.endswith(".whl"):
        raise ValueError(f"{path}: expected a .whl file")
    parts = name[:-len(".whl")].split("-")
    if len(parts) < 5:
        raise ValueError(
            f"{path}: wheel name does not match distribution-version-python-abi-platform.whl"
        )
    return parts[1]


def verify_wheel(release_path: Path, wheel_path: Path) -> int:
    try:
        release = load_json(release_path)
        if not isinstance(release, dict):
            raise ValueError(f"{release_path}: expected a JSON object")
        result = release.get("result")
        if result != "PASS":
            return fail(f"{release_path}: result={result!r}, expected PASS")
        expected_version = release.get("version")
        if not isinstance(expected_version, str) or not expected_version:
            return fail(f"{release_path}: missing string version")
        actual_version = wheel_version(wheel_path)
        if actual_version != expected_version:
            return fail(
                f"wheel: version {actual_version} does not match release.json: {expected_version}"
            )
        wheel = release.get("wheel")
        expected_sha = wheel.get("sha256") if isinstance(wheel, dict) else None
        if not isinstance(expected_sha, str) or not expected_sha:
            return fail(f"{release_path}: missing wheel.sha256")
        actual_sha = sha256(wheel_path)
    except ValueError as error:
        return fail(str(error))
    if actual_sha != expected_sha:
        return fail(f"wheel: SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")
    print(f"wheel {wheel_path.name}: PASS (version {actual_version}, sha256 {actual_sha})")
    return 0


def verify_installation(release_path: Path, installation_path: Path) -> int:
    try:
        release = load_json(release_path)
        installation = load_json(installation_path)
    except ValueError as error:
        return fail(str(error))
    if not isinstance(release, dict) or not isinstance(release.get("installation"), dict):
        return fail(f"{release_path}: missing installation object")
    if not isinstance(installation, dict):
        return fail(f"{installation_path}: expected a JSON object")

    expected = release["installation"]
    dist = expected.get("distribution")
    differences: list[str] = []

    for field in ("distribution", "version"):
        exp_val = expected.get(field)
        act_val = installation.get(field)
        if not isinstance(exp_val, str) or not exp_val:
            differences.append(f"{field}: missing in release.json")
        elif exp_val != act_val:
            differences.append(f"{field}: expected {exp_val!r}, got {act_val!r}")

    if dist == "acceptbox":
        for int_field in ("format", "report_format"):
            exp_val = expected.get(int_field)
            act_val = installation.get(int_field)
            if type(exp_val) is not int or exp_val != act_val:
                differences.append(f"{int_field}: expected {exp_val!r}, got {act_val!r}")
        git_block = installation.get("git")
        if not isinstance(git_block, dict) or git_block.get("status") != "ok":
            differences.append(f"git.status: expected 'ok', got {git_block!r}")
    elif dist == "witnessbox":
        for field in ("gate_sha256", "checker_sha256"):
            exp_val = expected.get(field)
            act_val = installation.get(field)
            if not isinstance(exp_val, str) or not exp_val:
                differences.append(f"{field}: missing in release.json")
            elif exp_val != act_val:
                differences.append(f"{field}: expected {exp_val!r}, got {act_val!r}")
    else:
        differences.append(f"unsupported distribution in release.json: {dist!r}")

    if differences:
        print("installation: mismatches against release.json:", file=sys.stderr)
        for diff in differences:
            print(f"  {diff}", file=sys.stderr)
        return 1
    print(f"installation ({dist}): PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify acceptbox/witnessbox wheel and --installation against release.json"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    wheel = sub.add_parser("wheel", help="verify wheel SHA-256 against release.json")
    wheel.add_argument("release", type=Path)
    wheel.add_argument("wheel", type=Path)
    inst = sub.add_parser("installation", help="verify --installation JSON against release.json")
    inst.add_argument("release", type=Path)
    inst.add_argument("installation", type=Path)
    args = parser.parse_args(argv)
    if args.command == "wheel":
        return verify_wheel(args.release, args.wheel)
    return verify_installation(args.release, args.installation)


if __name__ == "__main__":
    sys.exit(main())
