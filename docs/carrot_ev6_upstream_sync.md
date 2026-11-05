# EV6 upstream synchronization

`EV6 upstream sync` checks `ajouatom/openpilot:carrot-wip` at minute 17 and
47 of every hour (UTC) and can also be run manually from Actions. GitHub may
delay scheduled runs. The fork's default branch is `carrot-ev6`, because
scheduled workflows must exist on the default branch. The workflow is restricted
to `huukuujuu/openpilot`; it does not update the fork's `carrot-wip` branch.

For new upstream history, it opens or updates one PR from the original
`ajouatom:carrot-wip` into `huukuujuu:carrot-ev6`. It computes an ordinary
three-way merge without altering a working checkout. Text conflicts, including
modify/delete conflicts, block integration and are listed in the PR and run
summary. The JSON merge report is saved as an Actions artifact.

If conflict-free, the workflow creates a deterministic local merge commit.
Every job in `tests.yaml` fetches the pinned source and base and reconstructs
that commit, checking both its tree and SHA before testing. This avoids stale
GitHub `refs/pull/N/merge` objects when the target advances. The temporary commit
is never pushed. The automation's own regression suite also runs. Validation jobs
receive read-only repository tokens and no inherited secrets. Only after every
job succeeds does a separate write-capable controller recheck the PR, upstream
head, target head and reconstructed merge SHA, then merge the PR with the pinned
upstream head and verify the resulting commit's tree and parents.
If either branch advances during validation, the run stops and the next run must
test the new candidate. Repository protection rules still apply.

No force push, rebase, file-wide ours/theirs resolution, source overwrite,
device write or device reboot is used. Git can merge independent changes;
passing CI is not proof that all behavioral interactions are correct or that
the change has been validated on a vehicle. Manual pushes racing the final
GitHub merge request remain subject to GitHub's PR merge/protection behavior;
the controller is not a replacement for strict branch protection rules.

The repository must enable Actions and allow GitHub Actions to create pull
requests. It uses the built-in `GITHUB_TOKEN` with per-job permissions; no PAT,
Cloudflare account, or separately stored credential is needed. CI is invoked
directly as a reusable workflow, so integration does not depend on push/PR
events recursively triggering new runs with `GITHUB_TOKEN`.

To pause, disable `EV6 upstream sync` in Actions. To handle a conflict, inspect
the PR and merge report, resolve the actual changes in a local integration
branch, test, and merge manually. Do not choose an entire side merely to clear
the conflict. If CI fails, inspect the failing job before changing the gate.
An unrelated manually managed upstream PR is never taken over automatically.

Local checks:

```sh
python -m unittest discover -s tools/upstream_sync -p 'test_*.py' -v
python tools/upstream_sync/sync.py prepare --dry-run
```

The dry run fetches Git refs and writes `upstream-sync-report.json` locally;
it does not create PRs, change branch heads, merge, or require a GitHub token.

After a merge, a comma installed from this fork follows `origin/carrot-ev6`
through its existing device update setting. This workflow does not enable that
setting or change its reboot policy.
