# Worksheet review interface

## Brief

A local engineering conversion tool. Engineers need to inspect their raw report, review selected loads and inherited worksheet assumptions, compare changes, then inspect and download executable files. Keep the worksheet central. The visual direction is minimal enterprise with restrained monochrome depth, informed by the user's CRED reference.

## Tokens

- Ink `#000000`: top-level header.
- Graphite `#22252B`: controls surface.
- Paper `#FFFFFF`: actual worksheet and document controls.
- Canvas `#E4E7EB`: surrounding document area.
- Secondary text `#AEB4BF`: readable text on graphite.
- Focus `#365D9F`: keyboard focus and selection boundaries.

IBM Plex Sans, locally bundled under the SIL Open Font License, is the interface family. Use 12px metadata, 12–14px controls/body, 20px tabular load values and 24px section titles. Native document typography and formatting remain unchanged. Source/XML content alone uses monospace. Labels and prose align left; comparable numeric values align right.

## Layout comparison

A generic dashboard puts summaries ahead of source evidence:

    [ status ][ count ][ metric ]
    [            cards         ]

Use a document review desk instead:

    [ brand                         saved outputs ]
    [ workflow ][ file title / document views     ]
    [ inputs   ][ pages ][ actual worksheet      ]
    [ next step][       ][                       ]

The numbered workflow is a real sequence. The page rail contains actual document thumbnails. On small screens, the document occupies the screen with an explicit return-to-inputs control; controls must remain reachable and touch-sized.

## Review against the brief

The earlier design used monospace metadata everywhere, repeated borders and badges, dot-separated labels, arrow-suffixed actions and undersized text. Those conventions were generic decoration rather than useful engineering information. Remove them. Retain boundaries only where they separate the input workflow from the document or show selected state. Keep the main worksheet as the single dominant element; do not introduce a promotional hero, fake metrics or decorative drawings.

Case selection and implementation details stay behind disclosures. Status text describes completed operations, never engineering approval. Saved Mathcad results remain distinct from fresh Calcpad calculations. Errors explain the next action.

## Interaction and verification

Use 44px touch targets where controls are operated, visible keyboard focus, tabular numbers and at least 4.5:1 text contrast. Buttons use the supplied 0.96 press scale, except static high-frequency page controls. Interactive transitions use `cubic-bezier(0.2, 0, 0, 1)`, at most 150ms, and disable under reduced motion. No page-load animation.

Verify desktop and mobile, input and empty states, keyboard focus, selected/disabled controls, errors, loading feedback, thumbnail navigation and search. The pasted ui-ux-pro-max database scripts and linked better-ui recipes were not supplied or installed; this implementation uses the provided prose rules, not a claimed database match.

## Document-authoring workspace

Both calculation and document workspaces follow the user’s updated Apple-inspired direction: system typography (`-apple-system` first), pale neutral surfaces, restrained translucency in the toolbar, soft depth and blue actions. This supersedes the earlier Plex/graphite direction for application chrome; preserve original document typography. Keep a compact file rail and a large document canvas. Project creation and service credentials belong in labelled modal dialogs, not long sidebar forms. The document library starts with Sources and Answers, without calculation prerequisites. Connecting calculation files unlocks Design basis and Package. The calculation workspace remains a visible entry/return point; do not present document drafting as a replacement calculation engine.

Use the single blue action colour for draft/review actions, distinguish original PDF selection in the file rail, and keep unresolved evidence visible in the draft. Empty states provide a working file action. Modal errors stay visible inside the active dialog, entered values survive errors, and Escape/close restores focus.

## Primary path and Ask (workspace, October 2026)

The engineer supplies two files: a raw load report (`.gp11t` or `.txt`) and a Mathcad template (`.mcdx`). The workspace makes that path primary:

- The sidebar groups **Calculation** (Inputs, Worksheet, Outputs) separately from **Reference documents** (Requirements). A project with no connected calculation opens on Inputs unless the URL names a view.
- A project holds many calculations. Once one exists, the task bar shows a **Calculation** picker (newest selected by default, written to the URL as `calc=`) with **+ New calculation**. Inputs, Worksheet, Outputs, the run selector, Calculate and the design-basis binding all follow the selected calculation; the Outputs badge counts only its runs.
- Inputs shows one slot per file, each with its own picker (report pickers accept only report extensions; template pickers only `.mcdx`), then a one-line status: select cases, checking, resolve items, or ready to connect. The template slot also offers **From template library…**; an uploaded template is added to the library first.
- Each mapped value shows its governing load case under it, and a note states that extremes from different cases are not a concurrent load combination.
- Outputs toggles between **Calculated report** (the CalcpadCE result) and **Mathcad file**, a read-only render of the exact `calculation.mcdx` the run delivers, served only after its package hash verifies. It shows the inserted load summary and the mapped inputs that now refer to it. Download buttons are labelled **Download …** so they are not confused with the views.
- **Templates** sits beside Ask in the sidebar and works without a project. It lists library templates (version, input count, calculations using it); the selected template shows its discovered inputs and its rendered worksheet, with **Use in new calculation** and **Archive**.
- The task bar shows four measured steps (raw report, Mathcad template, inputs connected, calculated) with the current step marked. These reflect recorded state only; they never imply native Mathcad execution or engineering approval.
- The header's primary button is the next action: Add raw report, Add Mathcad template, Check inputs, or Calculate. It is hidden when no project is selected or when the next action is Connect on the Inputs view itself.

**Ask is a separate destination, not a step.** Its sidebar entry sits above the calculation group and is reachable from every view, including before any upload, during calculation and with no project selected. Without a project, Ask shows an empty state describing it as an independent project assistant: it can guide the workflow at any point and, when reference documents are indexed, answers with citations. Neither a raw report, a template nor indexed PDFs are required to open it. With a project, a context line above the conversation states the project and, when connected, the calculation files, selected cases and number of runs recorded as calculated (failed runs are not counted); otherwise it says no calculation is connected and Ask doesn't need one. Any explicit navigation, including choosing Ask before a project exists or loads, wins over the automatic Inputs landing, and the view is written to the URL with or without a project so Ask can be bookmarked.

Inputs never claims a check is running after it has finished: it shows checking, failed (with a deliberate "Check again" button and no automatic retry, window-focus or reconnect refetch), needs resolution, or ready. Connect is enabled only after a finished, successful check of the current selection, never from cached results while a check is running or after it failed. Ask never changes calculation inputs. The source drawer stays secondary and opens on demand.

## Civil engineering project notebook

Keep Documents and Calculations in a shared header. The library notebook has persistent Questions, Source evidence, Design basis and Outputs sections; section controls jump within the work rather than hiding it. Sources remain searchable in a bounded file rail. Put the question composer above answers so large libraries cannot push the primary task below a long document list. Advanced service configuration belongs in a modal. Preserve project identity when moving to Calculations and restore connected snapshots, case selections and explicit mappings through the shared engine. A saved answer or calculation is historical evidence, never silently updated by new documents.
