# Development workflow

`main` is the stable default branch. The original `feat/flashdrop` branch is retained to preserve existing submission links; new PRs should target `main`.

## Make one coherent change

Inspect the working tree and project documentation before editing. Branch from current `main` using a descriptive prefix such as `feat/`, `fix/`, `docs/`, or `test/`. Keep related unmerged follow-up work on that branch rather than making a branch for every step.

For changes that affect behavior, describe the intended outcome and important tradeoffs before implementing. Record consequential decisions in the relevant architecture or testing document. Fix a bug with a reproduction test where practical, and commit the fix and its tests together. Commit at meaningful verified milestones; do not reconstruct or manufacture a development history after the work is complete.

## Validate

```sh
python -m unittest discover -v
python -m pip install ruff==0.14.3 build==1.3.0
python -m ruff check .
python -m ruff format --check .
python -m build
```

Run runtime checks appropriate to the change. For UI changes, check the browser flow and responsive layout where available; report blocked checks. Run the optional load test only when concurrency, persistence, or load handling changes justify it; instructions and result limits are in [TESTING.md](docs/TESTING.md).

Review the final diff for correctness, security, failure paths, secrets, and unrelated changes. Keep the README, API behavior, architecture, and [current verification summary](docs/AUDIT.md) consistent. Label production proposals and unexecuted checks clearly.

## Open a PR

Push the working branch and open one focused PR targeting `main`. Use a Conventional Commit title and the supplied description template. Include validation actually performed, important risks, and rollback or follow-up steps. Inspect CI for the actual head commit before declaring it ready.

Do not merge without explicit maintainer approval. An open PR does not mean its changes are on `main`. Keep private notes, credentials, and local environment files out of commits. Do not rewrite published history or force-push without authorization.
