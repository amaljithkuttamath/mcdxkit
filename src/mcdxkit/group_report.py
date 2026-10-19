"""Parse GROUP's final local summary without mixing in global or detail tables."""
import math
import re

MARKER = 'SUMMARY FOR LOAD CASES AND COMBINATIONS'
COMPONENTS = ('P', 'Vy', 'Vz', 'My', 'Mz')
_LIMIT_STATE = re.compile(r'^(STR|SER)(?:\b|[-_])', re.I)


def limit_state(name):
    """'STR', 'SER' or None from a case name. Default selection recognizes only these prefixes;
    the advisory review (mcdxkit.review) also recognizes AASHTO long names."""
    match = _LIMIT_STATE.match(name or '')
    return None if match is None else match.group(1).upper()


def peak(case, key):
    """Envelope peak of one case: largest axial compression for P, largest magnitude otherwise."""
    values = case['pairs'][key]
    return max(values) if key == 'P' else max(map(abs, values))
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?'
# Line patterns use horizontal whitespace only: \s spans newlines and backtracks quadratically.
H = r'[^\S\r\n]'
SUMMARY_HEADING = re.compile(
    '^' + H + r'*(?:\*+' + H + '*)?' + (H + '+').join(MARKER.split())
    + H + r'*(?:\*+' + H + r'*)?\r?$', re.M | re.I)
CASE_HEADING = re.compile('^' + H + '*LOAD' + H + '+CASE' + H + '*:' + H + r'*([^\r\n]*)', re.M | re.I)
LOCAL_UNITS = (r'AXIAL *, *KIP +LAT\. *y *, *KIP +LAT\. *z *, *KIP +MOM *x *, *KIP-IN +MOM *y *, *KIP-IN +MOM *z *, *KIP-IN\b'
               .replace(' *', H + '*').replace(' +', H + '+'))


def _table(block, header, minimum, maximum, columns):
    matches = list(re.finditer(header, block, re.I))
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous ' + header)
    tail = block[matches[0].end():]
    following = re.search(r'\n' + H + r'*\*' + H + '+[A-Z]', tail)
    if following:
        tail = tail[:following.start()]
    rows, locations = [], []
    for label in [minimum, maximum]:
        found = list(re.finditer('^' + H + '*' + re.escape(label) + H + r'+([^\r\n]+)', tail, re.M))
        if len(found) != 1:
            raise ValueError('Missing or duplicate ' + label + ' in ' + header)
        words = found[0].group(1).split()
        if len(words) != columns or any(not re.fullmatch(NUMBER, w) for w in words):
            raise ValueError('Malformed numeric row in ' + header)
        values = [float(w.replace('D', 'E').replace('d', 'e')) for w in words]
        if not all(math.isfinite(x) for x in values):
            raise ValueError('Non-finite numeric value in ' + header)
        rows.append(values)
        locations.append(found[0].group(0).strip())
    if any(a > b for a, b in zip(*rows, strict=True)):
        raise ValueError('Minimum exceeds maximum in ' + header)
    ids = []
    for line in re.findall('^' + H + r'*Pile N\.' + H + r'+([^\r\n]+)', tail, re.M | re.I):
        words = line.split()
        if len(words) != columns or any(not w.isdigit() or int(w) < 1 for w in words):
            raise ValueError('Malformed local pile identifiers')
        ids.extend(map(int, words))
    return rows, ids, locations, tail


