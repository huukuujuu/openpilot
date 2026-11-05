from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
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

  def publish_merge(self, state, *, tree=None, parents=None):
    args = ["-c", "user.name=Sync Test", "-c", "user.email=sync-test@example.invalid", "commit-tree", tree or state["tree"]]
    for parent in parents or [state["base"], state["source"]]:
      args += ["-p", parent]
    commit = self.run_git(*args, "-m", "Simulated server merge", cwd=self.work)
    self.run_git("push", "origin", f"{commit}:refs/heads/simulated-server-objects", cwd=self.work)
    self.run_git("update-ref", "refs/heads/carrot-ev6", commit, cwd=self.origin)
    return commit

  def divergent_state(self):
    self.commit(self.origin, "custom.txt", "personal setting\n", "custom")
    self.commit(self.source, "upstream.txt", "new feature\n", "upstream")
    return sync.analyse()

  def test_recovers_exact_server_merge_including_when_later_commits_exist(self):
    state = self.divergent_state()
    expected = sync.candidate_commit(state["base"], state["source"], state["tree"])
    applied = self.publish_merge(state)
    self.assertEqual(sync.find_applied_merge(state["base"], state["source"], expected), applied)
    self.publish_merge(state, parents=[applied])
    self.assertEqual(sync.find_applied_merge(state["base"], state["source"], expected), applied)

  def test_recovery_rejects_wrong_tree_or_wrong_validated_candidate(self):
    state = self.divergent_state()
    applied = self.publish_merge(state)
    with self.assertRaisesRegex(RuntimeError, "validated candidate"):
      sync.find_applied_merge(state["base"], state["source"], "wrong-candidate")
    wrong_tree = self.run_git("show", "-s", "--format=%T", state["base"], cwd=self.work)
    # Use a separate transfer ref for a deliberately non-fast-forward fixture.
    self.run_git("update-ref", "-d", "refs/heads/simulated-server-objects", cwd=self.origin)
    self.publish_merge(state, tree=wrong_tree)
    with self.assertRaisesRegex(RuntimeError, "unexpected contents"):
      sync.find_applied_merge(state["base"], state["source"])

  def test_recovery_does_not_accept_a_different_parent_pair(self):
    state = self.divergent_state()
    self.publish_merge(state, parents=[state["source"], state["base"]])
    self.assertIsNone(sync.find_applied_merge(state["base"], state["source"]))

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

  def test_candidate_is_reproducible_and_checked_out_only_after_hash_verification(self):
    self.commit(self.origin, "custom.txt", "personal setting\n", "custom")
    self.commit(self.source, "upstream.txt", "new feature\n", "upstream")
    state = sync.analyse()
    first = sync.candidate_commit(state["base"], state["source"], state["tree"])
    self.assertEqual(first, sync.candidate_commit(state["base"], state["source"], state["tree"]))
    self.assertEqual(self.run_git("show", "-s", "--format=%P", first, cwd=self.work).split(), [state["base"], state["source"]])
    self.run_git("checkout", "--detach", state["base"], cwd=self.work)
    with self.assertRaisesRegex(RuntimeError, "commit differs"):
      sync.checkout_candidate(state["source"], state["tree"], "wrong-sha")
    self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=self.work), state["base"])
    sync.checkout_candidate(state["source"], state["tree"], first)
    self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=self.work), first)
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
    "title": sync.TITLE,
    "state": "open", "draft": False, "mergeable": True, "merge_commit_sha": "merge",
    "base": {"repo": {"full_name": sync.REPOSITORY}, "ref": sync.TARGET, "sha": "base"},
    "head": {"repo": {"full_name": sync.SOURCE}, "ref": sync.SOURCE_BRANCH, "sha": "source"},
  }


