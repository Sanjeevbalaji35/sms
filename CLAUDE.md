# Instructions for Claude

Read [AGENTS.md](AGENTS.md) first: it maps the code and its rules.

## Saving work: always push and merge, without asking

After every finished change, do all of this without waiting to be asked:

1. Run the tests: `python tests/run.py`. Only continue if they all pass. If they fail, fix it, or tell the owner
   what failed and don't push.
2. Commit everything (`git add -A`) with a clear message.
3. Push the working branch.
4. Merge it into `main` and push `main`, so `main` always has the latest working version.

Never force-push `main`, and never rewrite its history. If `main` has commits the working branch doesn't, merge
them in first (`git fetch origin main && git merge origin/main`), run the tests again, then push.
