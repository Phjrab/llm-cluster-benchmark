# Rollback

The implementation is a local feature-branch commit after baseline `9245e24`,
followed by an evidence/documentation commit. Exact implementation SHA is recorded
in [HANDOFF.md](HANDOFF.md) and all after rows of the capture manifest.

Review a clean working tree and the branch's own commits first:

```bash
git status --short
git log --oneline 9245e24..codex/ui-llm-cluster-research-lab
```

To revert the product change, use `git revert <implementation-sha>` on the chosen
branch. To remove its documentation/captures as well, revert the evidence commit
first, then implementation. Substitute only the two commits from this task;
review any later dependent changes and resolve conflicts without discarding user edits.

There was no database, model, result, research lock, authentication or persisted
preference migration. Existing stored values and original summaries were not
rewritten. No data restore is needed; old UI uses its original null/identity display
limitations after revert. No service restart, hardware change, destructive clean,
reset or data deletion is part of this rollback procedure.