class MergeGateTest(unittest.TestCase):
  def setUp(self):
    for name in ("find_applied_merge", "reconcile_pull"):
      patcher = patch.object(sync, name, return_value=None)
      patcher.start()
      self.addCleanup(patcher.stop)
    patcher = patch.object(sync.time, "sleep")
    patcher.start()
    self.addCleanup(patcher.stop)

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

  def test_rejects_unmergeable_candidate(self):
    pr = pull()
    pr["mergeable"] = False
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
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    merged = {"tree": {"sha": "tree"}, "parents": [{"sha": "base"}, {"sha": "source"}]}
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "candidate_commit", return_value="merge"), patch.object(sync, "api", side_effect=[pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, {"merged": True, "sha": "final"}, merged]) as api:
      sync.finish(7, "base", "source", "merge")
      merge_call = api.call_args_list[-2]
      self.assertEqual(merge_call.kwargs["method"], "PUT")
      self.assertEqual(merge_call.kwargs["data"]["sha"], "source")
      self.assertEqual(merge_call.kwargs["data"]["merge_method"], "merge")

  def test_api_merge_rejection_is_a_failure(self):
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "candidate_commit", return_value="merge"), patch.object(sync, "api", side_effect=[pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, {"merged": False, "message": "branch protected"}]):
      with self.assertRaisesRegex(RuntimeError, "branch protected"):
        sync.finish(1, "base", "source", "merge")

  def test_recomputed_candidate_mismatch_prevents_merge(self):
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "candidate_commit", return_value="different"), patch.object(sync, "api", side_effect=[pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}]) as api:
      with self.assertRaisesRegex(RuntimeError, "no longer matches"):
        sync.finish(1, "base", "source", "merge")
      self.assertEqual(api.call_count, 3)

  def test_rerun_recovers_applied_merge_before_rejecting_changed_heads(self):
    pr = pull()
    pr["state"] = "closed"
    with patch.object(sync, "api", return_value=pr) as api, patch.object(sync, "find_applied_merge", return_value="already-applied"):
      sync.finish(1, "base", "source", "merge")
      self.assertEqual(api.call_count, 1)
      sync.reconcile_pull.assert_called_once_with(1, "source", "already-applied")

  def test_502_after_success_is_recovered_without_second_merge_request(self):
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    responses = [pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, sync.GitHubAPIError(502, "server error")]
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "candidate_commit", return_value="merge"), patch.object(sync, "api", side_effect=responses) as api, patch.object(sync, "find_applied_merge", side_effect=[None, "applied"]):
      sync.finish(1, "base", "source", "merge")
      self.assertEqual(sum(c.kwargs.get("method") == "PUT" for c in api.call_args_list), 1)
      sync.reconcile_pull.assert_called_once_with(1, "source", "applied")

  def test_503_without_application_retries_only_after_rechecking_both_heads(self):
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    heads = [{"object": {"sha": "base"}}, {"object": {"sha": "source"}}]
    merged = {"tree": {"sha": "tree"}, "parents": [{"sha": "base"}, {"sha": "source"}]}
    responses = [pull(), *heads, sync.GitHubAPIError(503, "unavailable"), pull(), *heads, {"merged": True, "sha": "final"}, merged]
    with patch.object(sync, "analyse", return_value=state) as analyse, patch.object(sync, "candidate_commit", return_value="merge"), patch.object(sync, "api", side_effect=responses) as api:
      sync.finish(1, "base", "source", "merge")
      self.assertEqual(analyse.call_count, 2)
      self.assertEqual(sum(c.kwargs.get("method") == "PUT" for c in api.call_args_list), 2)

  def test_ambiguous_failure_then_branch_change_never_retries_a_write(self):
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    responses = [pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, sync.GitHubAPIError(0, "timeout"), pull(), {"object": {"sha": "changed"}}, {"object": {"sha": "source"}}]
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "candidate_commit", return_value="merge"), patch.object(sync, "api", side_effect=responses) as api:
      with self.assertRaisesRegex(RuntimeError, "branch advanced"):
        sync.finish(1, "base", "source", "merge")
      self.assertEqual(sum(c.kwargs.get("method") == "PUT" for c in api.call_args_list), 1)

  def test_permission_denial_is_not_retried(self):
    state = {"state": "ready", "base": "base", "source": "source", "tree": "tree"}
    responses = [pull(), {"object": {"sha": "base"}}, {"object": {"sha": "source"}}, sync.GitHubAPIError(403, "denied")]
    with patch.object(sync, "analyse", return_value=state), patch.object(sync, "candidate_commit", return_value="merge"), patch.object(sync, "api", side_effect=responses) as api:
      with self.assertRaises(sync.GitHubAPIError):
        sync.finish(1, "base", "source", "merge")
      self.assertEqual(sum(c.kwargs.get("method") == "PUT" for c in api.call_args_list), 1)


