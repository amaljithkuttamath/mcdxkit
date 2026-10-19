"""Installed `mcdxkit` command, also runnable as `python scripts/cli.py`."""
import argparse
import sys
if __package__:
    from . import generate, validate
else:
    import generate  # type: ignore[no-redef]
    import validate  # type: ignore[no-redef]


def checks(argv=None):
    """`mcdxkit checks list`: id, version, kind, source and on/off for a --checks value."""
    import json
    from .review import registry
    parser=argparse.ArgumentParser(prog='mcdxkit checks',description='List advisory review checks. Built-ins are on by default; '
                                   'installed plug-ins (entry-point group mcdxkit.review) are off unless named in --checks.')
    parser.add_argument('action',choices=['list'],help='list: show every check and whether --checks enables it')
    parser.add_argument('--checks',default='default',metavar='SPEC',help='Show on/off for this value: default, none, or a comma list such as default,my_check')
    parser.add_argument('--json',action='store_true',help='Print JSON rows instead of a table')
    args=parser.parse_args(argv)
    try:rows=registry.listing(args.checks)
    except ValueError as exc:
        print('mcdxkit: '+str(exc),file=sys.stderr);return 2
    if args.json:
        print(json.dumps(rows,indent=2));return 0
    print(f"{'ID':<30} {'VERSION':<8} {'KIND':<11} {'SOURCE':<12} STATE")
    for row in rows:
        state='on' if row['enabled'] else 'off'
        if row.get('error'):state='error: '+row['error']
        print(f"{row['id']:<30} {row['version'] or '-':<8} {row['kind'] or '-':<11} {row['source']:<12} {state}")
    return 0


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(prog='mcdxkit',description='Convert final GROUP local-load summaries to native Mathcad worksheets.')
    parser.add_argument('--version',action='version',version='mcdxkit 0.4.0')
    sub=parser.add_subparsers(dest='command',required=True)
    for name,description in [('inspect','Inspect local loads and strength case selection'),
                             ('convert','Generate and calculate open and Mathcad worksheets'),
                             ('setup-engine','Install the required local CalcpadCE calculator'),
                             ('validate','Check an .mcdx package without executing Mathcad'),
                             ('serve','Start the local browser interface'),
                             ('batch','Convert files or folders with parallel workers and resume'),
                             ('checks','List advisory review checks: built-in and installed plug-ins'),
                             ('summary','Write a CSV of per-case loads for several reports or output directories'),
                             ('documents','Create source-backed design reports and calculation packages'),
                             ('standards','Create or inspect a versioned engineering provenance register')]:
        sub.add_parser(name,help=description,add_help=False)
    selected,rest=parser.parse_known_args(args)
    if selected.command=='documents':
        from .document_cli import main as documents
        return documents(rest)
    if selected.command=='standards':
        from .standards import main as standards
        return standards(rest)
    if selected.command=='setup-engine':
        from .setup_engine import main as setup
        return setup(rest)
    if selected.command=='batch':
        from .batch import main as batch
        return batch(rest)
    if selected.command=='checks':
        return checks(rest)
    if selected.command=='summary':
        from .summary import main as summary
        return summary(rest)
    if selected.command=='serve':
        from .server import main as serve
        return serve(rest)
    if selected.command=='convert':return generate.main(rest,prog='mcdxkit convert')
    if selected.command=='inspect':return generate.main(rest+['--inspect'],prog='mcdxkit inspect')
    return validate.main(rest,prog='mcdxkit validate')


if __name__=='__main__':sys.exit(main())
