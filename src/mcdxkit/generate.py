"""CLI: GROUP final local summary -> native Mathcad input/formula worksheet."""
import argparse
import json
import os
import sys
from pathlib import Path
from . import engine, mcdx
from .review import runner as review_runner


def case_ids(value):
    try:
        result=[]
        for word in value.split(','):
            if '-' in word:
                first,last=map(int,word.split('-'))
                if last<first or last-first>100:raise ValueError
                result.extend(range(first,last+1))
            else:result.append(int(word))
        if any(i<1 for i in result) or len(set(result))!=len(result):raise ValueError
        return result
    except ValueError:raise argparse.ArgumentTypeError('Use unique positive cases, e.g. 1-10 or 1,3,7') from None


def input_map(pairs):
    if not pairs:return None
    mapping={}
    for pair in pairs:
        key,component=pair.split('=',1)
        if not key or key in mapping or component not in mcdx.CODES:
            raise ValueError('Use unique --map VARIABLE=COMPONENT entries (P, Vy, Vz, My, Mz)')
        mapping[key]=component
    return mapping


def default_template():
    """Resolve explicit, checkout, generic user and legacy template locations."""
    if os.environ.get('MCDXKIT_TEMPLATE'):
        return Path(os.environ['MCDXKIT_TEMPLATE'])
    home = Path.home()
    generic = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'mcdxkit/reference.mcdx'
    candidates = (
        Path(__file__).resolve().parents[2] / 'assets/local/reference.mcdx',
        generic,
        home / '.agents/skills/mcdxkit/assets/local/reference.mcdx',
        Path(os.environ.get('CODEX_HOME', str(home / '.codex'))) / 'skills/mcdxkit/assets/local/reference.mcdx',
    )
    return next((path for path in candidates if path.is_file()), generic)


def main(argv=None,prog=None):
    parser=argparse.ArgumentParser(prog=prog,description=__doc__)
    parser.add_argument('input',type=Path,help='GROUP report, or an .mcdx template when inspecting')
    parser.add_argument('--template',type=Path,help='Compatible .mcdx template; also MCDXKIT_TEMPLATE or installed private template')
    parser.add_argument('--map',action='append',default=[],metavar='VARIABLE=COMPONENT',help='Map a detected template input to P, Vy, Vz, My or Mz; repeat for custom templates')
    parser.add_argument('--output',type=Path,help='New .mcdx output path (never overwritten)')
    parser.add_argument('--inspect',action='store_true',help='Inspect case selection and load envelopes without generating')
    parser.add_argument('--cases',type=case_ids,help='Explicit strength case IDs; default detects STR case names')
    parser.add_argument('--load-source',choices=['effects','reactions'],default='effects',help='Use local pile effects (default) or local pile-top reactions for shear/moments')
    parser.add_argument('--title',help='Title on generated source pages; template header remains unchanged')
    parser.add_argument('--set',action='append',default=[],metavar='VARIABLE=VALUE',help='Override a single literal template input, retaining its units')
    parser.add_argument('--checks',default='default',metavar='SPEC',help='Advisory review plug-ins: default (built-ins), none, or a comma list such as default,my_check; see "mcdxkit checks list"')
    parser.add_argument('--history',type=Path,metavar='OUTPUT_DIR',help='Inspect only: learn case naming from completed conversion audits in this output directory')
    args=parser.parse_args(argv)
    try:
        mapping=input_map(args.map)
        overrides={}
        for assignment in args.set:
            key,value=assignment.split('=',1)
            if key in overrides:raise ValueError('Duplicate override: '+key)
            overrides[key]=float(value)
        if args.inspect:
            if args.input.suffix.lower()=='.mcdx':
                print(json.dumps(engine.inspect_template(args.input,input_map=mapping),indent=2))
                return 0
            history=None
            if args.history is not None:
                from .review.history import AuditHistory
                if not args.history.is_dir():raise ValueError('--history must be an existing output directory')
                history=AuditHistory(args.history)
            print(json.dumps(engine.inspect_report(args.input,cases=args.cases,load_source=args.load_source,checks=args.checks,history=history,
                                                  template=args.template,input_map=mapping,overrides=overrides),indent=2))
            return 0
        if args.history is not None:raise ValueError('--history applies to inspect only')
        if args.output is None:raise ValueError('--output is required unless using --inspect')
        result=engine.convert(args.input,args.template or default_template(),args.output,cases=args.cases,load_source=args.load_source,
                              title=args.title,overrides=overrides,checks=args.checks,input_map=mapping)
        print(json.dumps({'output':result['output'],'audit':result['audit'],'cases':result['cases'],
                          'envelope':result['envelope'],'open_worksheet':result['open_worksheet'],
                          'calculated_worksheet':result['calculated_worksheet'],
                          'calculation':{k:result['calculation'][k] for k in ('engine','calculated','translated_math_regions')},
                          'open_checks':review_runner.open_flags(result['review_checks']),
                          'check_errors':review_runner.check_errors(result['review_checks']),
                          'check_summary':result['check_summary'],

                          'native_execution_verified':False},indent=2))
        return 0
    except (ValueError,OSError,KeyError,IndexError,mcdx.E.XMLSyntaxError,mcdx.zipfile.BadZipFile) as exc:
        print('mcdxkit: '+str(exc),file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
