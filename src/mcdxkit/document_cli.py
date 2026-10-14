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
    sub.add_parser('config', help='Show configuration presence, never secrets')
    sub.add_parser('list', help='List saved projects')
    create = sub.add_parser('create', help='Create a document library, optionally with calculation files')
    create.add_argument('report', nargs='?'); create.add_argument('--template'); create.add_argument('--title', required=True)
    create.add_argument('--cases', nargs='+', type=int); create.add_argument('--input-map', help='JSON object mapping template variables to load components')
    create.add_argument('--load-source', choices=['effects', 'reactions'], default='effects')
    connect = sub.add_parser('connect', help='Connect calculation files to an existing library without reindexing PDFs')
    connect.add_argument('project'); connect.add_argument('report'); connect.add_argument('--template', required=True)
    connect.add_argument('--cases', nargs='+', type=int); connect.add_argument('--input-map')
    connect.add_argument('--load-source', choices=['effects', 'reactions'], default='effects')
    ask = sub.add_parser('ask', help='Ask a cited question without a raw report or template')
    ask.add_argument('project'); ask.add_argument('--question', required=True)
    add = sub.add_parser('add', help='Save original PDFs locally'); add.add_argument('project'); add.add_argument('files', nargs='+')
    for action in ('show', 'ingest', 'status'):
        sub.add_parser(action).add_argument('project')
    draft = sub.add_parser('draft', help='Send retrieved excerpts to OpenRouter and save a cited draft')
    draft.add_argument('project'); draft.add_argument('--request', required=True)
    review = sub.add_parser('review', help='Record design-basis review; unselected suggestions are not applied')
    review.add_argument('project'); review.add_argument('draft'); review.add_argument('--accept', nargs='*', type=int, default=[])
    review.add_argument('--reviewer', required=True); review.add_argument('--note', required=True)
    build = sub.add_parser('build', help='Calculate accepted inputs and create an immutable report package')
    build.add_argument('project'); build.add_argument('draft')
    args = parser.parse_args(argv)
    try:
        if args.action == 'config': result = ModelConfig.for_workspace(args.workspace).public()
        else:
            flow = DocumentWorkflow(args.workspace)
            if args.action == 'list': result = flow.listing()
            elif args.action == 'create': result = flow.create(args.title, args.report, args.template, cases=args.cases,
                    input_map=json.loads(args.input_map) if args.input_map else None, load_source=args.load_source)
            elif args.action == 'connect': result = flow.attach_calculation(args.project, args.report, args.template, cases=args.cases,
                    input_map=json.loads(args.input_map) if args.input_map else None, load_source=args.load_source)
            elif args.action == 'ask': result = flow.ask(args.project, args.question)
            elif args.action == 'add':
                result = []
                for file in args.files:
                    path = Path(file)
                    if path.stat().st_size > 256*1024*1024: raise ValueError('PDF exceeds 256 MiB; split it into named parts.')
                    result.append(flow.add_source(args.project, path.name, path.read_bytes()))
            elif args.action == 'show': result = flow.get(args.project)
            elif args.action == 'ingest': result = flow.ingest(args.project)
            elif args.action == 'status': result = flow.status(args.project)
            elif args.action == 'draft': result = flow.draft(args.project, args.request)
            elif args.action == 'review': result = flow.review(args.project, args.draft, args.accept, args.reviewer, args.note)
            else:
                result = flow.build(args.project, args.draft)
                result['package'] = str(flow.artifact(args.project, result['id'], 'package.zip'))
        print(json.dumps(result, indent=2, allow_nan=False)); return 0
    except (ValueError, OSError) as exc:
        print('mcdxkit documents: ' + str(exc), file=sys.stderr); return 2