def parse(text, load_source='effects'):
    """Read full reports or summary-only files, advancing to the next case marker.

    Case names before the final summary are optional metadata, never a source of loads.
    """
    if load_source not in ('effects', 'reactions'):
        raise ValueError('load_source must be effects or reactions')
    headings = list(SUMMARY_HEADING.finditer(text))
    if not headings:
        raise ValueError('Final GROUP summary heading was not found')
    last = headings[-1]
    prefix, summary = text[:last.start()], text[last.end():]
    summary_only = not prefix.strip(' \t\r\n\f*\ufeff')
    names = {}
    for ident, label in re.findall(r'LOAD CASE\s*:\s*(\d+)\s+CASE NAME\s*:\s*([^\r\n]+)', prefix):
        ident = int(ident); label = label.strip()
        if ident in names and names[ident] != label:
            raise ValueError('Conflicting case names for case ' + str(ident))
        names[ident] = label
    if re.search(r'LOAD COMBINATION\s*:', summary, re.I):
        raise ValueError('Separate load-combination sections are not supported; provide a case summary')
    cases = []
    markers = list(CASE_HEADING.finditer(summary))
    for index, marker in enumerate(markers):
        heading = marker.group(1).strip()
        if not re.fullmatch(r'[0-9]+', heading) or int(heading) < 1:
            raise ValueError('Malformed load case heading')
        ident = int(heading)
        # Bound every table search by the next marker; rows from another case
        # cannot complete a truncated case, even when the report has no header.
        next_cursor = markers[index + 1].start() if index + 1 < len(markers) else len(summary)
        block = summary[marker.end():next_cursor]
        if any(c['id'] == ident for c in cases):
            raise ValueError('Duplicate case in final summary: ' + str(ident))
        local, ids, raw_local, local_text = _table(block, r'\* PILE TOP REACTIONS, LOCAL \*', 'MINIMUM', 'MAXIMUM', 6)
        if not re.search(LOCAL_UNITS, local_text, re.I):
            raise ValueError('Expected local reaction units: kip and kip-in')
        if load_source == 'effects':
            effects, effect_ids, raw_effects, effect_text = _table(block, r'\* EFFECTS FOR LATERALLY LOADED PILE \*', 'Min.', 'Max.', 9)
            if not re.search(r'KIP-IN\s+KIP-IN\s+KIP\s+KIP\b', effect_text, re.I):
                raise ValueError('Expected pile-effect units: kip-in and kip')
            directions = re.search(r'y-DIR\s+z-DIR\s+z-DIR\s+y-DIR\s+y-DIR\s+z-DIR', effect_text, re.I)
            if directions is None: raise ValueError('Unrecognized local pile-effect column directions')
            pairs = {key: [row[col] for row in effects] for key, col in [('Vy',4),('Vz',5),('My',3),('Mz',2)]}
            ids += effect_ids
        else:
            effects, raw_effects = None, []
            pairs = {key: [row[col] for row in local] for key, col in [('Vy',1),('Vz',2),('My',4),('Mz',5)]}
        pairs['P'] = [row[0] for row in local]
        cases.append({'id':ident, 'name':names.get(ident), 'pairs':pairs, 'pile_ids':sorted(set(ids)),
                      'load_source':load_source, 'source_rows':{'local':raw_local,'effects':raw_effects},
                      # Both numeric tables, for the advisory review (mcdxkit.review.view): pairs hold only
                      # the chosen load source, and source_rows are unparsed text.
                      'tables':{'local':local,'effects':effects}})
    if not cases: raise ValueError('No load cases in final summary')
    return {'cases':cases,'load_source':load_source,'summary_only':summary_only}


def select(parsed, cases=None):
    available = {c['id']:c for c in parsed['cases']}
    if cases is None:
        if any(limit_state(c['name']) is None for c in available.values()):
            if parsed.get('summary_only'):
                raise ValueError('Case names are absent from this summary-only report; specify --cases explicitly')
            raise ValueError('Case classification unavailable; specify --cases explicitly')
        cases = [c['id'] for c in available.values() if limit_state(c['name']) == 'STR']
    if not cases or len(cases) != len(set(cases)) or any(i not in available for i in cases):
        raise ValueError('Select nonempty, unique, existing --cases')
    selected = [available[i] for i in cases]
    if any(max(c['pairs']['P']) <= 0 or min(c['pairs']['P']) < 0 for c in selected):
        raise ValueError('Selected cases contain tension or no compression; this compression template needs engineering review')
    return selected


def envelope(cases):
    return {key: max(peak(c, key) for c in cases) for key in COMPONENTS}