class ReconcileTest(unittest.TestCase):
  def test_closes_stale_pr_with_a_receipt_and_is_idempotent(self):
    pr = pull()
    closed = {**pr, "state": "closed"}
    with patch.object(sync, "api", side_effect=[pr, closed, closed]) as api:
      sync.reconcile_pull(2, "source", "applied")
      data = api.call_args_list[1].kwargs["data"]
      self.assertEqual(data["state"], "closed")
      self.assertIn("EV6-SYNC-APPLIED:source", data["body"])
    with patch.object(sync, "api", return_value=closed) as api:
      sync.reconcile_pull(2, "source", "applied")
      self.assertEqual(api.call_count, 1)

  def test_new_source_or_unmanaged_pr_is_never_closed(self):
    for key, value in [("head", {**pull()["head"], "sha": "new-source"}), ("title", "manual PR")]:
      pr = {**pull(), key: value}
      with self.subTest(key=key), patch.object(sync, "api", return_value=pr) as api:
        sync.reconcile_pull(2, "source", "applied")
        self.assertEqual(api.call_count, 1)

  def test_source_advance_racing_cleanup_reopens_pr(self):
    pr = pull()
    changed = {**pr, "state": "closed", "head": {**pr["head"], "sha": "new-source"}}
    with patch.object(sync, "api", side_effect=[pr, changed, changed, {}]) as api:
      sync.reconcile_pull(2, "source", "applied")
      self.assertEqual(api.call_args.kwargs["data"], {"state": "open"})

  def test_cleanup_outage_does_not_erase_verified_merge_success(self):
    with patch.object(sync, "api", side_effect=sync.GitHubAPIError(502, "unavailable")), patch.object(sync, "summary") as summary:
      sync.reconcile_pull(2, "source", "applied")
      self.assertIn("cleanup is pending", summary.call_args.args[0])

  def test_noop_check_repairs_only_proven_managed_prs(self):
    pr = {**pull(), "number": 2}
    with patch.object(sync, "api", return_value=[pr, {**pr, "title": "manual"}]), patch.object(sync, "find_applied_merge", return_value="applied") as find, patch.object(sync, "reconcile_pull") as reconcile:
      sync.reconcile_current_pulls()
      find.assert_called_once_with("base", "source")
      reconcile.assert_called_once_with(2, "source", "applied")


class APITransportTest(unittest.TestCase):
  def test_non_json_502_write_is_typed_and_never_replayed(self):
    error = urllib.error.HTTPError("https://api.github.com/test", 502, "bad gateway", {}, io.BytesIO(b"<html>bad gateway</html>"))
    with patch.dict(os.environ, {"GITHUB_TOKEN": "test-only-token"}), patch.object(sync.urllib.request, "urlopen", side_effect=error) as request:
      with self.assertRaises(sync.GitHubAPIError) as raised:
        sync.api("test", method="PUT", data={})
      self.assertEqual(raised.exception.status, 502)
      self.assertNotIn("test-only-token", str(raised.exception))
      self.assertEqual(request.call_count, 1)

  def test_transient_read_is_retried(self):
    error = urllib.error.HTTPError("https://api.github.com/test", 503, "unavailable", {}, io.BytesIO(b'{}'))
    with patch.dict(os.environ, {"GITHUB_TOKEN": "test-only-token"}), patch.object(sync.time, "sleep"), patch.object(sync.urllib.request, "urlopen", side_effect=[error, io.BytesIO(b'{"ok":true}')]) as request:
      self.assertEqual(sync.api("test"), {"ok": True})
      self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
  unittest.main()
