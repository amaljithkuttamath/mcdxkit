"""Document-authoring CLI; same workflow as the browser, no alternate engine."""
import argparse
import json
import sys
from pathlib import Path
from .document_clients import ModelConfig
from .documents import DocumentWorkflow


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mcdxkit documents', description='Create cited design packages using RAGFlow, OpenRouter free models and the shared calculation engine.')
    parser.add_argument('--workspace', default='mcdxkit-output/.documents', help='Persistent document-project directory (same server output directory/.documents)')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('discover', help='Detect document service health and its default search model')
    sub.add_parser('config', help='Show configuration presence, never secrets')
    sub.add_parser('list', help='List saved projects')
    create = sub.add_parser('create', help='Create a document library, optionally with calculation files')
    create.add_argument('report', nargs='?'); create.add_argument('--template'); create.add_argument('--title', required=True)
    create.add_argument('--cases', nargs='+', type=int); create.add_argument('--input-map', help='JSON object mapping template variables to load components')
    create.add_argument('--load-source', choices=['effects', 'reactions'], default='effects')
    connect = sub.add_parser('connect', help='Add a calculation (report + template) to a project; a project holds many')
    connect.add_argument('project'); connect.add_argument('report')
    source = connect.add_mutually_exclusive_group(required=True)
    source.add_argument('--template', help='Mathcad template file')
    source.add_argument('--library-template', help='Template ID from "templates list"')
    connect.add_argument('--name', help='Calculation name; defaults to "report + template"')
    connect.add_argument('--cases', nargs='+', type=int); connect.add_argument('--input-map')
    connect.add_argument('--load-source', choices=['effects', 'reactions'], default='effects')
    ask = sub.add_parser('ask', help='Ask a cited question without a raw report or template')
    ask.add_argument('project'); ask.add_argument('--question', required=True); ask.add_argument('--parent', help='Previous answer ID for a follow-up')
    extract = sub.add_parser('extract', help='Extract cited values and conditions from selected original PDF pages')
    extract.add_argument('project'); extract.add_argument('source'); extract.add_argument('--pages', required=True); extract.add_argument('--question', required=True)
    decision = sub.add_parser('review-requirement', help='Record a source requirement review without changing inputs')
    decision.add_argument('project'); decision.add_argument('requirement'); decision.add_argument('--status', choices=['reviewed', 'rejected'], required=True)
    decision.add_argument('--reviewer', required=True); decision.add_argument('--note', required=True)
    basis = sub.add_parser('requirement-draft', help='Create a design basis from explicitly reviewed requirements and input bindings')
    basis.add_argument('project'); basis.add_argument('--requirements', nargs='+', required=True)
    basis.add_argument('--bindings', default='[]', help='JSON list of requirement, variable, value and unit bindings; empty retains inputs')
    basis.add_argument('--reviewer', required=True); basis.add_argument('--note', required=True); basis.add_argument('--applicability', required=True)
    calculations = sub.add_parser('calculations', help="List a project's calculations"); calculations.add_argument('project')
    templates = sub.add_parser('templates', help='Workspace template library: upload once, reuse in every project')
    template_sub = templates.add_subparsers(dest='template_action', required=True)
    template_sub.add_parser('list', help='List templates').add_argument('--archived', action='store_true', help='Include archived templates')
    template_sub.add_parser('add', help='Add .mcdx templates; identical files are stored once').add_argument('files', nargs='+')
    for verb, text in (('archive', 'Hide from new calculations; nothing is deleted'), ('restore', 'Show an archived template again')):
        template_sub.add_parser(verb, help=text).add_argument('template')
    sub.add_parser('jobs', help='List durable operations for a project').add_argument('project')
    sub.add_parser('job', help='Read a durable operation').add_argument('job')
    sub.add_parser('cancel', help='Stop an operation; uncertain remote requests are not retried').add_argument('job')
    from .jobs import ACTIONS
    submit = sub.add_parser('submit', help='Submit an operation to the running server workers')
    submit.add_argument('project'); submit.add_argument('operation', choices=sorted(ACTIONS))
    submit.add_argument('--payload', default='{}', help='JSON object matching the operation fields')
    submit.add_argument('--request-key', required=True, help='Stable client identity; reuse only for the same inputs')
    add = sub.add_parser('add', help='Save original PDFs locally'); add.add_argument('project'); add.add_argument('files', nargs='+')
    for action in ('show', 'ingest', 'status'):
        sub.add_parser(action).add_argument('project')
    draft = sub.add_parser('draft', help='Send retrieved excerpts to OpenRouter and save a cited draft')
    draft.add_argument('project'); draft.add_argument('--request', required=True)
    draft.add_argument('--calculation', help='Calculation ID; required when the project has several')
    review = sub.add_parser('review', help='Record design-basis review; unselected suggestions are not applied')
    review.add_argument('project'); review.add_argument('draft'); review.add_argument('--accept', nargs='*', type=int, default=[])
    review.add_argument('--reviewer', required=True); review.add_argument('--note', required=True)
    build = sub.add_parser('build', help='Calculate accepted inputs and create an immutable report package')
    build.add_argument('project'); build.add_argument('draft', nargs='?', help='Omit to calculate retained template inputs without a design-basis review claim')
    build.add_argument('--calculation', help='Calculation ID; required without a draft when the project has several')
    basis.add_argument('--calculation', help='Calculation ID; required when the project has several')
    args = parser.parse_args(argv)
    try:
        if args.action == 'discover':
            from .document_services import discover
            result = discover(args.workspace)
        elif args.action == 'config': result = ModelConfig.for_workspace(args.workspace).public()
        elif args.action in ('jobs', 'job', 'cancel', 'submit'):
            from .jobs import JobStore
            store = JobStore(args.workspace)
            if args.action == 'jobs': result = store.listing(args.project)
            elif args.action == 'job': result = store.public(store.get(args.job))
            elif args.action == 'cancel': result = store.cancel(args.job)
            else: result = store.public(store.submit(args.project, args.operation, json.loads(args.payload), args.request_key))
        else:
            flow = DocumentWorkflow(args.workspace)
            if args.action == 'list': result = flow.listing()
            elif args.action == 'create': result = flow.create(args.title, args.report, args.template, cases=args.cases,
                    input_map=json.loads(args.input_map) if args.input_map else None, load_source=args.load_source)
            elif args.action == 'connect':
                template = args.template
                if args.library_template:
                    from .template_library import TemplateLibrary
                    template = TemplateLibrary(args.workspace).path(args.library_template)
                result = flow.attach_calculation(args.project, args.report, template, cases=args.cases, name=args.name,
                    template_id=args.library_template, input_map=json.loads(args.input_map) if args.input_map else None,
                    load_source=args.load_source)
            elif args.action == 'calculations': result = flow.calculations(flow.get(args.project))
            elif args.action == 'templates':
                from .template_library import TemplateLibrary
                library = TemplateLibrary(args.workspace)
                if args.template_action == 'list': result = library.listing(args.archived, flow.template_usage())
                elif args.template_action == 'add':
                    result = []
                    for file in args.files:
                        path = Path(file)
                        if path.stat().st_size > 32*1024*1024: raise ValueError('Template exceeds 32 MiB.')
                        result.append(library.add(path.name, path.read_bytes()))
                else: result = library.archive(args.template, args.template_action == 'archive')
            elif args.action == 'ask': result = flow.ask(args.project, args.question, parent=args.parent)
            elif args.action == 'extract': result = flow.extract(args.project, args.source, args.pages, args.question)
            elif args.action == 'review-requirement': result = flow.review_requirement(args.project, args.requirement, args.status, args.reviewer, args.note)
            elif args.action == 'requirement-draft': result = flow.requirement_draft(args.project, args.requirements, json.loads(args.bindings), args.reviewer, args.note, args.applicability, args.calculation)
            elif args.action == 'add':
                result = []
                for file in args.files:
                    path = Path(file)
                    if path.stat().st_size > 256*1024*1024: raise ValueError('PDF exceeds 256 MiB; split it into named parts.')
                    result.append(flow.add_source(args.project, path.name, path.read_bytes()))
            elif args.action == 'show': result = flow.public(flow.get(args.project))
            elif args.action == 'ingest': result = flow.ingest(args.project)
            elif args.action == 'status': result = flow.status(args.project)
            elif args.action == 'draft': result = flow.draft(args.project, args.request, calculation=args.calculation)
            elif args.action == 'review': result = flow.review(args.project, args.draft, args.accept, args.reviewer, args.note)
            else:
                result = flow.build(args.project, args.draft, args.calculation)
                result['package'] = str(flow.artifact(args.project, result['id'], 'package.zip'))
        print(json.dumps(result, indent=2, allow_nan=False)); return 0
    except (ValueError, OSError) as exc:
        print('mcdxkit documents: ' + str(exc), file=sys.stderr); return 2
