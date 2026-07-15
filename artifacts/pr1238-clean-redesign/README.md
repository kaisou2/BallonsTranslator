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

## Replay topology

The replay uses three distinct locations. They must not be conflated:

1. **Final documentation checkout**: the checkout containing this README and
   the current replay runner. Generated evidence is written here. Its HEAD is
   the final review/docs commit and is intentionally allowed to be newer than
   the implementation SHA. It must be clean when the replay starts so the
   runner and documentation bytes are attributable to that exact HEAD.
2. **Implementation worktree**: a separate, fully clean detached worktree at
   exactly `a1f7c1f7b6e1e93400a4372b8e228d3908a1a966`. Tests of the feature run
   here. Pass it with `--target-worktree` or `PR1238_REPO_DIR`.
3. **Baseline worktree**: a separate, fully clean detached worktree at exactly
   `6155f9b303033b24f57a2c025d2edbfed3eb847f`. Baseline comparison tests run
   here. Pass it with `--baseline-worktree` or `PR1238_BASELINE_DIR`.

The Python interpreter and dependency overlay default to the Windows paths
used for the recorded run. They can be supplied with `--python` and
`--dependency-overlay`, or with `PR1238_PYTHON` and
`PR1238_DEPENDENCY_OVERLAY`. The overlay must provide the package set recorded
in `test-results/environment.json`.

### PowerShell replay example

Run these commands from the final documentation checkout. Create the detached
worktrees once; do not create them inside one another.

```powershell
$docs = 'F:\ballon\BallonsTranslator'
$target = 'F:\ballon\BallonsTranslator-pr1238-a1f7c1f'
$baseline = 'F:\ballon\BallonsTranslator-baseline-6155f9b'
$runner = Join-Path $docs 'artifacts\pr1238-clean-redesign\a1f7c1f7b6e1e93400a4372b8e228d3908a1a966\generate_evidence.py'

git -C $docs worktree add --detach $target a1f7c1f7b6e1e93400a4372b8e228d3908a1a966
git -C $docs worktree add --detach $baseline 6155f9b303033b24f57a2c025d2edbfed3eb847f

& 'F:\python\python.exe' $runner `
  --target-worktree $target `
  --baseline-worktree $baseline `
  --python 'F:\python\python.exe' `
  --dependency-overlay 'F:\ballon\.pr1238-test-deps' `
  --preflight-only

& 'F:\python\python.exe' $runner `
  --target-worktree $target `
  --baseline-worktree $baseline `
  --python 'F:\python\python.exe' `
  --dependency-overlay 'F:\ballon\.pr1238-test-deps'
```

If either worktree already exists, omit its `git worktree add` command. The
`--preflight-only` invocation validates the interpreter, overlay packages,
required files, exact target/baseline HEADs, and clean status of all three
checkouts without creating, deleting, or replacing `test-results`.

For a full replay, all output is first generated under a sibling temporary
directory. Only after every command, JUnit parse, failure-identity comparison,
and final worktree-status check succeeds does the runner install it with a
same-filesystem rename swap. A failed preflight or generation keeps the prior
`test-results` directory unchanged; a failed swap restores the prior directory.

## Generation-time file inventory semantics

The `files` hashes and sizes in the existing
`a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/test-results/generation-report.json`
describe the **raw bytes read from the generation-time Windows worktrees**.
They therefore include checkout-specific line endings: for text files whose
working-tree representation was CRLF, the recorded SHA-256 and size can differ
from the committed LF Git blob.

That inventory is useful for identifying the exact files present during the
recorded run, but it is **not Git blob integrity evidence** and must not be
presented as proof that the hashes match committed repository blobs. Verify
committed content separately from Git object data when that claim is needed.
The existing result files are intentionally retained as the historical record;
this clarification does not rewrite or regenerate them. Future runs of the
revised runner also write this limitation explicitly into
`file_inventory_semantics`.

## Superseded historical bundle

`f101479edbaf4b35606fe7503ae752846055d71d/` is an immutable historical bundle
for the older `f101479edbaf4b35606fe7503ae752846055d71d` implementation. It is
retained for provenance and must not be rewritten to describe later code.

That bundle, including its reviewer report and generated evidence, is
**superseded and is not a current approval basis**. In particular, it predates
the later anisotropic-scale rotation correction and global text-effect pipeline
correction. Any approval in that directory attests only to the older bytes.
