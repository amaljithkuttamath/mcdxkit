# Contributing

Start with [AGENTS.md](AGENTS.md), [the architecture](docs/architecture.md) and the [template contract](references/template-contract.md). Use an issue or pull request to describe the concrete behavior being changed, its compatibility scope and how it was verified.

## Tickets, branches and review

1. Open or choose a GitHub issue. Define the problem, acceptance criteria, scope and validation approach. Use the feature or bug form and sanitized synthetic examples.
2. Claim the issue or leave a short implementation note. Break large features into independently reviewable tickets, linking dependencies. Do not silently expand the ticket's scope.
3. Fork the repository if you do not have write access. Start from current `main` and create `feat/<issue>-<description>`, `fix/<issue>-<description>` or `chore/<issue>-<description>`.
4. Implement and run the relevant checks below. Commit the code, tests and documentation together. Open a draft PR early when coordination helps.
5. Link the issue using `Closes #<issue>` and complete the PR template. Resolve review comments and required checks. Branch updates must include the current base when GitHub requires it.
6. A maintainer merges the ready PR using squash merge. Never force-push or delete `main`. The merged commit triggers package/image publication; the linked issue closes automatically.

```bash
git switch main
git pull --ff-only
git switch -c feat/123-descriptive-name
# Make changes, run checks, then commit.
git push -u origin feat/123-descriptive-name
gh pr create --draft --base main
```

For forks, `origin` is your fork and `upstream` is this repository; synchronize your local base from `upstream/main`. Write access is not required to open an issue or propose a PR from a fork. Fork PRs run CI without access to publishing credentials.

Use issue/PR state as the work queue: open issue → claimed work/branch → draft PR → ready for review → merged/closed. Labels describe area or type; an open PR links implementation to its ticket. Required CI protects `main`; maintainers remain responsible for reviewing calculation semantics and acceptance evidence.

## Development setup

Read [design principles](docs/design-principles.md) before changing product behavior or adding a subsystem. The [UI guide](docs/ui-design.md) covers interaction and visual requirements.

Requirements: Python 3.10+, Git; .NET 10 SDK for the initial calculator build; Node.js for JavaScript syntax checks. Docker is needed for container verification.

```bash
git clone https://github.com/amaljithkuttamath/mcdxkit.git
cd mcdxkit
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
mcdxkit setup-engine
mcdxkit serve --no-open --port 0 --output-dir ./mcdxkit-output
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`; the remaining `python -m pip` and `mcdxkit` commands are the same. Use `python` instead of `python3` when that is your Python 3 command. Node 22 is the browser-test baseline. Install the .NET 10 SDK before `setup-engine`; the generated calculator is reused across later runs.

A successful contributor setup prints the server URL, opens the project workspace, and passes the synthetic tests without a private template. Use `python -m mcdxkit --help` to confirm the active environment if a different installed CLI is found. Stop only the server you started and preserve its output directory.

Select your own compatible template in the browser. There is no public production template or engineering dataset. Tests generate synthetic inputs and do not need a private template. `setup-engine` builds into a persistent cache and refuses to overwrite it. If already installed, skip setup or build into a new explicit directory.

## Tests and checks

The [testing contract](docs/testing.md) maps each change type to required positive/negative cases. [Repository standards](docs/standards.md) define engineering semantics, code boundaries, data handling and the definition of done. These apply equally to human and coding-agent contributions.

```bash
python -m unittest discover -s tests -v
node --check src/mcdxkit/web/app.js
node --test tests/*.test.cjs
python -m pip install -r requirements-dev.txt
ruff check src tests scripts
mypy
python -m pip install build
python -m build
python scripts/check_package.py
git diff --check
```

Use focused tests while iterating, then run the full suite for changes spanning parsing, calculation, packaging or HTTP behavior. Real-engine tests need the installed calculator; HTTP tests bind loopback. Keep fixtures synthetic. Add behavior-oriented cases for changed unit rules, source selection, unsupported syntax, result validity or publication failure. Do not test only that code reproduces itself.

For container changes:

```bash
docker build -t mcdxkit:test .
docker run --rm -v "$PWD:/verify:ro" --entrypoint python \
  mcdxkit:test /verify/scripts/test_installed.py --tests /verify/tests
```

The Actions workflow also starts the container, checks health, checks bundled fonts and confirms that unauthenticated API access is rejected.

For UI changes, use the real browser flow: upload → inputs → changes → generated outputs, then inspect the original file and fresh calculation view. Check the affected errors, keyboard focus and narrow layout. A rendered screenshot or syntax check alone does not establish a working conversion.

