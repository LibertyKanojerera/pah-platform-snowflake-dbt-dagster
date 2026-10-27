# ADR-010: The silent quality gate

## Status

Accepted 2026-09-24. Amended 2026-10-25 with two further instances.

## Context

Four times across two builds, a quality control reported success while operating on nothing. In
every case the output was indistinguishable from a real pass, and in every case the failure was
found by accident rather than by the control.

**Instance 1, Databricks build.** The Bronze layer created a lowercased `tot_benes` column, then
called `.drop("Tot_Benes")`. Spark resolves column names case-insensitively by default, so the
drop removed both columns. Silver then failed looking for a count that Bronze had silently
deleted. The pipeline was green throughout. Diagnosis took far longer than the fix, because the
symptom looked like a stale version rather than a logic error, and `git pull` kept not helping.

**Instance 2, 2026-09-24.** The git repository root sat one directory above the project. Pre-commit
matches its `files:` regexes against paths relative to the **repository** root, so it saw
`pah-platform-.../ingest/core/http.py`, which does not match `^ingest/`. The mypy and sqlfluff
hooks reported `(no files to check) Skipped` while real Python and SQL files were staged. The
regexes were correct. The inputs were empty.

One root cause produced two symptoms: a clone that nests a directory inside a directory of the
same name, which is cosmetic and visible, and quality gates checking nothing, which is serious
and invisible. The visible one was noticed first by luck.

**Instance 3, 2026-10-25.** `.gitignore` existed and was zero bytes. `.env` was tracked in git as
a result. Two layers of protection agreed that everything was fine: the file looked present, and
the `detect private key` hook passed, because `.env` was itself empty and there was nothing to
detect. Nothing leaked only because no credentials had been created yet. The AWS account was
created ninety minutes later.

`.venv` escaped being committed not because of any rule but because `uv` writes a `.gitignore`
containing `*` inside the environment it creates. A dependency's courtesy was standing in for
the project's own ignore file.

**Instance 4, 2026-10-25.** A commit with the message `fix: populate empty .gitignore, untrack .env`
recorded `1 file changed, 0 insertions(+), 0 deletions(-)` and only a deletion. The populate step
had been a comment inside a pasted block, not a command. Every hook ran and passed, because the
only staged change was a deletion and there was nothing to lint. The commit message was a plain
statement of work that had not happened.

## Decision

**Treat "passed" and "skipped" as different results, and never read the second as the first.**

Concretely:

1. **Prove a gate can fail before trusting it.** After configuring or repointing a checker,
   introduce a deliberate violation, confirm it fails, remove it. Thirty seconds. The mypy hook
   was only confirmed working after it moved from `Skipped` to `Passed` on an unchanged
   configuration.
2. **A control that is present but empty is worse than one that is absent**, because it stops
   anyone looking. Verify positively: `git check-ignore -v .env` must name the ignoring file.
   A green hook log is not that proof.
3. **Audit for hollow files, not just missing ones.** `find . -empty -type f` after scaffolding.
   An empty `.gitignore`, an empty `docker-compose.yml` and a placeholder module all look alike
   on a directory listing.
4. **Check that a commit changed what its message claims.** `git show --stat` before pushing when
   the commit matters. Two commit messages in this project describe work their diffs do not
   contain.
5. **When a tool reports nothing to do, ask why there is nothing**, rather than accepting it.
   That question would have caught instances 2, 3 and 4 immediately.

## Consequences

**A short verification step after any gate is configured.** Small, and it is the only thing that
distinguishes a working control from a decorative one.

**Material for the migration write-up.** All four instances share one shape: the control ran, the
control passed, the control was looking at nothing. The Spark bug and the pre-commit bug are the
same failure in different tools, one in a data pipeline and one in a build pipeline, which makes
the pair more interesting than either alone.

The sentence the write-up turns on: **a quality gate that cannot fail is indistinguishable from
one that passed.**

**A point about automation generally.** Three of these four were introduced by a tool doing
something reasonable on the author's behalf: `uv init --package` creating a `src/` layout that
competed with the real packages, a scaffolding step producing files that existed but were empty,
and a dependency shipping its own ignore rules. None was a mistake by the tool. Each became a
problem because its output was assumed rather than inspected.
