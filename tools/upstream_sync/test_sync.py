from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ev6_sync", Path(__file__).with_name("sync.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class MergeAnalysisTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.origin = self.root / "origin"
    self.source = self.root / "source"
    self.work = self.root / "work"
    self.run_git("init", "-b", "carrot-ev6", str(self.origin))
    self.commit(self.origin, "shared.txt", "base\n", "base")
    self.run_git("clone", str(self.origin), str(self.source))
    self.run_git("checkout", "-b", "carrot-wip", cwd=self.source)
    self.run_git("clone", str(self.origin), str(self.work))
    self.old_cwd = Path.cwd()
    os.chdir(self.work)
    real_git = sync.git

    def local_git(*args, **kwargs):
      args = tuple(str(self.source) if arg == f"https://github.com/{sync.SOURCE}.git" else arg for arg in args)
      return real_git(*args, **kwargs)

    self.git_patch = patch.object(sync, "git", side_effect=local_git)
    self.git_patch.start()

  def tearDown(self):
    self.git_patch.stop()
    os.chdir(self.old_cwd)
    self.temp.cleanup()

  def run_git(self, *args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

  def commit(self, repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    self.run_git("add", filename, cwd=repo)
    self.run_git("-c", "user.name=Sync Test", "-c", "user.email=sync-test@example.invalid", "commit", "-m", message, cwd=repo)

  def test_already_included_is_noop(self):
    self.assertEqual(sync.analyse()["state"], "current")

  def test_independent_customization_survives_real_three_way_merge(self):
    self.commit(self.origin, "custom.txt", "personal setting\n", "custom")
    self.commit(self.source, "upstream.txt", "new feature\n", "upstream")
    before = self.run_git("rev-parse", "HEAD", cwd=self.work)
    state = sync.analyse()
    self.assertEqual(state["state"], "ready")
    for filename, content in [("custom.txt", "personal setting"), ("upstream.txt", "new feature")]:
      self.assertEqual(self.run_git("show", f"{state['tree']}:{filename}", cwd=self.work), content)
    self.assertEqual(before, self.run_git("rev-parse", "HEAD", cwd=self.work))
    self.assertEqual(self.run_git("status", "--porcelain", cwd=self.work), "")

  def test_same_line_conflict_is_reported_without_modifying_checkout(self):
    self.commit(self.origin, "shared.txt", "custom value\n", "custom")
    self.commit(self.source, "shared.txt", "upstream value\n", "upstream")
    state = sync.analyse()
    self.assertEqual(state["state"], "conflict")
    self.assertIn("shared.txt", state["conflicts"])
    self.assertEqual(self.run_git("status", "--porcelain", cwd=self.work), "")

  def test_delete_modify_conflict_is_not_automatically_resolved(self):
    self.run_git("rm", "shared.txt", cwd=self.origin)
    self.run_git("-c", "user.name=Sync Test", "-c", "user.email=sync-test@example.invalid", "commit", "-m", "remove", cwd=self.origin)
    self.commit(self.source, "shared.txt", "new value\n", "upstream")
    self.assertEqual(sync.analyse()["state"], "conflict")

  def test_conflict_creates_report_but_never_enables_validation_or_merge(self):
    state = {"state": "conflict", "source": "s", "base": "b", "conflicts": ["shared.txt"]}
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "ensure_pull", return_value={"number": 7, "html_url": "https://example.invalid/pr/7"}), patch.object(sync, "emit") as emit:
      sync.prepare(False)
    emit.assert_called_once_with(ready=False, pr=7, conflict=True)


def pull():
  return {
    "state": "open", "draft": False, "mergeable": True, "merge_commit_sha": "merge",
    "base": {"repo": {"full_name": sync.REPOSITORY}, "ref": sync.TARGET, "sha": "base"},
    "head": {"repo": {"full_name": sync.SOURCE}, "ref": sync.SOURCE_BRANCH, "sha": "source"},
  }


class MergeGateTest(unittest.TestCase):
  def test_existing_automatic_pr_explicitly_refreshes_the_same_base(self):
    pr = pull()
    pr.update(number=7, title=sync.TITLE)
    with patch.object(sync, "api", side_effect=[[pr], pr]) as api:
      sync.ensure_pull({"state": "ready", "source": "source", "base": "base"})
      self.assertEqual(api.call_args.kwargs["method"], "PATCH")
      self.assertEqual(api.call_args.kwargs["data"]["base"], sync.TARGET)

  def test_rejects_changed_head_base_draft_closed_or_wrong_repository(self):
    changes = [
      lambda p: p["head"].update(sha="new-source"),
      lambda p: p["base"].update(sha="new-base"),
      lambda p: p["base"].update(ref="another-branch"),
      lambda p: p["head"]["repo"].update(full_name="someone/else"),
      lambda p: p.update(draft=True),
      lambda p: p.update(state="closed"),
    ]
    for change in changes:
      with self.subTest(change=change):
        pr = pull()
        change(pr)
        with patch.object(sync, "api", return_value=pr) as api:
          with self.assertRaises(RuntimeError):
            sync.finish(1, "base", "source", "merge")
          self.assertEqual(api.call_count, 1)

  def test_rejects_stale_merge_candidate(self):
    pr = pull()
    pr["merge_commit_sha"] = "new-merge"
    with patch.object(sync, "api", return_value=pr) as api:
      with self.assertRaises(RuntimeError):
        sync.finish(1, "base", "source", "merge")
      self.assertEqual(api.call_count, 1)

  def test_rechecks_both_live_branch_heads_after_ci(self):
    for base, source in [("changed", "source"), ("base", "changed")]:
      with self.subTest(base=base, source=source), patch.object(sync, "api", side_effect=[pull(), {"object": {"sha": base}}, {"object": {"sha": source}}]) as api:
        with self.assertRaises(RuntimeError):
          sync.finish(1, "base", "source", "merge")
        self.assertEqual(api.call_count, 3)

  def test_success_uses_normal_merge_with_pinned_head(self):
    with patch.object(sync, "api", side_effect=[pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, {"merged": True, "sha": "final"}]) as api:
      sync.finish(7, "base", "source", "merge")
      self.assertEqual(api.call_args.kwargs["method"], "PUT")
      self.assertEqual(api.call_args.kwargs["data"]["sha"], "source")
      self.assertEqual(api.call_args.kwargs["data"]["merge_method"], "merge")

  def test_api_merge_rejection_is_a_failure(self):
    with patch.object(sync, "api", side_effect=[pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, {"merged": False, "message": "branch protected"}]):
      with self.assertRaisesRegex(RuntimeError, "branch protected"):
        sync.finish(1, "base", "source", "merge")


if __name__ == "__main__":
  unittest.main()
