"""Rebuild a narrow set of web outputs with read-only credentials, then publish the verified tree."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sync import REPOSITORY, SOURCE, SOURCE_BRANCH, TARGET, WEB, WEB_FILES as FILES, api, candidate_commit, emit, git

TITLE = "Rebuild Carrot Web for the EV6 upstream merge"


def conflict_tree(base, source):
  result = git("merge-tree", "--write-tree", "--name-only", base, source, check=False)
  lines = result.stdout.partition("\n\n")[0].splitlines()
  paths = set(lines[1:])
  if result.returncode != 1 or not paths or not paths <= FILES:
    raise ValueError("Recovery requires conflicts confined to the two generated web files")
  for rev in (base, source, lines[0]):
    for path in FILES:
      if not git("ls-tree", rev, "--", path).stdout.startswith("100644 blob "):
        raise ValueError(f"Missing or nonregular generated file: {path}")
  return lines[0]


def rebuild(base, source, directory):
  tree = conflict_tree(base, source)
  git("checkout", "--detach", base)
  git("read-tree", "--reset", "-u", tree)
  subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "ci"], cwd=WEB, check=True)
  subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "run", "check"], cwd=WEB, check=True)
  changed = set(git("diff", "--name-only", tree).stdout.splitlines())
  if not changed <= FILES or git("ls-files", "--others", "--exclude-standard").stdout.strip():
    raise ValueError("Build changed files outside the allowed outputs")
  bundle = Path(WEB + "js/generated/tools.js").read_text(encoding="utf-8")
  if "dashcam_upload_url" not in bundle or "/api/dashcam/upload/test" not in bundle:
    raise ValueError("EV6 dashcam settings missing from tools bundle")
  directory.mkdir(exist_ok=True, parents=True)
  for path in FILES:
    (directory / Path(path).name).write_bytes(Path(path).read_bytes())


def reconstruct(base, source, directory):
  # The privileged runner imports only two data blobs into a temporary Git index.
  tree = conflict_tree(base, source)
  with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
    subprocess.run(["git", "read-tree", tree], env=env, check=True)
    for path in sorted(FILES):
      artifact = directory / Path(path).name
      if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"Invalid web artifact: {path}")
      blob = git("hash-object", "-w", "--", str(artifact)).stdout.strip()
      subprocess.run(["git", "update-index", "--cacheinfo", f"100644,{blob},{path}"], env=env, check=True)
    resolved = subprocess.check_output(["git", "write-tree"], env=env, text=True).strip()
  return candidate_commit(base, source, resolved)


def assert_current(base, source):
  for remote, ref, expected in (("origin", TARGET, base), (f"https://github.com/{SOURCE}.git", SOURCE_BRANCH, source)):
    if git("ls-remote", "--exit-code", remote, f"refs/heads/{ref}").stdout.split()[0] != expected:
      raise RuntimeError("Branches advanced; wait for fresh validation")


def publish(base, source, directory):
  assert_current(base, source)
  candidate = reconstruct(base, source, directory)
  branch = "codex/ev6-web-recovery-" + candidate
  git("push", "origin", f"{candidate}:refs/heads/{branch}")
  pulls = api(f"repos/{REPOSITORY}/pulls?state=open&base={TARGET}&head=huukuujuu:{branch}")
  pr = pulls[0] if pulls else api(f"repos/{REPOSITORY}/pulls", method="POST", data={
    "title": TITLE, "head": branch, "base": TARGET,
    "body": f"Rebuilt web assets for upstream {source} and EV6 {base}. Exact CI candidate: {candidate}.",
  })
  verify_pr(pr, candidate, base)
  emit(candidate=candidate, pr=pr["number"])


def verify_pr(pr, candidate, base):
  if (pr["state"] != "open" or pr["draft"] or pr["title"] != TITLE or
      any(pr[k]["repo"]["full_name"] != REPOSITORY for k in ("base", "head")) or
      pr["base"]["ref"] != TARGET or pr["base"]["sha"] != base or pr["head"]["sha"] != candidate or
      pr["head"]["ref"] != "codex/ev6-web-recovery-" + candidate):
    raise RuntimeError("Recovery PR changed")


def finish(base, source):
  candidate, number = os.environ["CANDIDATE"], int(os.environ["PR"])
  path = f"repos/{REPOSITORY}/pulls/{number}"
  verify_pr(api(path), candidate, base)
  assert_current(base, source)
  receipt = api(path + "/merge", method="PUT", data={"sha": candidate, "merge_method": "merge"})
  if not receipt.get("merged"):
    raise RuntimeError("Recovery PR was not merged")
  git("fetch", "--no-tags", "origin", receipt["sha"])
  if (git("show", "-s", "--format=%P", receipt["sha"]).stdout.split() != [base, candidate] or
      git("rev-parse", receipt["sha"] + "^{tree}").stdout != git("rev-parse", candidate + "^{tree}").stdout):
    raise RuntimeError("Unexpected merge receipt; inspect PR")
  print("Verified recovery merge", receipt["sha"])


if __name__ == "__main__":
  base, source = os.environ["BASE"], os.environ["SOURCE"]
  git("fetch", "--no-tags", f"https://github.com/{SOURCE}.git", source)
  if sys.argv[1] == "finish":
    finish(base, source)
  else:
    {"rebuild": rebuild, "publish": publish}[sys.argv[1]](base, source, Path(os.environ["ARTIFACT"]))
