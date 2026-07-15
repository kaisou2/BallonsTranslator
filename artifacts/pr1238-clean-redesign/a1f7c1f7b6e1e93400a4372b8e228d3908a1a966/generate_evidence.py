"""Generate recorded local automated evidence for the a1f7c1f implementation.

This runner lives in the final documentation checkout, but executes the target
and baseline tests in separate, exact, clean worktrees.  It records automated
checks only.  Human GUI acceptance and independent reviewer approval are
tracked separately in manual-checklist.md and the artifact README.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


TARGET_SHA = "a1f7c1f7b6e1e93400a4372b8e228d3908a1a966"
BASELINE_SHA = "6155f9b303033b24f57a2c025d2edbfed3eb847f"

ARTIFACT_DIR = Path(__file__).resolve().parent
ARTIFACT_REPO_DIR = ARTIFACT_DIR.parents[2]
RESULTS_DIR = ARTIFACT_DIR / "test-results"

DEFAULT_PYTHON = Path(os.environ.get("PR1238_PYTHON", r"F:\python\python.exe"))
DEFAULT_DEPENDENCY_OVERLAY = Path(
    os.environ.get("PR1238_DEPENDENCY_OVERLAY", r"F:\ballon\.pr1238-test-deps")
)
DEFAULT_BASELINE_DIR = Path(
    os.environ.get(
        "PR1238_BASELINE_DIR", r"F:\ballon\BallonsTranslator-baseline-6155f9b"
    )
)
DEFAULT_TARGET_DIR = (
    Path(os.environ["PR1238_REPO_DIR"])
    if "PR1238_REPO_DIR" in os.environ
    else None
)

FOCUSED_TESTS = (
    "tests/test_text_transform_compensation.py",
    "tests/test_text_transform_itemchange_safety.py",
    "tests/test_text_transform_rotation_properties.py",
    "tests/test_text_transform_translation_pipeline.py",
    "tests/test_textitem_set_size_transaction.py",
)
DOCTEST_MODULES = (
    "ballontranslator/utils/fontformat.py",
    "ballontranslator/ui/text_transform.py",
    "ballontranslator/ui/mainwindow.py",
)
EXPECTED_PACKAGE_VERSIONS: dict[str, str | None] = {
    "pytest": "9.1.1",
    # pytest 9 supplies the subtests support used by this run; no separate
    # pytest-subtests distribution was installed in the recorded overlay.
    "pytest-subtests": None,
    "qtpy": "2.4.3",
    "PyQt5": "5.15.11",
    "PyQt6": "6.6.1",
    "PySide6": "6.8.2.1",
}
PACKAGE_NAMES = tuple(EXPECTED_PACKAGE_VERSIONS)


@dataclass(frozen=True)
class ReplayConfig:
    python: Path
    dependency_overlay: Path
    target_worktree: Path
    baseline_worktree: Path
    preflight_only: bool = False


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def parse_args(arguments: list[str] | None = None) -> ReplayConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Replay PR #1238 evidence using separate exact target and baseline "
            "worktrees. Existing evidence is replaced only after every check passes."
        )
    )
    parser.add_argument(
        "--target-worktree",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help=(
            "clean detached worktree at the implementation SHA; alternatively "
            "set PR1238_REPO_DIR"
        ),
    )
    parser.add_argument(
        "--baseline-worktree",
        type=Path,
        default=DEFAULT_BASELINE_DIR,
        help="clean detached baseline worktree; defaults to PR1238_BASELINE_DIR",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=DEFAULT_PYTHON,
        help="test interpreter; defaults to PR1238_PYTHON",
    )
    parser.add_argument(
        "--dependency-overlay",
        type=Path,
        default=DEFAULT_DEPENDENCY_OVERLAY,
        help="dependency overlay; defaults to PR1238_DEPENDENCY_OVERLAY",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate every input without creating or replacing test-results",
    )
    namespace = parser.parse_args(arguments)
    if namespace.target_worktree is None:
        parser.error(
            "--target-worktree or PR1238_REPO_DIR is required; the implementation "
            "worktree must be separate from the artifact checkout"
        )
    return ReplayConfig(
        python=resolved(namespace.python),
        dependency_overlay=resolved(namespace.dependency_overlay),
        target_worktree=resolved(namespace.target_worktree),
        baseline_worktree=resolved(namespace.baseline_worktree),
        preflight_only=namespace.preflight_only,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git(
    *arguments: str, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={cwd.as_posix()}",
            "-C",
            str(cwd),
            *arguments,
        ],
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def git_lines(*arguments: str, cwd: Path) -> list[str]:
    return git(*arguments, cwd=cwd).stdout.splitlines()


def common_environment(config: ReplayConfig) -> dict[str, str]:
    return {
        "PYTHONPATH": str(config.dependency_overlay),
        "PYTHONDONTWRITEBYTECODE": "1",
        "QT_QPA_PLATFORM": "offscreen",
    }


def overlay_package_versions(
    python: Path, environment: dict[str, str]
) -> dict[str, str | None]:
    code = (
        "import importlib.metadata, json\n"
        f"names = {PACKAGE_NAMES!r}\n"
        "versions = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        versions[name] = importlib.metadata.version(name)\n"
        "    except importlib.metadata.PackageNotFoundError:\n"
        "        versions[name] = None\n"
        "print(json.dumps(versions, sort_keys=True))\n"
    )
    process_env = os.environ.copy()
    process_env.update(environment)
    result = subprocess.run(
        [str(python), "-c", code],
        env=process_env,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return json.loads(result.stdout)


def python_version(python: Path, environment: dict[str, str]) -> str:
    process_env = os.environ.copy()
    process_env.update(environment)
    result = subprocess.run(
        [str(python), "--version"],
        env=process_env,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return (result.stdout or result.stderr).strip()


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")


def require_directory(path: Path, label: str) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"{label} does not exist: {path}")


def preflight(config: ReplayConfig) -> dict[str, object]:
    """Validate every external input before any results directory is touched."""

    require_file(config.python, "Python interpreter")
    require_directory(config.dependency_overlay, "dependency overlay")
    require_directory(config.target_worktree, "target worktree")
    require_directory(config.baseline_worktree, "baseline worktree")
    require_directory(ARTIFACT_REPO_DIR, "artifact checkout")
    if config.target_worktree == ARTIFACT_REPO_DIR:
        raise RuntimeError(
            "target worktree must be separate from the final artifact checkout: "
            f"{config.target_worktree}"
        )
    if config.baseline_worktree in (ARTIFACT_REPO_DIR, config.target_worktree):
        raise RuntimeError(
            "baseline worktree must be separate from both artifact and target "
            f"checkouts: {config.baseline_worktree}"
        )

    environment = common_environment(config)
    checked_python_version = python_version(config.python, environment)
    packages = overlay_package_versions(config.python, environment)
    if packages != EXPECTED_PACKAGE_VERSIONS:
        raise RuntimeError(
            "dependency environment does not match the recorded package versions: "
            f"actual={packages}, expected={EXPECTED_PACKAGE_VERSIONS}"
        )

    target_root = resolved(
        Path(git("rev-parse", "--show-toplevel", cwd=config.target_worktree).stdout.strip())
    )
    baseline_root = resolved(
        Path(
            git(
                "rev-parse", "--show-toplevel", cwd=config.baseline_worktree
            ).stdout.strip()
        )
    )
    if target_root != config.target_worktree:
        raise RuntimeError(
            f"target path must be the worktree root: {config.target_worktree} != {target_root}"
        )
    if baseline_root != config.baseline_worktree:
        raise RuntimeError(
            "baseline path must be the worktree root: "
            f"{config.baseline_worktree} != {baseline_root}"
        )

    target_head = git("rev-parse", "HEAD", cwd=config.target_worktree).stdout.strip()
    target_status = git_lines(
        "status", "--short", "--untracked-files=all", cwd=config.target_worktree
    )
    if target_head != TARGET_SHA or target_status:
        raise RuntimeError(
            "target worktree must be the exact clean implementation checkout: "
            f"head={target_head}, expected={TARGET_SHA}, status={target_status}"
        )

    baseline_head = git(
        "rev-parse", "HEAD", cwd=config.baseline_worktree
    ).stdout.strip()
    baseline_status = git_lines(
        "status", "--short", "--untracked-files=all", cwd=config.baseline_worktree
    )
    if baseline_head != BASELINE_SHA or baseline_status:
        raise RuntimeError(
            "baseline worktree must be the exact clean baseline checkout: "
            f"head={baseline_head}, expected={BASELINE_SHA}, status={baseline_status}"
        )

    # Confirm the comparison commit and every input path before staging output.
    git("cat-file", "-e", f"{BASELINE_SHA}^{{commit}}", cwd=config.target_worktree)
    required_target_files = (*FOCUSED_TESTS, *DOCTEST_MODULES)
    missing_target_files = [
        name for name in required_target_files if not (config.target_worktree / name).is_file()
    ]
    if not (config.target_worktree / "tests").is_dir():
        missing_target_files.append("tests/")
    if missing_target_files:
        raise FileNotFoundError(
            "target worktree is missing replay inputs: " + ", ".join(missing_target_files)
        )

    changed_production = [
        name
        for name in git_lines(
            "diff",
            "--name-only",
            f"{BASELINE_SHA}...{TARGET_SHA}",
            "--",
            "ballontranslator",
            cwd=config.target_worktree,
        )
        if name.endswith(".py")
    ]
    missing_changed_files = [
        name for name in changed_production if not (config.target_worktree / name).is_file()
    ]
    if missing_changed_files:
        raise FileNotFoundError(
            "changed production files are absent from target: "
            + ", ".join(missing_changed_files)
        )

    artifact_head = git("rev-parse", "HEAD", cwd=ARTIFACT_REPO_DIR).stdout.strip()
    artifact_status = git_lines(
        "status", "--short", "--untracked-files=all", cwd=ARTIFACT_REPO_DIR
    )
    if artifact_status:
        raise RuntimeError(
            "artifact checkout must be clean so the replay runner and recorded "
            "documentation are attributable to its HEAD: "
            f"head={artifact_head}, status={artifact_status}"
        )
    return {
        "python_version": checked_python_version,
        "packages": packages,
        "target_head": target_head,
        "target_branch": git(
            "branch", "--show-current", cwd=config.target_worktree
        ).stdout.strip(),
        "target_status": target_status,
        "baseline_head": baseline_head,
        "baseline_status": baseline_status,
        "artifact_checkout_head": artifact_head,
        "artifact_checkout_status": artifact_status,
        "merge_base": git(
            "merge-base", BASELINE_SHA, TARGET_SHA, cwd=config.target_worktree
        ).stdout.strip(),
        "changed_production_python": changed_production,
        "commit_subjects": git_lines(
            "log",
            "--format=%H %s",
            f"{BASELINE_SHA}..{TARGET_SHA}",
            cwd=config.target_worktree,
        ),
    }


def junit_summary(path: Path) -> dict[str, object]:
    root = ElementTree.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failure_ids: list[str] = []
    failures = errors = skipped = 0
    for case in cases:
        identity = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        if case.find("failure") is not None:
            failures += 1
            failure_ids.append(identity)
        elif case.find("error") is not None:
            errors += 1
            failure_ids.append(identity)
        elif case.find("skipped") is not None:
            skipped += 1
    total = len(cases)
    return {
        "total": total,
        "passed": total - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "failure_ids": sorted(failure_ids),
    }


def run_recorded(
    *,
    label: str,
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    expected_exit_codes: tuple[int, ...],
    log_name: str,
    results_dir: Path,
) -> dict[str, object]:
    process_env = os.environ.copy()
    process_env.update(environment)
    result = subprocess.run(
        command,
        cwd=str(cwd),
        env=process_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    log_path = results_dir / log_name
    log_path.write_text(
        "\n".join(
            (
                f"label={label}",
                f"cwd={cwd}",
                "environment_overrides="
                + json.dumps(environment, ensure_ascii=False, sort_keys=True),
                "command=" + json.dumps(command, ensure_ascii=False),
                f"exit_code={result.returncode}",
                "expected_exit_codes=" + json.dumps(expected_exit_codes),
                "--- stdout ---",
                result.stdout,
                "--- stderr ---",
                result.stderr,
            )
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )
    if result.returncode not in expected_exit_codes:
        raise RuntimeError(
            f"{label} exited {result.returncode}; expected {expected_exit_codes}"
        )
    pytest_summaries = [
        line.strip()
        for line in result.stdout.splitlines()
        if re.search(r"\b\d+ passed\b", line)
    ]
    return {
        "label": label,
        "cwd": str(cwd),
        "environment_overrides": environment,
        "command": command,
        "exit_code": result.returncode,
        "expected_exit_codes": list(expected_exit_codes),
        "log": f"test-results/{log_name}",
        "pytest_summary": pytest_summaries[-1] if pytest_summaries else None,
    }


def pytest_command(
    config: ReplayConfig, results_dir: Path, *paths: str, junit_name: str
) -> list[str]:
    return [
        str(config.python),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        *paths,
        "-q",
        f"--junitxml={results_dir / junit_name}",
    ]


def inventory_key(path: Path, generated_results_dir: Path) -> str:
    try:
        relative = path.relative_to(generated_results_dir)
    except ValueError:
        relative = path.relative_to(ARTIFACT_DIR)
        return relative.as_posix()
    return (Path("test-results") / relative).as_posix()


def generate_bundle(
    config: ReplayConfig, preflight_state: dict[str, object], results_dir: Path
) -> None:
    common_env = common_environment(config)
    records: list[dict[str, object]] = []
    junit_files: list[Path] = []

    for qt_api in ("pyqt5", "pyqt6", "pyside6"):
        junit_name = f"focused-{qt_api}.xml"
        junit_files.append(results_dir / junit_name)
        records.append(
            run_recorded(
                label=f"focused-{qt_api}",
                command=pytest_command(
                    config, results_dir, *FOCUSED_TESTS, junit_name=junit_name
                ),
                cwd=config.target_worktree,
                environment={**common_env, "QT_API": qt_api},
                expected_exit_codes=(0,),
                log_name=f"focused-{qt_api}.log",
                results_dir=results_dir,
            )
        )

    text_tests = sorted(
        {
            *(
                path.relative_to(config.target_worktree).as_posix()
                for path in (config.target_worktree / "tests").glob(
                    "test_text_transform*.py"
                )
            ),
            *(
                path.relative_to(config.target_worktree).as_posix()
                for path in (config.target_worktree / "tests").glob("test_textitem*.py")
            ),
        }
    )
    if not text_tests:
        raise RuntimeError("no text transform/item tests were discovered")
    text_junit = results_dir / "text-suite-pyqt6.xml"
    junit_files.append(text_junit)
    records.append(
        run_recorded(
            label="text-suite-pyqt6",
            command=pytest_command(
                config, results_dir, *text_tests, junit_name=text_junit.name
            ),
            cwd=config.target_worktree,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(0,),
            log_name="text-suite-pyqt6.log",
            results_dir=results_dir,
        )
    )

    feature_junit = results_dir / "full-feature-pyqt6.xml"
    baseline_junit = results_dir / "full-upstream-pyqt6.xml"
    junit_files.extend((feature_junit, baseline_junit))
    records.append(
        run_recorded(
            label="full-feature-pyqt6",
            command=pytest_command(
                config, results_dir, "tests", junit_name=feature_junit.name
            ),
            cwd=config.target_worktree,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(1,),
            log_name="full-feature-pyqt6.log",
            results_dir=results_dir,
        )
    )
    records.append(
        run_recorded(
            label="full-upstream-pyqt6",
            command=pytest_command(
                config, results_dir, "tests", junit_name=baseline_junit.name
            ),
            cwd=config.baseline_worktree,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(1,),
            log_name="full-upstream-pyqt6.log",
            results_dir=results_dir,
        )
    )

    changed_production = list(preflight_state["changed_production_python"])
    records.append(
        run_recorded(
            label="changed-production-syntax-compile",
            command=[
                str(config.python),
                "-c",
                (
                    "import pathlib, sys\n"
                    "for name in sys.argv[1:]:\n"
                    "    path = pathlib.Path(name)\n"
                    "    compile(path.read_bytes(), str(path), 'exec')\n"
                ),
                *changed_production,
            ],
            cwd=config.target_worktree,
            environment=common_env,
            expected_exit_codes=(0,),
            log_name="static-syntax-compile.log",
            results_dir=results_dir,
        )
    )
    records.append(
        run_recorded(
            label="relevant-doctests",
            command=[
                str(config.python),
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--doctest-modules",
                *DOCTEST_MODULES,
                "-q",
            ],
            cwd=config.target_worktree,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(0,),
            log_name="relevant-doctests.log",
            results_dir=results_dir,
        )
    )

    junit = {path.name: junit_summary(path) for path in junit_files}
    feature_failures = junit[feature_junit.name]["failure_ids"]
    baseline_failures = junit[baseline_junit.name]["failure_ids"]
    full_run_shape_is_expected = all(
        junit[name]["failures"] == 7 and junit[name]["errors"] == 0
        for name in (feature_junit.name, baseline_junit.name)
    )
    if feature_failures != baseline_failures or not full_run_shape_is_expected:
        raise AssertionError(
            "feature/upstream failure identities differ or are not seven failures "
            "and zero errors in both runs"
        )

    final_target_status = git_lines(
        "status", "--short", "--untracked-files=all", cwd=config.target_worktree
    )
    final_baseline_status = git_lines(
        "status", "--short", "--untracked-files=all", cwd=config.baseline_worktree
    )
    if final_target_status or final_baseline_status:
        raise RuntimeError(
            "a replay worktree changed while tests ran: "
            f"target={final_target_status}, baseline={final_baseline_status}"
        )

    environment = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python_executable": str(config.python),
        "python_version": preflight_state["python_version"],
        "dependency_overlay": str(config.dependency_overlay),
        "packages": preflight_state["packages"],
    }
    write_json(results_dir / "environment.json", environment)

    provenance = {
        "target_sha": TARGET_SHA,
        "target_worktree": str(config.target_worktree),
        "target_worktree_head": preflight_state["target_head"],
        "target_worktree_branch": preflight_state["target_branch"],
        "target_worktree_status_at_preflight": preflight_state["target_status"],
        "target_worktree_status_after_tests": final_target_status,
        "baseline_sha": BASELINE_SHA,
        "baseline_worktree": str(config.baseline_worktree),
        "baseline_worktree_head": preflight_state["baseline_head"],
        "baseline_worktree_status_at_preflight": preflight_state["baseline_status"],
        "baseline_worktree_status_after_tests": final_baseline_status,
        "artifact_checkout": str(ARTIFACT_REPO_DIR),
        "artifact_checkout_head_at_preflight": preflight_state[
            "artifact_checkout_head"
        ],
        "artifact_checkout_status_at_preflight": preflight_state[
            "artifact_checkout_status"
        ],
        "merge_base": preflight_state["merge_base"],
        "changed_production_python": changed_production,
        "commit_subjects": preflight_state["commit_subjects"],
    }
    write_json(results_dir / "git-provenance.json", provenance)

    summary_lines = [
        "# Automated verification summary",
        "",
        f"Implementation target: `{TARGET_SHA}`",
        "",
        "This bundle records automated checks only. Manual GUI acceptance and independent reviewer approval remain separate and pending where stated.",
        "",
        "The table below reports JUnit testcase outcomes. Pytest's console summaries are recorded separately because pytest-subtests can count a parent test differently in console and JUnit output.",
        "",
        "| Run | Passed | Failed | Errors | Skipped |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in (
        "focused-pyqt5.xml",
        "focused-pyqt6.xml",
        "focused-pyside6.xml",
        "text-suite-pyqt6.xml",
        "full-feature-pyqt6.xml",
        "full-upstream-pyqt6.xml",
    ):
        item = junit[name]
        summary_lines.append(
            f"| `{name}` | {item['passed']} | {item['failures']} | {item['errors']} | {item['skipped']} |"
        )
    summary_lines.extend(
        (
            "",
            "## Pytest console summaries",
            "",
            *(
                f"- `{record['label']}`: {record['pytest_summary']}"
                for record in records
                if record["pytest_summary"] is not None
            ),
            "",
            "The feature and exact-upstream full runs have the same seven failure identities. They are pre-existing baseline failures, not new failures introduced by the target implementation.",
            "",
            "Changed production modules passed an in-memory Python syntax compilation check; relevant FontFormat and text-transform doctests passed. Raw command logs, JUnit XML, environment details, and Git provenance are stored beside this summary.",
            "",
            "Independent reviewer approval: **Pending**",
            "",
        )
    )
    (results_dir / "verification-summary.md").write_text(
        "\n".join(summary_lines), encoding="utf-8"
    )

    inventory_paths = [
        path
        for path in results_dir.rglob("*")
        if path.is_file() and path.name != "generation-report.json"
    ]
    for extra in (ARTIFACT_DIR / "manual-checklist.md", Path(__file__)):
        if extra.is_file():
            inventory_paths.append(extra)
    report = {
        "target_sha": TARGET_SHA,
        "baseline_sha": BASELINE_SHA,
        "records": records,
        "junit": junit,
        "feature_and_upstream_failure_ids_equal": feature_failures
        == baseline_failures,
        "failure_ids": feature_failures,
        "file_inventory_semantics": {
            "kind": "generation-time-worktree-raw-bytes",
            "hash_algorithm": "sha256",
            "git_blob_integrity_evidence": False,
            "description": (
                "Hashes and sizes describe the raw files read from the generation "
                "worktrees. Checkout line-ending conversion, including CRLF on "
                "Windows, can make them differ from committed Git blob bytes."
            ),
        },
        "files": {
            inventory_key(path, results_dir): {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in sorted(set(inventory_paths))
        },
    }
    write_json(results_dir / "generation-report.json", report)


def transactional_replace_directory(staged: Path, destination: Path) -> None:
    """Swap in a complete directory and restore the old one if the swap fails."""

    if not staged.is_dir():
        raise FileNotFoundError(f"staged results directory is absent: {staged}")
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(destination)

    backup = destination.with_name(
        f"{destination.name}.backup-{uuid.uuid4().hex}"
    )
    moved_existing = False
    try:
        if destination.exists():
            os.replace(destination, backup)
            moved_existing = True
        os.replace(staged, destination)
    except BaseException:
        if moved_existing and not destination.exists() and backup.exists():
            os.replace(backup, destination)
        raise
    else:
        if backup.exists():
            try:
                shutil.rmtree(backup)
            except OSError as error:
                warnings.warn(
                    f"new results are installed but old backup cleanup failed: {error}",
                    RuntimeWarning,
                    stacklevel=2,
                )


def execute(config: ReplayConfig) -> None:
    # This must remain the first potentially failing phase. It performs no writes
    # to test-results, so every preflight failure preserves the recorded bundle.
    preflight_state = preflight(config)
    print(
        "Preflight passed: "
        f"target={config.target_worktree} ({TARGET_SHA}), "
        f"baseline={config.baseline_worktree} ({BASELINE_SHA})"
    )
    if config.preflight_only:
        print("Preflight-only mode: test-results was not created or replaced.")
        return

    staged_results = Path(
        tempfile.mkdtemp(prefix=f"{RESULTS_DIR.name}.tmp-", dir=ARTIFACT_DIR)
    )
    try:
        generate_bundle(config, preflight_state, staged_results)
        transactional_replace_directory(staged_results, RESULTS_DIR)
    finally:
        if staged_results.exists():
            try:
                shutil.rmtree(staged_results)
            except OSError as error:
                warnings.warn(
                    f"failed to clean temporary results directory: {error}",
                    RuntimeWarning,
                    stacklevel=2,
                )
    print(f"Evidence replay passed; replaced {RESULTS_DIR}")


def main(arguments: list[str] | None = None) -> None:
    execute(parse_args(arguments))


if __name__ == "__main__":
    main()
