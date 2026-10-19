# MCDXKit

**Open-source tools for Mathcad workflows.**

[![PyPI](https://img.shields.io/pypi/v/mcdxkit?color=2563eb)](https://pypi.org/project/mcdxkit/)
[![CI](https://github.com/amaljithkuttamath/mcdxkit/actions/workflows/test.yml/badge.svg)](https://github.com/amaljithkuttamath/mcdxkit/actions/workflows/test.yml)

Convert ENSOFT GROUP reports into executable Calcpad worksheets, calculated results and native Mathcad formula packages. One engine powers the CLI, Python SDK and local browser.

[Documentation](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/README.md) · [Python SDK](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/sdk.md) · [Agent skill](https://github.com/amaljithkuttamath/mcdxkit/blob/main/SKILL.md) · [Contributing](https://github.com/amaljithkuttamath/mcdxkit/blob/main/CONTRIBUTING.md)

- **Calculate from source.** Read final local-load summaries and execute supported equations with CalcpadCE.
- **Inspect the work.** Open source files, review input changes and view fresh results in the browser.
- **Run a pipeline.** Convert folders with bounded workers, audit trails and verified resume.

## Quickstart

Requires Python 3.10+. The one-time calculator build needs Git and the .NET 10 SDK. Bring a [compatible private template](https://github.com/amaljithkuttamath/mcdxkit/blob/main/references/template-contract.md).

```bash
# In your Python environment
pip install mcdxkit
mcdxkit setup-engine

mcdxkit convert report.gp11t --template reference.mcdx \
  --output results/design.mcdx
```

Each conversion produces **`.cpd`** (executable worksheet), **`.html`** (calculated results), **`.mcdx`** (native formulas) and **`.audit.json`** (provenance). Existing files are preserved.

## Browser or batch

```bash
mcdxkit serve

mcdxkit batch ./reports --recursive --template reference.mcdx \
  --output-dir ./results --workers 4 --resume
```

The browser guides **Files → Inputs → Changes → Outputs**. Select your report and template, inspect the source, then calculate. [CLI guide](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/cli.md) · [Docker deployment](https://github.com/amaljithkuttamath/mcdxkit/blob/main/deploy/README.md)

Built-in [machine learning](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/machine-learning.md), offline with no extra dependencies: a case-name classifier that learns your naming from completed conversions (**Suggest cases**, `mcdxkit inspect`), plug-in advisory review checks (`mcdxkit checks list`) for mislabelled cases, misplaced or copied tables, single wrong values and possible unit slips, a hint for strength cases that can never govern, and the governing case behind every envelope value.

ML is advisory only, never applied without your click: it never changes inputs, case selection or outputs unless you select **Use cases …**.

## Python SDK

```python
from mcdxkit.engine import convert

result = convert("report.gp11t", "reference.mcdx", "results/design.mcdx")
print(result["calculated_worksheet"])
```

Use the same engine from scripts, jobs or agents. See the [SDK contract](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/sdk.md) and [portable skill setup](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/agent-integration.md).

## Current scope

Supports GROUP `.gp11t`/text reports in kip/in and compatible scalar templates: use the fixed-head preset or explicitly map detected inputs to report components. CalcpadCE executes translated formulas; **Prime-native execution remains unverified**. Unsupported equations fail explicitly. Private engineering files stay out of the distribution. [Calculation and compatibility details](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/calculation.md)

## Contribute

Pick an [issue](https://github.com/amaljithkuttamath/mcdxkit/issues), work on a branch, add meaningful tests and open a PR. [Contribution guide](https://github.com/amaljithkuttamath/mcdxkit/blob/main/CONTRIBUTING.md) · [Architecture](https://github.com/amaljithkuttamath/mcdxkit/blob/main/docs/architecture.md) · [Coding-agent guide](https://github.com/amaljithkuttamath/mcdxkit/blob/main/AGENTS.md)

[MIT licensed](https://github.com/amaljithkuttamath/mcdxkit/blob/main/LICENSE). Dependencies retain their [own licenses](https://github.com/amaljithkuttamath/mcdxkit/blob/main/THIRD_PARTY.md).
