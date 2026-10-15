# Documentation

## Use mcdxkit

| Start here | What it covers |
| --- | --- |
| [CLI and browser](cli.md) | Install, calculator setup, inspect, convert, batch/resume, [summary CSV](cli.md#summary-csv-across-reports), server startup and configuration |
| [Machine learning](machine-learning.md) | Offline case classifier, advisory review flags and governing cases |
| [Python SDK](sdk.md) | Imports, function contracts, output artifacts and error handling |
| [Calculation and compatibility](calculation.md) | Supported expressions, load basis, execution evidence, [check results](calculation.md#check-results) and browser inspection |
| [Template contract](../references/template-contract.md) | Required worksheet profile, geometry, inputs and overrides |
| [Agent integration](agent-integration.md) | Use the portable [skill](../SKILL.md) with different coding agents |
| [Document workflow](document-workflow.md) | Project documents, Ask, requirement extraction and review |
| [Answer checks and audit receipts](answer-audit.md) | What Ask publishes, its limits, and the downloadable receipt |
| [Use your ChatGPT plan](chatgpt.md) | Sign in with ChatGPT for Ask on a local server |
| [Docker and hosting](../deploy/README.md) | Images, Compose, authentication, HTTPS and persistent storage |

## Build and contribute

[Contributing](../CONTRIBUTING.md) · [Architecture](architecture.md) · [Repository standards](standards.md) · [Testing contract](testing.md) · [Release process](releasing.md) · [Agent instructions](../AGENTS.md)

[GitHub roadmap and workflow](github-workflow.md) · [Security reporting](../SECURITY.md)

## Research and planned work

These describe direction and requirements, not completed features:

- [Mathcad Prime feature review](mathcad-prime-review.md): product coverage and the independent calculation architecture.
- [Engineering standards register](engineering-standards.md): proposed per-value sources, applicable codes, editions and rule validation.
- [Ask retrieval and answer evaluation](retrieval-evaluation.md): gold labels, per-stage metrics, first-loss attribution, replay and the acceptance rule for Ask pipeline changes.
- [Template-driven 3D reuse research](research/template-driven-3d.md): existing open-source implementations, license considerations and prototype acceptance criteria.

[Engineering workflow and offline project plan](engineering-workflow.md) records the source-to-review product direction and separates shipped capabilities from future acceptance gates.

[Product and engineering principles](design-principles.md) explain workflow, evidence, calculation and offline boundaries for contributors.
