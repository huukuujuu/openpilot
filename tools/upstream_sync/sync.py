"""Merge ajouatom/carrot-wip into this fork only after testing the exact PR merge.

No force pushes, rebases, or automatic conflict resolutions. Authentication comes
from GITHUB_TOKEN; local dry runs do not require a token or change remote state.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "huukuujuu/openpilot"
SOURCE = "ajouatom/openpilot"
SOURCE_BRANCH = "carrot-wip"
TARGET = "carrot-ev6"
UPSTREAM_REF = "refs/upstream-sync/source"
TARGET_REF = "refs/upstream-sync/target"
TITLE = "chore: merge upstream carrot-wip into carrot-ev6"
TRANSIENT_ERRORS = {0, 408, 500, 502, 503, 504}


class GitHubAPIError(RuntimeError):
  def __init__(self, status: int, message: str):
    self.status = status
    super().__init__(message)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
  return subprocess.run(["git", *args], text=True, encoding="utf-8", errors="replace", capture_output=True, check=check)


def api(path: str, *, method: str = "GET", data=None):
  request = urllib.request.Request(
    "https://api.github.com/" + path,
    method=method,
    data=None if data is None else json.dumps(data).encode(),
    headers={
      "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "carrot-ev6-upstream-sync",
      "Cache-Control": "no-cache",
    },
  )
  # Reads may be retried. Writes, especially merges, must be reconciled against
  # actual Git history before another write is attempted.
  for attempt in range(3 if method == "GET" else 1):
    try:
      with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
      body = exc.read()
      try:
        detail = json.loads(body).get("message", f"HTTP {exc.code}")
      except (ValueError, AttributeError):
        detail = f"HTTP {exc.code} (non-JSON response)"
      error = GitHubAPIError(exc.code, f"GitHub {method} {path}: {exc.code} {detail}")
    except (urllib.error.URLError, TimeoutError) as exc:
      error = GitHubAPIError(0, f"GitHub {method} {path}: network/timeout failure ({type(exc).__name__})")
    if method != "GET" or error.status not in TRANSIENT_ERRORS or attempt == 2:
      raise error from None
    time.sleep(2 ** attempt)


def emit(**values):
  print(json.dumps(values, ensure_ascii=False))
  output = os.environ.get("GITHUB_OUTPUT")
  if output:
    with open(output, "a", encoding="utf-8") as stream:
      for key, value in values.items():
        if "\n" in str(value):
          raise ValueError("Output values must be single-line")
        stream.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def summary(text: str):
  print(text)
  if path := os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(path, "a", encoding="utf-8") as stream:
      stream.write(text + "\n")


def analyse() -> dict:
  git("fetch", "--no-tags", f"https://github.com/{SOURCE}.git", f"+refs/heads/{SOURCE_BRANCH}:{UPSTREAM_REF}")
  git("fetch", "--no-tags", "origin", f"+refs/heads/{TARGET}:{TARGET_REF}")
  source = git("rev-parse", UPSTREAM_REF).stdout.strip()
  base = git("rev-parse", TARGET_REF).stdout.strip()
  contained = git("merge-base", "--is-ancestor", source, base, check=False)
  if contained.returncode not in (0, 1):
    raise RuntimeError(contained.stderr)
  if contained.returncode == 0:
    return {"state": "current", "source": source, "base": base}
  # Reject unrelated history rather than trying an automatic replacement.
  git("merge-base", base, source)
  result = git("merge-tree", "--write-tree", base, source, check=False)
  if result.returncode not in (0, 1):
    raise RuntimeError(result.stderr)
  conflicts = []
  if result.returncode:
    conflicts = git("merge-tree", "--write-tree", "--name-only", base, source, check=False).stdout.split("\n\n", 1)[0].splitlines()[1:]
  return {
    "state": "conflict" if result.returncode else "ready",
    "source": source, "base": base, "conflicts": conflicts,
    "tree": result.stdout.splitlines()[0],
  }


def pull_body(state: dict) -> str:
  conflict_text = "\n".join(f"- `{path}`" for path in state.get("conflicts", []))
  result = (
    "Automatic merge is blocked by conflicts. Resolve them manually; no side is selected automatically.\n\n" + conflict_text
    if state["state"] == "conflict" else
    "The exact proposed merge must pass the EV6 upstream-sync CI before automatic merge."
  )
  run = os.environ.get("GITHUB_RUN_ID")
  run_link = f"\n\n[Latest sync run](https://github.com/{REPOSITORY}/actions/runs/{run})" if run else ""
  return (
    f"Synchronize `{SOURCE}:{SOURCE_BRANCH}` into `{REPOSITORY}:{TARGET}` using a normal merge commit.\n\n"
    f"Upstream: `{state['source']}`\n\nTarget at inspection: `{state['base']}`\n\n"
    f"{result}{run_link}\n\n"
    "No rebase, force push, file-wide ours/theirs strategy, or device install/reboot is performed.\n\n"
    "Docs-Not-Needed: upstream integration automation; vehicle settings are not changed by this workflow."
  )


def ensure_pull(state: dict) -> dict:
  query = urllib.parse.urlencode({"state": "open", "base": TARGET, "head": f"ajouatom:{SOURCE_BRANCH}", "per_page": 100})
  pulls = api(f"repos/{REPOSITORY}/pulls?{query}")
  matches = [pr for pr in pulls if pr.get("head", {}).get("repo", {}).get("full_name") == SOURCE]
  if len(matches) > 1:
    raise RuntimeError("Multiple upstream PRs exist; resolve the duplicate before continuing")
  body = pull_body(state)
  if matches:
    pr = matches[0]
    if pr.get("title") != TITLE:
      raise RuntimeError("An existing manually managed upstream PR exists; automatic takeover is disabled")
    # Explicitly reselect the same base: GitHub can retain the old PR base SHA
    # and synthetic merge after the target branch advances. A body-only edit
    # does not refresh it. This never switches to a different target branch.
    return api(f"repos/{REPOSITORY}/pulls/{pr['number']}", method="PATCH", data={"body": body, "base": TARGET})
  return api(f"repos/{REPOSITORY}/pulls", method="POST", data={
    "title": TITLE, "head": f"ajouatom:{SOURCE_BRANCH}", "base": TARGET,
    "body": body, "maintainer_can_modify": False,
  })


def verify_pull(pr: dict, base: str, source: str):
  if (pr.get("state") != "open" or pr.get("draft") or
      pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY or
      pr.get("head", {}).get("repo", {}).get("full_name") != SOURCE or
      pr.get("base", {}).get("ref") != TARGET or
      pr.get("head", {}).get("ref") != SOURCE_BRANCH or
      pr["base"]["sha"] != base or pr["head"]["sha"] != source):
    raise RuntimeError("PR source/base changed; wait for the next sync run")


def managed_pull(pr: dict) -> bool:
  return (
    pr.get("title") == TITLE and
    (pr.get("base", {}).get("repo") or {}).get("full_name") == REPOSITORY and
    (pr.get("head", {}).get("repo") or {}).get("full_name") == SOURCE and
    pr.get("base", {}).get("ref") == TARGET and
    pr.get("head", {}).get("ref") == SOURCE_BRANCH
  )


def find_applied_merge(base: str, source: str, merge_sha: str | None = None) -> str | None:
  """Prove that the exact validated merge is in the target's first-parent history.

  An ancestor check alone is insufficient: it could accept a different merge,
  a changed tree, or discard EV6 work. Fetch public Git objects independently
  of the possibly stale PR/REST merge response.
  """
  git("fetch", "--no-tags", "origin", f"+refs/heads/{TARGET}:{TARGET_REF}")
  head = git("rev-parse", TARGET_REF).stdout.strip()
  if head == base:
    return None
  ancestor = git("merge-base", "--is-ancestor", base, head, check=False)
  if ancestor.returncode == 1:
    return None
  if ancestor.returncode:
    raise RuntimeError("Unable to verify target history after merge request")
  history = git("rev-list", "--first-parent", "--parents", "--max-count=200", f"{base}..{head}").stdout.splitlines()
  for line in history:
    commit, *parents = line.split()
    if parents != [base, source]:
      continue
    result = git("merge-tree", "--write-tree", base, source)
    expected_tree = result.stdout.splitlines()[0]
    if merge_sha is not None and candidate_commit(base, source, expected_tree) != merge_sha:
      raise RuntimeError("Applied merge does not match the validated candidate")
    if git("show", "-s", "--format=%T", commit).stdout.strip() != expected_tree:
      raise RuntimeError("Applied merge has unexpected contents; manual inspection required")
    return commit
  return None


def reconcile_pull(number: int, source: str, applied: str) -> None:
  """Close only our stale, already-applied PR and retain a verifiable receipt."""
  try:
    path = f"repos/{REPOSITORY}/pulls/{number}"
    pr = api(path)
    if not managed_pull(pr) or pr.get("state") != "open" or pr["head"]["sha"] != source:
      return
    marker = f"<!-- EV6-SYNC-APPLIED:{source} -->"
    body = pr.get("body") or ""
    if marker not in body:
      body += (
        f"\n\n{marker}\nApplied upstream `{source}` was verified in target history as "
        f"[{applied[:8]}](https://github.com/{REPOSITORY}/commit/{applied}). "
        "The merge parents and file tree match the proposed integration. "
        "Closing this already-applied PR even if GitHub did not update its merged flag."
      )
    api(path, method="PATCH", data={"state": "closed", "body": body})
    # Do not leave a newly advanced upstream head closed if it raced the PATCH.
    updated = api(path)
    if managed_pull(updated) and updated["head"]["sha"] != source and updated.get("state") == "closed":
      api(path, method="PATCH", data={"state": "open"})
      summary(f"PR #{number} advanced during cleanup and was reopened for fresh validation.")
    else:
      summary(f"Reconciled already-applied PR #{number}: `{applied}`.")
  except GitHubAPIError as exc:
    # A metadata outage must not turn a proven applied merge into a failed code
    # update. The next scheduled no-op check retries stale-PR cleanup.
    summary(f"::warning::Merge is verified; PR #{number} metadata cleanup is pending: {exc}")


def reconcile_current_pulls() -> None:
  query = urllib.parse.urlencode({"state": "open", "base": TARGET, "head": f"ajouatom:{SOURCE_BRANCH}", "per_page": 100})
  for pr in api(f"repos/{REPOSITORY}/pulls?{query}"):
    if managed_pull(pr):
      applied = find_applied_merge(pr["base"]["sha"], pr["head"]["sha"])
      if applied:
        reconcile_pull(pr["number"], pr["head"]["sha"], applied)


def candidate_commit(base: str, source: str, tree: str) -> str:
  """Create the same local merge object on every runner; do not push it."""
  timestamp = max(int(git("show", "-s", "--format=%ct", sha).stdout) for sha in (base, source)) + 1
  env = {
    **os.environ,
    "GIT_AUTHOR_NAME": "EV6 Upstream Sync", "GIT_COMMITTER_NAME": "EV6 Upstream Sync",
    "GIT_AUTHOR_EMAIL": "ev6-sync@users.noreply.github.com", "GIT_COMMITTER_EMAIL": "ev6-sync@users.noreply.github.com",
    "GIT_AUTHOR_DATE": f"{timestamp} +0000", "GIT_COMMITTER_DATE": f"{timestamp} +0000",
  }
  result = subprocess.run(
    ["git", "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", base, "-p", source],
    input=b"Validate pinned carrot-wip / carrot-ev6 integration\n",
    capture_output=True, check=True, env=env,
  )
  return result.stdout.decode("ascii").strip()


def checkout_candidate(source: str, tree: str, expected_commit: str):
  """Reconstruct and check the candidate after checking out its pinned base."""
  base = git("rev-parse", "HEAD").stdout.strip()
  git("fetch", "--no-tags", f"https://github.com/{SOURCE}.git", source)
  result = git("merge-tree", "--write-tree", base, source)
  if result.stdout.splitlines()[0] != tree:
    raise RuntimeError("Runner merge tree differs from the prepared candidate")
  candidate = candidate_commit(base, source, tree)
  if candidate != expected_commit:
    raise RuntimeError("Runner merge commit differs from the prepared candidate")
  git("checkout", "--detach", candidate)
  git("submodule", "update", "--init", "--recursive")
  print(f"Checked out exact integration commit {candidate} (tree {tree})")


def prepare(dry_run: bool):
  state = analyse()
  Path("upstream-sync-report.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
  if dry_run:
    emit(**state)
    return
  if state["state"] == "current":
    reconcile_current_pulls()
    summary("carrot-ev6 already contains the current upstream commit.")
    emit(ready=False)
    return
  pr = ensure_pull(state)
  summary(f"Upstream integration PR: {pr['html_url']}\n\nState: {state['state']}")
  if state["state"] == "conflict":
    summary("Conflicting files:\n" + "\n".join(f"- `{p}`" for p in state["conflicts"]))
    emit(ready=False, pr=pr["number"], conflict=True)
    return
  verify_pull(api(f"repos/{REPOSITORY}/pulls/{pr['number']}"), state["base"], state["source"])
  # GitHub's refs/pull/N/merge can retain a previous base even after the API's
  # base SHA has refreshed. Reconstruct one deterministic merge object instead
  # and require every validation runner to reproduce its tree and commit SHA.
  merge_sha = candidate_commit(state["base"], state["source"], state["tree"])
  emit(ready=True, pr=pr["number"], merge_sha=merge_sha, merge_tree=state["tree"], base_sha=state["base"], source_sha=state["source"])


def finish(number: int, base: str, source: str, merge_sha: str):
  # This phase runs only after all validation jobs succeeded. Fetch the PR again
  # to reject upstream/target movement while CI was running.
  pr = api(f"repos/{REPOSITORY}/pulls/{number}")
  if not managed_pull(pr):
    raise RuntimeError("PR source/base identity is not the managed upstream integration")
  applied = find_applied_merge(base, source, merge_sha)
  if applied:
    reconcile_pull(number, source, applied)
    summary(f"Verified previously applied PR #{number}: `{applied}`. No second merge was requested.")
    return
  verify_pull(pr, base, source)
  if pr.get("mergeable") is not True:
    raise RuntimeError("PR is not currently mergeable; wait for GitHub to refresh it")
  for attempt in range(3):
    # Repeat all head/tree gates before retrying a write. Never replay a merge
    # merely because the previous HTTP response was 502 or timed out.
    current_base = api(f"repos/{REPOSITORY}/git/ref/heads/{TARGET}")["object"]["sha"]
    current_source = api(f"repos/{SOURCE}/git/ref/heads/{SOURCE_BRANCH}")["object"]["sha"]
    if current_base != base or current_source != source:
      raise RuntimeError("A branch advanced during CI; the next run must test it again")
    state = analyse()
    if (state["state"] != "ready" or state["base"] != base or state["source"] != source or
        candidate_commit(base, source, state["tree"]) != merge_sha):
      raise RuntimeError("Final merge no longer matches the validated candidate")
    try:
      result = api(f"repos/{REPOSITORY}/pulls/{number}/merge", method="PUT", data={
        "sha": source, "merge_method": "merge",
        "commit_title": f"Merge {SOURCE_BRANCH} into {TARGET} (#{number})",
      })
      break
    except GitHubAPIError as exc:
      if exc.status not in TRANSIENT_ERRORS:
        raise
      summary(f"Merge response uncertain ({exc}); verifying Git history before any retry.")
      for observation in range(3):
        applied = find_applied_merge(base, source, merge_sha)
        if applied:
          reconcile_pull(number, source, applied)
          summary(f"Recovered successful PR #{number} after API error: `{applied}`.")
          return
        if observation < 2:
          time.sleep(2)
      if attempt == 2:
        raise
      time.sleep(2 ** attempt)
      pr = api(f"repos/{REPOSITORY}/pulls/{number}")
      verify_pull(pr, base, source)
  if result.get("merged") is not True:
    raise RuntimeError(result.get("message", "GitHub refused the merge"))
  merged = api(f"repos/{REPOSITORY}/git/commits/{result['sha']}")
  if merged["tree"]["sha"] != state["tree"] or [p["sha"] for p in merged["parents"]] != [base, source]:
    raise RuntimeError("GitHub merged a different base/tree; inspect the PR before further updates")
  reconcile_pull(number, source, result["sha"])
  summary(f"Merged PR #{number}: `{result['sha']}`. No device installation or reboot was requested.")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  prepare_args = sub.add_parser("prepare")
  prepare_args.add_argument("--dry-run", action="store_true")
  checkout_args = sub.add_parser("checkout")
  for arg in ("source", "tree", "merge"):
    checkout_args.add_argument(f"--{arg}", required=True)
  finish_args = sub.add_parser("finish")
  finish_args.add_argument("--pr", type=int, required=True)
  for arg in ("base", "source", "merge"):
    finish_args.add_argument(f"--{arg}", required=True)
  args = parser.parse_args()
  if args.command == "prepare":
    prepare(args.dry_run)
  elif args.command == "checkout":
    checkout_candidate(args.source, args.tree, args.merge)
  else:
    finish(args.pr, args.base, args.source, args.merge)


if __name__ == "__main__":
  main()
