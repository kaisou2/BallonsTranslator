# Automated verification summary

Implementation target: `a1f7c1f7b6e1e93400a4372b8e228d3908a1a966`

This bundle records automated checks only. Manual GUI acceptance and independent reviewer approval remain separate and pending where stated.

The table below reports JUnit testcase outcomes. Pytest's console summaries are recorded separately because pytest-subtests can count a parent test differently in console and JUnit output.

| Run | Passed | Failed | Errors | Skipped |
|---|---:|---:|---:|---:|
| `focused-pyqt5.xml` | 29 | 0 | 0 | 0 |
| `focused-pyqt6.xml` | 29 | 0 | 0 | 0 |
| `focused-pyside6.xml` | 29 | 0 | 0 | 0 |
| `text-suite-pyqt6.xml` | 209 | 0 | 0 | 0 |
| `full-feature-pyqt6.xml` | 373 | 7 | 0 | 1 |
| `full-upstream-pyqt6.xml` | 158 | 7 | 0 | 1 |

## Pytest console summaries

- `focused-pyqt5`: 29 passed, 1 warning, 151 subtests passed in 3.81s
- `focused-pyqt6`: 29 passed, 1 warning, 151 subtests passed in 3.93s
- `focused-pyside6`: 29 passed, 1 warning, 151 subtests passed in 4.32s
- `text-suite-pyqt6`: 209 passed, 1 warning, 409 subtests passed in 7.39s
- `full-feature-pyqt6`: 7 failed, 374 passed, 1 skipped, 1 warning, 446 subtests passed in 8.42s
- `full-upstream-pyqt6`: 7 failed, 159 passed, 1 skipped, 1 warning, 7 subtests passed in 2.22s
- `relevant-doctests`: 6 passed, 1 warning in 1.14s

The feature and exact-upstream full runs have the same seven failure identities. They are pre-existing baseline failures, not new failures introduced by the target implementation.

Changed production modules passed an in-memory Python syntax compilation check; relevant FontFormat and text-transform doctests passed. Raw command logs, JUnit XML, environment details, and Git provenance are stored beside this summary.

Independent reviewer approval: **Pending**
