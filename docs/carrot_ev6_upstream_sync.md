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

## Uncertain merge responses and stale PRs

A 502/503/504, timeout, or connection failure can arrive after GitHub has already
updated the branch. The merge controller therefore checks fetched Git history
before retrying a write. It requires a reachable first-parent merge with exactly
the validated base and source parents and the validated three-way merge tree.
Mere upstream ancestry does not count as successful validation. An exact applied
merge is successful even if the HTTP response failed or a later rerun sees that
the target has advanced. It never requests a second merge of that candidate.

If no applied merge can be proved, a transient error permits at most three merge
requests, with bounded observations between them. Each new request repeats the
branch-head and candidate-tree gates. Permission and other permanent failures
are not retried. Safe API reads have bounded transient retries; other writes are
not blindly replayed.

An already-applied automatic PR can remain open when GitHub fails to update its
PR metadata. The controller closes that PR with a commit receipt only if its
current head still matches the applied source and its repository/branch identity
matches this automation. A newer upstream head is kept open for fresh validation;
if it races cleanup, the PR is reopened. An unrelated manually managed PR is not
touched. A cleanup API outage produces a warning rather than erasing a verified
merge success; subsequent scheduled no-op checks retry cleanup. Dry runs never
change PR metadata. GitHub's historical failed runs are retained; a later success
and the receipt explain the recovered state. Closing a stale PR does not rewrite
GitHub's separate `merged` flag.

This handles the 2026-11-06 incident: run `42058935604` reported a merge HTTP 502,
but `0ce0fb27d288f6044260947de1d16152de868cc0` was already in `carrot-ev6`, with
parents `ad888df1` and `612881c4`. A rerun had then rejected the advanced target
instead of recognizing the completed merge.

No force push, rebase, file-wide ours/theirs resolution, source overwrite,
device write or device reboot is used. Git can merge independent changes;
passing CI is not proof that all behavioral interactions are correct or that
the change has been validated on a vehicle. Manual pushes racing the final
GitHub merge request remain subject to GitHub's PR merge/protection behavior;
the controller is not a replacement for strict branch protection rules.

The repository must enable Actions and allow GitHub Actions to create pull
requests. Preparation and tests use the built-in `GITHUB_TOKEN` with per-job
permissions. The final merge uses the repository secret `UPSTREAM_SYNC_TOKEN`
when configured, otherwise it tries the built-in token. The secret is available
only to the separate merge controller, never to candidate tests. CI is invoked
directly as a reusable workflow, so integration does not depend on push/PR
events recursively triggering new runs with `GITHUB_TOKEN`.

## One-time merge credential

On 2026-11-05, every validation job in run `41993713896` passed, including the
native build, settings compatibility and isolated Bluetooth checks. GitHub
rejected the built-in token at the final PR merge with HTTP 403, despite
`contents: write` and `pull-requests: write`. There were no branch-protection
rules. The documented repository merge endpoint also rejected the same token
in verification run `41995499940` (`contents=write` accepted-permissions header).
The precise additional authorization decision is not exposed in that response;
do not describe it as a proven Git conflict or failed CI. The update includes
workflow changes. A normal owner-authenticated merge of the same tested heads
succeeded as PR #1, commit `f52782f93e89789567a27e7f4c630cc45936ab00`, with its
resulting tree and parents verified. No desktop credential was copied to Actions.

For unattended merges, create a fine-grained GitHub personal access token owned
by `huukuujuu`, restricted to `huukuujuu/openpilot`, with repository permissions
Contents, Pull requests and Workflows set to Read and write. Give it an expiry
and renew the repository secret when needed. Save its value under Settings >
Secrets and variables > Actions > New repository secret, with the exact name
`UPSTREAM_SYNC_TOKEN`. Never commit the token or paste it into a PR/chat. The
workflow automatically picks it up on the next run; no source edit is needed.
An appropriately scoped GitHub App installation token is another option for
long-lived administration. The unattended merge credential must be verified
after registration; the built-in-token denial remains a known platform limit
for this tested PR rather than evidence that every GitHub-token merge fails.

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