For progress feedback, test pending requests, mixed success/failure, retries and cleanup after errors. Advance counts only on observed completion; do not simulate equation percentages or ETAs. Keep UI progress outside generated `.mcdx`, `.cpd`, result HTML and audit data. Browser orchestration tests use controlled HTTP responses; retain real-engine checks and actual browser verification as separate evidence.

## Change expectations

- Reuse existing modules and established open-source implementations before rebuilding. Record candidates, licenses, offline requirements and any concrete gap in the issue/PR. New solvers need separate numerical validation; a renderer must consume shared results rather than reproduce template formulas.
- Keep domain calculations and file semantics outside HTTP/UI glue. Reuse the common engine.
- Fail visibly on unsupported expressions or incompatible units. Preserve original source and governing-case provenance.
- Keep old caches distinct from calculated results. Never change native verification flags without actual Prime evidence.
- Update CLI help/docs for new flags; update `SKILL.md` when agent workflow changes.
- Keep [skill reviews](references/reviews.md) consistent with repository standards. The optional [installation interview](docs/agent-integration.md#install-with-optional-workflow-interview) must remain agent-neutral, skippable and local; preferences cannot count as validation evidence or change calculation defaults.
- Add packaged assets to both package metadata and Docker's context allowlist. Include licenses for bundled dependencies/assets.
- Update dependency locks intentionally. Pin the calculator revision and review its behavior when upgrading. Do not silently fetch newer engines during conversion.
- Exclude reports, templates, source snapshots, credentials and generated engineering outputs from commits and build contexts. Review staged filenames before publishing.

## Pull requests and releases

Add a line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for any change a user, integrator or deployer would notice. Internal refactors and test-only changes can skip it.

Describe the problem, resulting behavior, validation and remaining limitations. Separate current features from roadmap work. Include any template or expression compatibility changes so reviewers can assess existing documents.

Every pull request runs checks. Pushes to `main` and manual `main` runs publish Python build artifacts and, after all image tests succeed, a GHCR container. Image tags are `latest` and `sha-<full-commit-sha>`; use the latter for repeatable deployment. Actions uses `GITHUB_TOKEN`, not a repository-stored personal token. Dependabot maintains action update proposals.

When intentionally releasing a new application version, keep `pyproject.toml` and the version in `src/mcdxkit/cli.py` aligned, and move the `Unreleased` changelog entries under the new version heading. Check the workflow for the exact commit before claiming the package/image is published. Deployment to a live server is a separate operation; follow [deployment guidance](deploy/README.md).

Versioned Python releases use the separate [PyPI release workflow](docs/releasing.md), which tests the installed wheel before Trusted Publishing. Ordinary main CI artifacts are not a PyPI upload.

See [GitHub workflow](docs/github-workflow.md) for the roadmap, triage and security controls. Contributions are distributed under the repository's [MIT license](LICENSE); dependencies keep their own terms and attribution.

After **every push**, inspect the workflows for that exact commit SHA, including branch updates and post-merge publication. Follow pending runs to completion, read failed job logs, fix regressions and check the replacement commit. Never use an earlier green run as evidence for a newer push. Record the run URL and any explicitly unverified checks in the PR handoff.

## Continuous improvement

The [repository audit](docs/improvement-loop.md) runs after releases and weekly. Work evidence-backed findings through issue branches and PRs. Check for an existing matching issue first, record the affected commit and reproduction, distinguish infrastructure failures from product defects, and add a regression test for changed behavior. Passing software checks never grant engineering approval. Do not mark an issue done until acceptance evidence and exact-head CI, including post-merge publishing, pass.

For document-authoring contributions, include tests for cited source ownership, review staleness, unit compatibility and free-provider policy. Preserve ordinary offline calculation. Use synthetic source material in protocol/browser tests, and report live RAGFlow/OpenRouter runs separately. Read [document workflow](docs/document-workflow.md).

## Project workspace development

Use Node 22.12+ and the committed npm lockfile:

```bash
npm ci --prefix frontend --ignore-scripts
npm test --prefix frontend
npm run build --prefix frontend
```

Commit `frontend/` source and regenerated `src/mcdxkit/web/workspace-dist/` together. Do not hand-edit generated assets. CI rebuilds and checks for drift. The build collects production dependency licenses, including the React client-only marker's upstream notice.

For conversational changes, test follow-up scope, absent evidence, foreign citations, bounded calls, human clarification and action availability. For workers, test deduplication, interruption, cancellation fencing and responsive reads during remote work. Synthetic fixtures are required in Git; use existing private documents for local acceptance when authorized. Live model/browser checks are separate from deterministic tests and never substitute for engineering review.
