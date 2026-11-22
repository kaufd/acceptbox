"""Distribution release check for AcceptBox (modeled on WitnessBox release_check.py).

1. Checks pyproject.toml version (optionally against --expected-version) and
   src/acceptbox/__init__.py fallback __version__.
2. Verifies via AST that src/acceptbox/ uses only the Python standard library
   and never imports witnessbox.
3. Builds the sdist and a wheel from it (not from a potentially dirty working tree).
4. Unpacks the sdist, runs the full test suite from the unpacked sdist (zero skips),
   rebuilds a wheel from the sdist, and checks that the wheel payload is byte-identical.
5. Installs the release wheel into a clean venv without an index or PYTHONPATH,
   checks --version, --installation (both with and without witnessbox on PATH),
   isolated import, and an end-to-end `acceptbox accept` smoke run.
6. Writes build/release/<version>/release.json and SHA256SUMS.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


def run(cmd: list[str | Path], cwd: Path | None = None,
        env: dict[str, str] | None = None) -> None:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=ENV if env is None else env, check=True)


def single(paths) -> Path:
    found = list(paths)
    if len(found) != 1:
        raise SystemExit(f"expected one file, found: {found}")
    return found[0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(wheel: Path) -> dict[str, bytes]:
    """Package files excluding .dist-info: build metadata may differ, code may not."""
    with zipfile.ZipFile(wheel) as archive:
        return {name: archive.read(name) for name in archive.namelist()
                if ".dist-info/" not in name}


def check_fallback_version(init_file: Path, expected: str) -> bool:
    tree = ast.parse(init_file.read_text(encoding="utf-8"), filename=str(init_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id == "__version__"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    return node.value.value == expected
    return False


def check_stdlib_only(pkg_dir: Path) -> list[str]:
    """Ensure every runtime module imports only stdlib or acceptbox itself (L14)."""
    allowed = set(sys.stdlib_module_names) | {"acceptbox"}
    violations: list[str] = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top not in allowed:
                        violations.append(f"{py_file.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    top = node.module.split(".", 1)[0]
                    if top not in allowed:
                        violations.append(f"{py_file.name}: from {node.module} import ...")
    return violations


def run_sdist_tests(tree: Path) -> tuple[bool, str]:
    runner = (
        "import sys, unittest\n"
        "suite = unittest.TestLoader().discover('tests', pattern='test_*.py')\n"
        "res = unittest.TextTestRunner(verbosity=2).run(suite)\n"
        "print(f'SDIST_TESTS_SUMMARY={res.testsRun}:{len(res.skipped)}')\n"
        "if res.testsRun == 0 or res.skipped or not res.wasSuccessful():\n"
        "    sys.exit(1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner],
        cwd=tree, env=ENV, capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    summary = f"exit {proc.returncode}"
    for line in proc.stdout.splitlines():
        if line.startswith("SDIST_TESTS_SUMMARY="):
            ran, skipped = line.split("=", 1)[1].split(":", 1)
            summary = f"{ran} tests, {skipped} skipped"
    return proc.returncode == 0, summary


def check_installation_without_witnessbox(acceptbox_bin: Path, version: str) -> bool:
    """Verify acceptbox --installation exits 0 with witnessbox missing (L20)."""
    git_bin = shutil.which("git")
    if not git_bin:
        return False
    with tempfile.TemporaryDirectory(prefix="acceptbox-nowb-") as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(git_bin)
        env = dict(ENV)
        env["PATH"] = str(bin_dir)
        proc = subprocess.run(
            [str(acceptbox_bin), "--installation"],
            env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout + proc.stderr)
            return False
        info = json.loads(proc.stdout)
        return (
            info.get("format") == 1
            and info.get("report_format") == 1
            and info.get("distribution") == "acceptbox"
            and info.get("version") == version
            and info.get("git", {}).get("status") == "ok"
            and info.get("witnessbox", {}).get("status") == "missing"
        )


def run_wheel_smoke(acceptbox_bin: Path, version: str) -> bool:
    """End-to-end command-mode smoke run using the installed console script."""
    clean_git = {k: v for k, v in ENV.items() if not k.startswith("GIT_")}

    def git(cwd: Path, *args: str) -> str:
        res = subprocess.run(
            ["git", "-C", str(cwd), *args],
            env=clean_git, capture_output=True, text=True, check=True,
        )
        return res.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="acceptbox-smoke-") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "release@example.com")
        git(repo, "config", "user.name", "Release Check")

        (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_app.py").write_text(
            "import unittest\nfrom app import add\n"
            "class T(unittest.TestCase):\n"
            "    def test_zero(self): self.assertEqual(add(0, 0), 0)\n",
            encoding="utf-8",
        )
        (repo / ".acceptbox.toml").write_text(
            'format = 1\nmode = "command"\n\n[command]\ntests = ["tests/"]\n'
            f'argv = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n',
            encoding="utf-8",
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")

        (repo / "tests" / "test_app.py").write_text(
            "import unittest\nfrom app import add\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self): self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "repro")
        repro = git(repo, "rev-parse", "HEAD")

        (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fix")
        head = git(repo, "rev-parse", "HEAD")

        out_dir = Path(tmp) / "out"
        proc = subprocess.run(
            [str(acceptbox_bin), "accept", "--repo", str(repo),
             "--base", base, "--repro", repro, "--head", head, "--out", str(out_dir)],
            env=ENV, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout + proc.stderr)
            return False
        report = json.loads((out_dir / "accept.json").read_text(encoding="utf-8"))
        return (
            report.get("format") == 1
            and report.get("result") == "ACCEPTED"
            and report.get("acceptbox_version") == version
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify an AcceptBox release.")
    parser.add_argument("--expected-version", default=None,
                        help="Verify pyproject.toml matches this exact version (e.g. from tag)")
    args = parser.parse_args()

    uv = shutil.which("uv") or sys.exit("uv is required")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if args.expected_version is not None and version != args.expected_version:
        raise SystemExit(
            f"version mismatch: pyproject.toml has {version!r}, expected {args.expected_version!r}"
        )

    fallback_ok = check_fallback_version(ROOT / "src/acceptbox/__init__.py", version)
    stdlib_violations = check_stdlib_only(ROOT / "src/acceptbox")

    out = ROOT / "build/release" / version
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    # Without --wheel, uv builds the sdist first and builds the wheel from the sdist.
    run([uv, "build", "--out-dir", out / "dist", ROOT])
    wheel = single((out / "dist").glob("*.whl"))
    sdist = single((out / "dist").glob("*.tar.gz"))

    with tarfile.open(sdist) as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(out / "sdist", filter="data")
        else:
            archive.extractall(out / "sdist")
    tree = single((out / "sdist").iterdir())

    sdist_tests_ok, sdist_tests_summary = run_sdist_tests(tree)

    run([uv, "build", "--wheel", "--out-dir", out / "rebuilt", tree])
    payload_identical = payload(wheel) == payload(single((out / "rebuilt").glob("*.whl")))

    venv = out / "venv"
    run([uv, "venv", "--python", sys.executable, venv])
    run([uv, "pip", "install", "--python", venv / "bin/python", "--no-index", wheel])

    acceptbox_bin = venv / "bin/acceptbox"
    version_out = subprocess.check_output(
        [str(acceptbox_bin), "--version"], env=ENV, text=True
    ).strip()
    version_cli_ok = version_out == f"acceptbox {version}"

    installation = json.loads(
        subprocess.check_output([str(acceptbox_bin), "--installation"], env=ENV, text=True)
    )
    installation_ok = (
        installation.get("format") == 1
        and installation.get("report_format") == 1
        and installation.get("distribution") == "acceptbox"
        and installation.get("version") == version
        and installation.get("git", {}).get("status") == "ok"
    )
    without_wb_ok = check_installation_without_witnessbox(acceptbox_bin, version)

    isolated_import = subprocess.run(
        [str(venv / "bin/python"), "-I", "-c",
         f"import acceptbox; assert acceptbox.__version__ == {version!r}"],
        env=ENV, capture_output=True, text=True,
    )
    if isolated_import.returncode:
        sys.stderr.write(isolated_import.stdout + isolated_import.stderr)
        sys.stderr.flush()

    wheel_smoke_ok = run_wheel_smoke(acceptbox_bin, version)

    wheel_sha = sha256(wheel)
    sdist_sha = sha256(sdist)
    ok = all([
        fallback_ok,
        not stdlib_violations,
        sdist_tests_ok,
        payload_identical,
        version_cli_ok,
        installation_ok,
        without_wb_ok,
        isolated_import.returncode == 0,
        wheel_smoke_ok,
    ])
    release = {
        "version": version,
        "result": "PASS" if ok else "FAIL",
        "wheel": {"file": wheel.name, "sha256": wheel_sha},
        "sdist": {"file": sdist.name, "sha256": sdist_sha},
        "installation": {
            "format": installation.get("format"),
            "report_format": installation.get("report_format"),
            "distribution": installation.get("distribution"),
            "version": installation.get("version"),
            "git_status": installation.get("git", {}).get("status"),
            "witnessbox_status": installation.get("witnessbox", {}).get("status"),
            "witnessbox_version": installation.get("witnessbox", {}).get("version"),
        },
        "fallback_version_matched": fallback_ok,
        "stdlib_only": not stdlib_violations,
        "sdist_tests": sdist_tests_summary,
        "sdist_wheel_payload_identical": payload_identical,
        "installation_without_witnessbox": without_wb_ok,
        "isolated_import": isolated_import.returncode == 0,
        "wheel_smoke": wheel_smoke_ok,
    }
    if stdlib_violations:
        release["stdlib_violations"] = stdlib_violations
    (out / "release.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "SHA256SUMS").write_text(
        f"{wheel_sha}  {wheel.name}\n{sdist_sha}  {sdist.name}\n", encoding="utf-8"
    )
    print(f"wheel from sdist matched release wheel: {payload_identical}")
    print(f"\nrelease-check {version}: {release['result']} -> {out / 'release.json'}")
    print(f"wheel {wheel_sha}\nsdist {sdist_sha}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
