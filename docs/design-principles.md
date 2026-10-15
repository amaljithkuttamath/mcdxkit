# Product and engineering principles

MCDXKit turns source analysis reports and an engineer's formula template into inspectable, executable calculation packages. Design for the engineer importing, checking, revising and issuing that work. A successful software run is not an engineering approval.

## Show the evidence first

The actual source, template and generated file are the main content. Keep the selected template and an explicit way to open it available throughout the workflow. Put summaries beside the file; never replace the file with a fabricated dashboard. A reconstructed document view must identify unsupported regions and distinguish saved values from fresh calculations.

Use the sequence **Files → Inputs → Changes → Outputs**. State the current step, what needs attention and the next action. Let the engineer revisit inputs. Keep errors next to the input that caused them; preserve entered values. Opening a document on a phone must be intentional, with an obvious return control and restored keyboard focus.

## One calculation, several interfaces

CLI, SDK, browser and future desktop/cloud workers call the same engine. Template equations are authoritative. UI components format data and request operations; they do not reimplement design equations. A future 3D view consumes the same versioned inputs/results. Do not animate deformation without actual response data.

Reuse existing modules and established libraries before building a parser, renderer, solver or framework. Record the chosen dependency's license, pinned version, supported platforms, offline behavior and the gap it fills. Avoid a new service or repository until its operational responsibility requires it.

## Keep a trace from source to result

Every mapped load retains units, load basis, case/pile provenance and source identity. Geometry, material properties and design assumptions come from explicit sources or recorded overrides. Independent envelope extrema are not simultaneous load combinations. Missing data must stay missing; never substitute plausible values to produce a pass.

Preserve originals and completed revisions. Stage artifacts before publication, use an explicit completion record, and never overwrite an issued result. Resume requires matching source, template, options and engine versions plus verified artifact hashes.

## Tell the truth about state

Keep these claims distinct: package structure checked, formulas calculated by CalcpadCE, native Prime execution verified, and reviewed by an engineer. Unsupported syntax, absent design basis, calculation error and failed design checks need different states. A failed design check may be a correctly calculated result.

Progress belongs to the running app. Count completed files only; use an indeterminate indicator while a single file is calculating. Never simulate percentages or embed progress in engineering outputs. Present partial batch failures with the successful files still available.

## Local use stays first-class

Once the calculator and assets are installed, ordinary calculation must not need a login, CDN, model API or cloud connection. Network access during installation is a separate requirement. Cloud deployment is an optional delivery surface; it must not become an engine dependency. Private engineering documents are not telemetry or public fixtures.

The present hosted app is one trusted shared workspace. Horizontal scaling requires durable jobs, explicit project ownership, isolated execution and artifact storage. A load balancer alone does not provide those properties. Record the supported mode and limits in every deployment guide.

## Make changes reviewable

Use a ticket, a scoped branch and a pull request. Test the behavior and its failure boundaries with independently expected results. Use real-engine tests for numerical changes, browser verification for interactions and installed-package/container checks for delivery. Publish only after checks pass on the current revision. Record unverified platforms and runtimes plainly.

For UI details use [the visual design guide](ui-design.md); for code boundaries use [architecture](architecture.md); for acceptance evidence use [the testing contract](testing.md). Update these decisions when a change intentionally alters them.

## Conversation as part of the work

Keep Ask one click away in primary navigation. It understands the user's files and recorded workflow, not just question text. Show real search/validation activity as it occurs, preserve conversational context across follow-ups and refreshes, and explain gaps without invented confidence percentages. Do not stream private reasoning or present unfinished model text as verified evidence.

Pause for the person when intent, applicability or input decisions are missing. Keep the question and choices in context; accept free text too. Distinguish a suggested action from an executed action. Use existing review/mapping controls to change engineering inputs, with the original source a click away. Keep sources in an on-demand drawer and calculations/tasks at the center.
