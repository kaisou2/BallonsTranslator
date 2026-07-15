"""Generate recorded local automated evidence for the a1f7c1f implementation.

This script intentionally records automated checks only.  Human GUI acceptance
and independent reviewer approval are tracked separately in manual-checklist.md
and the artifact README.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


TARGET_SHA = "a1f7c1f7b6e1e93400a4372b8e228d3908a1a966"
BASELINE_SHA = "6155f9b303033b24f57a2c025d2edbfed3eb847f"
PYTHON = Path(os.environ.get("PR1238_PYTHON", r"F:\python\python.exe"))
DEPENDENCY_OVERLAY = Path(
    os.environ.get("PR1238_DEPENDENCY_OVERLAY", r"F:\ballon\.pr1238-test-deps")
)
BASELINE_DIR = Path(
    os.environ.get(
        "PR1238_BASELINE_DIR", r"F:\ballon\BallonsTranslator-baseline-6155f9b"
    )
)

ARTIFACT_DIR = Path(__file__).resolve().parent
REPO_DIR = ARTIFACT_DIR.parents[2]
RESULTS_DIR = ARTIFACT_DIR / "test-results"

FOCUSED_TESTS = (
    "tests/test_text_transform_compensation.py",
    "tests/test_text_transform_itemchange_safety.py",
    "tests/test_text_transform_rotation_properties.py",
    "tests/test_text_transform_translation_pipeline.py",
    "tests/test_textitem_set_size_transaction.py",
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


def git(*arguments: str, cwd: Path = REPO_DIR, check: bool = True) -> subprocess.CompletedProcess[str]:
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
    log_path = RESULTS_DIR / log_name
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
        "log": str(log_path.relative_to(ARTIFACT_DIR)).replace("\\", "/"),
        "pytest_summary": pytest_summaries[-1] if pytest_summaries else None,
    }


def pytest_command(*paths: str, junit_name: str) -> list[str]:
    return [
        str(PYTHON),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        *paths,
        "-q",
        f"--junitxml={RESULTS_DIR / junit_name}",
    ]


def overlay_package_versions(environment: dict[str, str]) -> dict[str, str | None]:
    names = ("pytest", "pytest-subtests", "qtpy", "PyQt5", "PyQt6", "PySide6")
    code = (
        "import importlib.metadata, json\n"
        f"names = {names!r}\n"
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
        [str(PYTHON), "-c", code],
        env=process_env,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    if RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not PYTHON.is_file():
        raise FileNotFoundError(PYTHON)
    if not DEPENDENCY_OVERLAY.is_dir():
        raise FileNotFoundError(DEPENDENCY_OVERLAY)
    if not BASELINE_DIR.is_dir():
        raise FileNotFoundError(BASELINE_DIR)

    head = git("rev-parse", "HEAD").stdout.strip()
    if head != TARGET_SHA:
        raise RuntimeError(f"HEAD {head} does not match target {TARGET_SHA}")
    dirty_implementation = git(
        "diff", "--name-only", "HEAD", "--", "ballontranslator", "tests"
    ).stdout.splitlines()
    dirty_staged_implementation = git(
        "diff", "--cached", "--name-only", "HEAD", "--", "ballontranslator", "tests"
    ).stdout.splitlines()
    implementation_status = git(
        "status",
        "--short",
        "--untracked-files=all",
        "--",
        "ballontranslator",
        "tests",
    ).stdout.splitlines()
    if dirty_implementation or dirty_staged_implementation or implementation_status:
        raise RuntimeError(
            "tracked production/tests differ from target: "
            f"{dirty_implementation + dirty_staged_implementation + implementation_status}"
        )
    baseline_head = git("rev-parse", "HEAD", cwd=BASELINE_DIR).stdout.strip()
    baseline_status = git(
        "status",
        "--short",
        "--untracked-files=all",
        "--",
        "ballontranslator",
        "tests",
        cwd=BASELINE_DIR,
    ).stdout.splitlines()
    if baseline_head != BASELINE_SHA or baseline_status:
        raise RuntimeError(
            "baseline worktree does not match the exact clean baseline: "
            f"head={baseline_head}, status={baseline_status}"
        )

    common_env = {
        "PYTHONPATH": str(DEPENDENCY_OVERLAY),
        "PYTHONDONTWRITEBYTECODE": "1",
        "QT_QPA_PLATFORM": "offscreen",
    }
    records: list[dict[str, object]] = []
    junit_files: list[Path] = []

    for qt_api in ("pyqt5", "pyqt6", "pyside6"):
        junit_name = f"focused-{qt_api}.xml"
        junit_files.append(RESULTS_DIR / junit_name)
        records.append(
            run_recorded(
                label=f"focused-{qt_api}",
                command=pytest_command(*FOCUSED_TESTS, junit_name=junit_name),
                cwd=REPO_DIR,
                environment={**common_env, "QT_API": qt_api},
                expected_exit_codes=(0,),
                log_name=f"focused-{qt_api}.log",
            )
        )

    text_tests = sorted(
        {
            *(path.relative_to(REPO_DIR).as_posix() for path in (REPO_DIR / "tests").glob("test_text_transform*.py")),
            *(path.relative_to(REPO_DIR).as_posix() for path in (REPO_DIR / "tests").glob("test_textitem*.py")),
        }
    )
    text_junit = RESULTS_DIR / "text-suite-pyqt6.xml"
    junit_files.append(text_junit)
    records.append(
        run_recorded(
            label="text-suite-pyqt6",
            command=pytest_command(*text_tests, junit_name=text_junit.name),
            cwd=REPO_DIR,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(0,),
            log_name="text-suite-pyqt6.log",
        )
    )

    feature_junit = RESULTS_DIR / "full-feature-pyqt6.xml"
    baseline_junit = RESULTS_DIR / "full-upstream-pyqt6.xml"
    junit_files.extend((feature_junit, baseline_junit))
    records.append(
        run_recorded(
            label="full-feature-pyqt6",
            command=pytest_command("tests", junit_name=feature_junit.name),
            cwd=REPO_DIR,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(1,),
            log_name="full-feature-pyqt6.log",
        )
    )
    records.append(
        run_recorded(
            label="full-upstream-pyqt6",
            command=pytest_command("tests", junit_name=baseline_junit.name),
            cwd=BASELINE_DIR,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(1,),
            log_name="full-upstream-pyqt6.log",
        )
    )

    changed_production = [
        name
        for name in git(
            "diff", "--name-only", f"{BASELINE_SHA}...{TARGET_SHA}", "--", "ballontranslator"
        ).stdout.splitlines()
        if name.endswith(".py")
    ]
    records.append(
        run_recorded(
            label="changed-production-syntax-compile",
            command=[
                str(PYTHON),
                "-c",
                (
                    "import pathlib, sys\n"
                    "for name in sys.argv[1:]:\n"
                    "    path = pathlib.Path(name)\n"
                    "    compile(path.read_bytes(), str(path), 'exec')\n"
                ),
                *changed_production,
            ],
            cwd=REPO_DIR,
            environment=common_env,
            expected_exit_codes=(0,),
            log_name="static-syntax-compile.log",
        )
    )
    records.append(
        run_recorded(
            label="relevant-doctests",
            command=[
                str(PYTHON),
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--doctest-modules",
                "ballontranslator/utils/fontformat.py",
                "ballontranslator/ui/text_transform.py",
                "ballontranslator/ui/mainwindow.py",
                "-q",
            ],
            cwd=REPO_DIR,
            environment={**common_env, "QT_API": "pyqt6"},
            expected_exit_codes=(0,),
            log_name="relevant-doctests.log",
        )
    )

    junit = {
        path.name: junit_summary(path)
        for path in junit_files
    }
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

    environment = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python_executable": str(PYTHON),
        "python_version": subprocess.run(
            [str(PYTHON), "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        ).stdout.strip(),
        "dependency_overlay": str(DEPENDENCY_OVERLAY),
        "packages": overlay_package_versions(common_env),
    }
    write_json(RESULTS_DIR / "environment.json", environment)

    provenance = {
        "target_sha": TARGET_SHA,
        "head": head,
        "branch": git("branch", "--show-current").stdout.strip(),
        "baseline_sha": BASELINE_SHA,
        "baseline_worktree_head": baseline_head,
        "baseline_production_test_status": baseline_status,
        "merge_base": git("merge-base", BASELINE_SHA, TARGET_SHA).stdout.strip(),
        "tracked_production_test_diff_from_head": dirty_implementation,
        "staged_production_test_diff_from_head": dirty_staged_implementation,
        "production_test_status": implementation_status,
        "full_status_at_generation": git(
            "status", "--short", "--untracked-files=all"
        ).stdout.splitlines(),
        "changed_production_python": changed_production,
        "commit_subjects": git(
            "log", "--format=%H %s", f"{BASELINE_SHA}..{TARGET_SHA}"
        ).stdout.splitlines(),
    }
    write_json(RESULTS_DIR / "git-provenance.json", provenance)

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
    (RESULTS_DIR / "verification-summary.md").write_text(
        "\n".join(summary_lines), encoding="utf-8"
    )

    inventory_paths = [
        path
        for path in RESULTS_DIR.rglob("*")
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
        "feature_and_upstream_failure_ids_equal": feature_failures == baseline_failures,
        "failure_ids": feature_failures,
        "files": {
            str(path.relative_to(ARTIFACT_DIR)).replace("\\", "/"): {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in sorted(set(inventory_paths))
        },
    }
    write_json(RESULTS_DIR / "generation-report.json", report)


if __name__ == "__main__":
    main()
