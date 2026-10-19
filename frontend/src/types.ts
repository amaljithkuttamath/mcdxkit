export type Quote = {page:number; quote:string; text?:string};
export type Review = {status?:string; reviewer:string; note:string; accepted?:number[]};
export type Fact = {id:string; subject:string; property:string; value_text:string; unit:string; evidence:Quote[]; conditions:Quote[]; review:Review|null};
export type Source = {id:string; name:string; status?:string; sha256?:string};
export type Extraction = {id:string; source_id:string; source_name:string; facts:Fact[]; gaps:string[]; model:string};
export type Evidence = {id:string; source_id:string; source_name:string; positions:number[][]; text:string; kind?:'page_context'|'extraction_quote'; truncated?:boolean; page_characters?:number};
export type Audit = {status:'passed'|'warnings'; cited:string[]; unsupported_numbers:{heading:string;values:string[]}[]; meaning:string};
// Answer-check record (docs/answer-audit.md). Historical statement checks remain readable.
export type GroundingQuote = {evidence_id:string; text:string};
export type GroundingVerdict = 'supported'|'unsupported'|'uncertain'|'structural';
export type GroundingStatement = {id:string; kind:'heading'|'section'|'conflict'; section:number|null; verdict:GroundingVerdict; reasons:string[]; checks_applied?:string[]; anchored?:boolean; text?:string; evidence_ids?:string[]; quotes?:GroundingQuote[]};
export type Grounding = {version:string; status:'citations_checked'|'grounded'|'withheld'|'abstained'|'workflow'; draft_withheld?:boolean; method?:string; statements?:GroundingStatement[]; inline_anchored?:number; checked_statements?:number; unconfirmed_statements?:number; reason_code?:string; coverage?:'partial'|'searchable_documents'; scope?:{retrieval?:string; precedence?:string; approval?:string}; meaning?:string};
export type ActivityType ='step'|'search'|'context'|'extraction'|'check';
export type Activity = {seq?:number; time:number; message:string; type?:ActivityType; detail?:{query?:string;passages?:number;sources?:string[];source?:string;pages?:number[];facts?:number;extraction_id?:string;status?:string;citations?:number;unsupported?:number;finished?:number;total?:number}};
// Result of a calculate_all job: one row per pinned calculation, in the order calculated.
export type CalculateAllResult = {total:number;succeeded:number;skipped:number;failed:number;calculations:{calculation_id:string;name:string;status:'succeeded'|'skipped'|'failed';run_id:string|null;error:string|null}[]};
export type WorkflowAction='add_files'|'index_documents'|'extract_requirements'|'review_requirements'|'map_inputs'|'calculate'|'view_outputs';
export type Draft = {id:string; created?:number; grounding?:Grounding; extraction_ids?:string[]; clarification?:{question:string;choices:string[]}|null; kind?:'research'|'workflow';actions?:WorkflowAction[]; conversation_id?:string; parent_id?:string|null; request:string; model:string; review:Review|null; assessment?:{status:'supported'|'partial'|'insufficient'|'conflicted';explanation:string;conflicts:string[]};coverage?:{ready:number;total:number;excluded:string[]};searches?:{query:string;passages:number}[];audit?:Audit;extraction_offer?:{source_id:string;source_name:string;pages:number[];question:string}|null;context_reads?:{source_name:string;anchor_pages:number[];pages:number[];truncated_pages?:number[];omitted_pages?:number[];characters_per_page?:number}[];content:{sections:{heading:string; body:string; evidence_ids:string[]}[]; suggestions:{variable:string; value:number; unit:string; reason:string}[]; gaps:string[]}; evidence:Evidence[]};
export type Run = {id:string; created:number; draft_id:string|null; calculation_id?:string; calculated:boolean; check_summary?:{total:number; passed:number; failed:number}};
export type Input = {variable:string; value:number; unit:string; component:string|null; status:string; expression:string; replacement?:{value:number; unit:string}|null};
export type Inputs = {inputs:Input[]; input_map:Record<string,string>};
export type CalculationOptions = {cases:number[]; input_map:Record<string,string>|null; load_source:string};
/** One immutable report + template snapshot. A project holds many; `primary` is a project saved before that. */
export type Calculation = {id:string; name:string; created:number; report_name:string; template_name:string; template_id?:string|null; template_inputs:Inputs; options:CalculationOptions};
export type Project = {id:string; title:string; revision:number; sources:Source[]; extractions?:Extraction[]; answers:Draft[]; drafts:Draft[]; runs:Run[]; calculations:Calculation[]};
export type LibraryTemplate = {id:string; name:string; sha256:string; bytes:number; created:number; archived:boolean; version:number; calculations:number;
  inputs:{variable:string; value:number; unit:string; component:string|null; status:string; expression:string}[]; mapping_basis:string|null; missing_load_inputs:string[]};
export type Job = {id:string;created?:number;activity?:Activity[]; action:string; state:string; stage:string; request?:string;parent?:string|null;error?:string|null; uncertain:boolean; result?:unknown};
export type Inspection = {cases:{id:number; name:string}[]; selected_cases:number[]; envelope:Record<string,number>; governing?:Record<string,{value:number; case:number; case_name:string}>|null; template_inputs?:Inputs; template_validation?:{status:string; errors:string[]; warnings:string[]}};
export type Upload = {id:string; name:string; inspection?:Inspection; template_id?:string};
