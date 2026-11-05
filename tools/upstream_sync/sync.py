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
    },
  )
  try:
    with urllib.request.urlopen(request, timeout=45) as response:
      return json.load(response)
  except urllib.error.HTTPError as exc:
    # GitHub errors contain useful permission/branch-protection diagnostics,
    # but must never include request headers or the token.
    detail = json.loads(exc.read()).get("message", str(exc))
    raise RuntimeError(f"GitHub {method} {path}: {exc.code} {detail}") from None


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


def prepare(dry_run: bool):
  state = analyse()
  Path("upstream-sync-report.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
  if dry_run:
    emit(**state)
    return
  if state["state"] == "current":
    summary("carrot-ev6 already contains the current upstream commit.")
    emit(ready=False)
    return
  pr = ensure_pull(state)
  summary(f"Upstream integration PR: {pr['html_url']}\n\nState: {state['state']}")
  if state["state"] == "conflict":
    summary("Conflicting files:\n" + "\n".join(f"- `{p}`" for p in state["conflicts"]))
    emit(ready=False, pr=pr["number"], conflict=True)
    return
  # GitHub prepares a synthetic merge commit. Test its exact SHA, not moving
  # branch names, and verify both parents and tree against the local merge.
  merge_ref = f"refs/pull/{pr['number']}/merge"
  for attempt in range(12):
    current_pr = api(f"repos/{REPOSITORY}/pulls/{pr['number']}")
    verify_pull(current_pr, state["base"], state["source"])
    fetched = git("fetch", "--no-tags", "origin", merge_ref, check=False)
    if fetched.returncode == 0:
      merge_sha = git("rev-parse", "FETCH_HEAD").stdout.strip()
      parents = git("show", "-s", "--format=%P", merge_sha).stdout.split()
      tree = git("show", "-s", "--format=%T", merge_sha).stdout.strip()
      if (parents == [state["base"], state["source"]] and tree == state["tree"] and
          current_pr.get("mergeable") is True and current_pr.get("merge_commit_sha") == merge_sha):
        emit(ready=True, pr=pr["number"], merge_sha=merge_sha, base_sha=state["base"], source_sha=state["source"])
        return
    if attempt < 11:
      time.sleep(3)
  raise RuntimeError("GitHub merge ref is not ready or changed; no merge will be performed")


def finish(number: int, base: str, source: str, merge_sha: str):
  # This phase runs only after all validation jobs succeeded. Fetch the PR again
  # to reject upstream/target movement while CI was running.
  pr = api(f"repos/{REPOSITORY}/pulls/{number}")
  verify_pull(pr, base, source)
  if pr.get("mergeable") is not True or pr.get("merge_commit_sha") != merge_sha:
    raise RuntimeError("Tested merge candidate is no longer current or mergeable")
  current_base = api(f"repos/{REPOSITORY}/git/ref/heads/{TARGET}")["object"]["sha"]
  current_source = api(f"repos/{SOURCE}/git/ref/heads/{SOURCE_BRANCH}")["object"]["sha"]
  if current_base != base or current_source != source:
    raise RuntimeError("A branch advanced during CI; the next run must test it again")
  result = api(f"repos/{REPOSITORY}/pulls/{number}/merge", method="PUT", data={
    "sha": source, "merge_method": "merge",
    "commit_title": f"Merge {SOURCE_BRANCH} into {TARGET} (#{number})",
  })
  if result.get("merged") is not True:
    raise RuntimeError(result.get("message", "GitHub refused the merge"))
  summary(f"Merged PR #{number}: `{result['sha']}`. No device installation or reboot was requested.")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  prepare_args = sub.add_parser("prepare")
  prepare_args.add_argument("--dry-run", action="store_true")
  finish_args = sub.add_parser("finish")
  finish_args.add_argument("--pr", type=int, required=True)
  for arg in ("base", "source", "merge"):
    finish_args.add_argument(f"--{arg}", required=True)
  args = parser.parse_args()
  if args.command == "prepare":
    prepare(args.dry_run)
  else:
    finish(args.pr, args.base, args.source, args.merge)


if __name__ == "__main__":
  main()
