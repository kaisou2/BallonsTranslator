# PR #1238 clean-redesign artifacts

Artifact evidence in this directory is scoped to the exact implementation SHA
named by its directory. Evidence or approval for one SHA must not be treated as
approval for a later SHA.

## Current review target

- Implementation SHA: `a1f7c1f7b6e1e93400a4372b8e228d3908a1a966`
- Manual record: [`a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/manual-checklist.md`](a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/manual-checklist.md)
- Automated verification: [`a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/test-results/verification-summary.md`](a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/test-results/verification-summary.md)
- Recorded local replay script: [`a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/generate_evidence.py`](a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/generate_evidence.py)
- Independent reviewer approval: **Pending**

The current manual record contains only observations supplied by the user in
the development conversation. The conversation screenshots were not copied
into this repository, so the record is an attestation with stated limitations,
not a self-contained reproduction bundle.

The replay script defaults to the Windows paths used for this recorded run.
They can be replaced without editing the script through `PR1238_PYTHON`,
`PR1238_DEPENDENCY_OVERLAY`, and `PR1238_BASELINE_DIR`. The baseline directory
must be a clean detached worktree at
`6155f9b303033b24f57a2c025d2edbfed3eb847f`; the dependency overlay must match
the package versions recorded in `test-results/environment.json`. The script
rejects a different target or baseline SHA before running tests.

## Superseded historical bundle

`f101479edbaf4b35606fe7503ae752846055d71d/` is an immutable historical bundle
for the older `f101479edbaf4b35606fe7503ae752846055d71d` implementation. It is
retained for provenance and must not be rewritten to describe later code.

That bundle, including its reviewer report and generated evidence, is
**superseded and is not a current approval basis**. In particular, it predates
the later anisotropic-scale rotation correction and global text-effect pipeline
correction. Any approval in that directory attests only to the older bytes.
