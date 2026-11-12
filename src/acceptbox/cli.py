"""Командная строка AcceptBox."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from importlib.metadata import version

from .pin import tree_sha256


WITNESSBOX_RANGE = ">=0.9.0,<0.10"


def _installation(acceptbox_version: str) -> int:
    executable = shutil.which("witnessbox")
    found_version = None
    status = "missing"
    if executable:
        status = "error"
        try:
            result = subprocess.run(
                [executable, "--version"], capture_output=True, encoding="utf-8",
                errors="replace", check=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        else:
            name, _, candidate = result.stdout.strip().partition(" ")
            if name == "witnessbox" and candidate:
                found_version = candidate
                status = "incompatible"
                if re.fullmatch(r"0\.9\.[0-9]+(?:\.post[0-9]+)?(?:\+[a-z0-9.]+)?", candidate):
                    status = "compatible"

    print(json.dumps({
        "distribution": "acceptbox",
        "version": acceptbox_version,
        "witnessbox": {
            "path": executable,
            "version": found_version,
            "supported": WITNESSBOX_RANGE,
            "status": status,
        },
    }, ensure_ascii=False, indent=2))
    return 0 if status == "compatible" else 2


def main(argv: list[str] | None = None) -> int:
    acceptbox_version = version("acceptbox")
    parser = argparse.ArgumentParser(
        prog="acceptbox", description="Подготовка входов для приёмки коммитов."
    )
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--version", action="version", version=f"%(prog)s {acceptbox_version}")
    options.add_argument("--installation", action="store_true", help="проверить версию WitnessBox")
    commands = parser.add_subparsers(dest="command")
    pin = commands.add_parser("pin", help="вычислить SHA-256 дерева ebt/ из Git-коммита")
    pin.add_argument("--ebt-repo", required=True, help="путь или URL репозитория")
    pin.add_argument("--ebt-sha", required=True, help="полный SHA коммита")
    args = parser.parse_args(argv)

    if args.installation:
        if args.command:
            parser.error("--installation используется отдельно")
        return _installation(acceptbox_version)
    if args.command == "pin":
        try:
            print(tree_sha256(args.ebt_repo, args.ebt_sha))
        except subprocess.CalledProcessError as error:
            print(f"acceptbox: {error.stderr.decode(errors='replace').strip()}", file=sys.stderr)
            return 2
        except (OSError, ValueError) as error:
            print(f"acceptbox: {error}", file=sys.stderr)
            return 2
        return 0

    parser.print_help()
    return 0
