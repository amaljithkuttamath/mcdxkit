# Supported template contract

This is a template adapter, not a universal MCDX authoring engine. It preserves the supplied design expressions and adds input pages upstream of them.

## Validate the pair first

Use `mcdxkit inspect report.txt --template reference.mcdx` before generating outputs. Preflight compares the parsed raw report with the selected template, mapping, case selection and overrides. It checks dimensions, supported layout and observed pile capacity. The browser exposes this on Inputs and blocks incompatible pairs. It does not execute formulas or approve the engineering design.

## Default fixed-head profile

Without an explicit input map, the fixed-head preset requires one literal scalar definition of each variable below. Literal subscripts in Mathcad's XAML identifiers are normalized with underscores for matching.

| Variable | Meaning | Replacement |
|---|---|---|
| `P_a` | Factored axial compression before downdrag | Maximum selected local axial compression, kip |
| `V_u` | Design shear for the inherited web check | Maximum selected local Vy magnitude, kip |
| `M_uy` | Applied weak-axis moment | Maximum selected local My magnitude, kip-in |
| `M_uz` | Applied strong-axis moment | Maximum selected local Mz magnitude, kip-in |
| `n_z`, `n_y` | Pile layout counts | Retained unless explicitly overridden |

This axis correspondence is the convention of the supplied fixed-head template. Confirm it before using a differently oriented section/template. Vz is included on the source pages but does not add a second shear check to the inherited design.

The expected package namespaces are worksheet50 and math50. Production layout is Letter portrait with page margins `48,144,48,48`; the adapter prepends 864-unit printable pages. Nested calculation areas and unsupported layouts require another adapter. Compact names beginning with `Gd` are reserved to keep formulas within the source-page columns. Start from a clean reference, not a previously generated file.

## Different template inputs

`mcdxkit inspect reference.mcdx` discovers top-level literal scalar inputs from the actual worksheet. Functions, derived equations and program-local variables are not editable inputs. Duplicate definitions are marked ambiguous.

Use repeated `--map VARIABLE=COMPONENT` flags (or SDK `input_map`) to replace the preset completely. Components are `P`, `Vy`, `Vz`, `My`, `Mz`; for example `--map AxialCustom=P --map TransverseCustom=Vz`. The browser exposes the same mapping choices in Inputs. Unmapped definitions and downstream equations remain in the template. Units and names alone do not establish the engineering correspondence: review custom mappings explicitly.

Mapped definitions must be finite literal scalars with supported dimensions: one force unit (`kip`, `lbf`, `N`, `kN`), or one force unit multiplied by one length unit (`in`, `ft`, `m`, `cm`, `mm`) for moments. Generated inputs use kip and kip-in quantities; native dependencies preserve physical units. Dimensionless counts and incompatible dimensions cannot be mapped to loads. Unsupported expression forms fail explicitly.

Select between 1 and 100 load cases. Source pages grow with the selection, five cases per page, and native running envelopes connect all selected cases to mapped inputs. This does not add design checks absent from the template.

With an explicit map, templates without the `n_z`/`n_y` pair are accepted, but pile capacity is **not checked** and the audit records `template_pile_count: null`. If both counts exist they are validated. Geometry and engineering adequacy still need their own review.

## Overrides

`--set VARIABLE=VALUE` accepts an unambiguous literal scalar, optionally multiplied by a unit. It retains the existing unit; it is not a unit conversion. Report-controlled load variables cannot be overridden. Compound expressions or powered-unit expressions with multiple numeric literals are rejected to avoid modifying an exponent accidentally. Count values must be positive integers.

The observed maximum pile ID is a lower bound on the layout capacity, not a recovered pile count. If a report references pile 15 and the template holds 11 piles, obtain/review the actual layout. Overriding `n_z` also changes spacing and downstream geometry formulas, so a count change is an engineering input change.

## Source interpretation and audit

The report parser retains both signed extrema in the audit JSON. Generated shear/moment input definitions use their magnitudes and native `max` expressions. Native running envelopes then feed the mapped template definitions. Each component can govern in a different case and pile; the result is not a concurrent load combination.

Defaults follow the provided Excel-style summary convention: P from local top reactions; shears and moments from local pile effects. Select `--load-source reactions` only when top reactions are the intended design basis. No global-table fallback exists.

## Validation limits

The scripts check package integrity, XML, relationships, case selection, units declared in the report, and input mapping. They clear cached outputs and enable automatic recalculation. They do not ship a Mathcad calculation engine or PTC schema files. Opening, rendering, calculation branches and engineering adequacy require the appropriate native/application review.

[PTC Prime 10 recalculation guidance](https://support.ptc.com/help/mathcad/r10.0/en/PTC_Mathcad_Help/about_controlling_region_calculation.html): opening alone does not force recalculation; Ctrl+F5 recalculates the worksheet.
