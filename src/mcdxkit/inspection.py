"""Read-only views of native worksheet expressions; never execute uploaded code."""
import io
import posixpath
import base64
import math
import re
import copy
import hashlib
from pathlib import Path
from . import mcdx

OPS = {'plus': '+', 'minus': '−', 'mult': '×', 'div': '/', 'pow': '^', 'equal': '=',
       'lessThan': '<', 'greaterThan': '>', 'lessOrEqual': '≤', 'greaterOrEqual': '≥',
       'and': 'and', 'or': 'or'}


def presentation(node, depth=0):
    """Presentation MathML for inspection only; no evaluator or HTML from the file."""
    def n(tag, *children, text=None):
        return {'tag': tag, 'children': list(children), 'text': text}
    def render(child): return presentation(child, depth+1)
    def op_of(child): return mcdx.tag(child[0]) if mcdx.tag(child) == 'apply' and len(child) else None
    def grouped(child, ops=('plus', 'minus', 'mult', 'scale', 'div')):
        rendered = render(child)
        return n('mrow', n('mo', text='('), rendered, n('mo', text=')')) if op_of(child) in ops else rendered
    tag = mcdx.tag(node); children = list(node)
    if depth > 35: return n('mtext', text='[nested expression]')
    if tag == 'id':
        name = mcdx.name(node)
        if '_' in name:
            base, sub = name.split('_', 1)
            return n('msub', n('mi', text=base), n('mi', text=sub))
        return n('mi', text=name)
    if tag == 'real': return n('mn', text=node.text)
    if tag == 'str': return n('mtext', text='“'+''.join(node.itertext())+'”')
    if tag == 'define' and len(children) == 2:
        return n('mrow', render(children[0]), n('mo', text=':='), render(children[1]))
    if tag == 'eval' and children:
        return n('mrow', render(children[0]), n('mo', text='='), n('mtext', text='…'))
    if tag == 'apply' and children:
        op = mcdx.tag(children[0]); args = children[1:]
        if op in ('div', 'pow') and len(args) == 2:
            if op == 'pow': return n('msup', grouped(args[0], ('plus', 'minus', 'mult', 'scale', 'div', 'neg', 'pow')), render(args[1]))
            return n('mfrac', *map(render, args))
        if op == 'sqrt' and args: return n('msqrt', render(args[0]))
        if op == 'nthRoot' and len(args) == 2:
            return n('msqrt', render(args[1])) if mcdx.tag(args[0]) == 'placeholder' else n('mroot', render(args[1]), render(args[0]))
        if op in OPS or op == 'scale':
            result = []
            for arg in args:
                if result: result.append(n('mo', text=OPS.get(op, ' ')))
                child=render(arg)
                child_op=op_of(arg)
                needs_parens=(op in ('mult','scale') and child_op in ('plus','minus')) or (op=='minus' and arg is args[-1] and child_op in ('plus','minus','neg'))
                result.append(n('mrow',n('mo',text='('),child,n('mo',text=')')) if needs_parens else child)
            return n('mrow', *result)
        if op == 'neg': return n('mrow', n('mo', text='−'), *map(grouped,args))
        if op == 'id': return n('mrow', render(children[0]), n('mo', text='('), *map(render,args), n('mo', text=')'))
    if tag in ('test', 'then', 'else', 'return', 'result', 'unitOverride'):
        return n('mrow', *map(render, children))
    if tag == 'program':
        return n('mrow', n('mo', text='│'), n('mtable', *(n('mtr', n('mtd', render(c))) for c in children if mcdx.tag(c) != 'placeholder')))
    if tag in ('if', 'alsoif', 'elseif'):
        rows = []
        if len(children) >= 2:
            rows.append(n('mtr', n('mtd', render(children[1])), n('mtd', n('mtext', text='if '), render(children[0]))))
        for child in children[2:]:
            rows.append(n('mtr', n('mtd', render(child)), n('mtd', n('mtext', text='otherwise' if mcdx.tag(child) == 'else' else ''))))
        return n('mtable', *rows)
    if tag == 'placeholder': return n('mtext', text='')
    if tag == 'sequence':
        result = []
        for child in children:
            if result: result.append(n('mo', text=','))
            result.append(render(child))
        return n('mrow', *result)
    if tag == 'parens': return n('mrow', n('mo', text='('), *map(render,children), n('mo', text=')'))
    return n('mtext', text=expression(node))


def expression(node, depth=0):
    if depth > 40:
        return '[nested expression — inspect XML]'
    tag = mcdx.tag(node)
    children = list(node)
    if tag == 'id':
        return mcdx.name(node)
    if tag in ('real', 'str', 'string'):
        text = ''.join(node.itertext())
        return repr(text) if tag in ('str', 'string') else text
    if tag == 'define' and len(children) == 2:
        return expression(children[0], depth+1) + ' := ' + expression(children[1], depth+1)
    if tag == 'eval' and children:
        return expression(children[0], depth+1) + ' = [calculate in Mathcad]'
    if tag == 'sequence':
        return ', '.join(expression(c, depth+1) for c in children)
    if tag == 'apply' and children:
        op = mcdx.tag(children[0]); args = [expression(c, depth+1) for c in children[1:]]
        if op == 'scale':
            return ' '.join(args)
        if op in OPS:
            return '(' + (' ' + OPS[op] + ' ').join(args) + ')'
        if op == 'neg':
            return '−(' + ', '.join(args) + ')'
        if op == 'id':
            return expression(children[0], depth+1) + '(' + ', '.join(args) + ')'
        return op + '(' + ', '.join(args) + ')'
    if tag in ('parens', 'unitOverride'):
        return '(' + ', '.join(expression(c, depth+1) for c in children) + ')'
    # Keep unsupported programs/operators visibly identified rather than pretending
    # a Python rendering reproduces Mathcad semantics.
    return '[' + tag + '] ' + ', '.join(expression(c, depth+1) for c in children)


def color(value):
    if re.fullmatch(r'#[0-9a-fA-F]{8}', value or ''):
        return '#'+value[3:] if value[1:3] != '00' else 'transparent'
    return value if re.fullmatch(r'#[0-9a-fA-F]{6}', value or '') else None


def styles(node):
    result = {}
    for source, dest in (('Foreground','color'), ('ForegroundColor','color'), ('Background','backgroundColor'), ('BackgroundColor','backgroundColor')):
        value = color(node.get(source))
        if value: result[dest] = value
    for source, dest in (('FontSize','fontSize'),):
        try:
            value = float(node.get(source,''))
            if math.isfinite(value) and 0 < value <= 100: result[dest] = str(value)+'px'
        except ValueError: pass
    if node.get('FontWeight') == 'Bold': result['fontWeight'] = 'bold'
    if node.get('FontStyle') == 'Italic': result['fontStyle'] = 'italic'
    if node.get('TextDecorations') == 'Underline': result['textDecoration'] = 'underline'
    if node.get('TextAlignment') in ('Left','Right','Center','Justify'): result['textAlign'] = node.get('TextAlignment').lower()
    if node.get('FontFamily') in ('Arial','Tahoma','Segoe UI','Times New Roman','Calibri'): result['fontFamily'] = node.get('FontFamily')
    return result


def flow(node):
    tags = {'Section':'div','FlowDocument':'div','Paragraph':'p','Run':'span','Span':'span','Bold':'b','Italic':'i','Underline':'u','LineBreak':'br','List':'ul','ListItem':'li','Subscript':'sub','Superscript':'sup'}
    return {'tag': tags.get(mcdx.tag(node),'span'), 'text': node.text or '', 'style':styles(node),
            'children':[{'node':flow(child), 'tail':child.tail or ''} for child in node]}


def read_regions(parts, part, cache, budget=None):
    root = mcdx.read_xml(parts[part])
    relpart = 'mathcad/_rels/'+posixpath.basename(part)+'.rels'
    relationships = {}
    if relpart in parts:
        relationships = {r.get('Id'): r.get('Target','').lstrip('/') if r.get('Target','').startswith('/') else posixpath.normpath(posixpath.join('mathcad',r.get('Target','')))
                         for r in mcdx.read_xml(parts[relpart])}
    regions = []
    for region in root.findall('.//w:region',mcdx.NS):
        entry = {'id':region.get('region-id'), 'top':region.get('top','0'), 'left':region.get('left','0'),
                 'width':region.get('actualWidth',region.get('width','120')), 'height':region.get('actualHeight','24')}
        math_node = region.find('w:math',mcdx.NS)
        if math_node is not None and len(math_node):
            formula = math_node[0]; rendered = presentation(formula)
            cached = cache.get(math_node.get('resultRef'))
            if cached is not None and len(cached):
                value = copy.deepcopy(cached[0])
                for real in value.iter(mcdx.q(mcdx.M,'real')):
                    try: real.text = format(float(real.text),'.2f').rstrip('0').rstrip('.')
                    except ValueError: pass
                result = presentation(value)
                unit = formula.find('.//m:unitOverride',mcdx.NS)
                if unit is not None and len(unit) and mcdx.tag(unit[0]) != 'placeholder' and mcdx.tag(value) != 'str':
                    result = {'tag':'mrow','children':[result,presentation(unit[0])],'text':None}
                def replace_value(node, result=result):
                    if node.get('tag') == 'mtext' and node.get('text') == '…': node.clear(); node.update(result)
                    else:
                        for child in node.get('children',[]):replace_value(child)
                replace_value(rendered)
                entry['cached_result'] = True
            formatting = math_node.find('w:formattingOverride',mcdx.NS)
            entry.update(kind='math', expression=expression(formula),presentation=rendered,
                         variable=mcdx.name(formula[0]) if mcdx.tag(formula)=='define' and len(formula) else None,
                         style=styles(formatting) if formatting is not None else {},
                         xml=mcdx.E.tostring(formula,encoding='unicode'))
        else:
            texts=[]; flows=[]
            for node in region.iter():
                if mcdx.tag(node)=='FlowDocument':
                    entry['style']=styles(node)
                    inline=' '.join(' '.join(node.itertext()).split())
                    if inline: texts.append(inline);flows.append(flow(node))
                ref=node.get('item-idref'); content=parts.get(relationships.get(ref,''))
                if content:
                    if content.startswith(b'\x89PNG\r\n\x1a\n') or content.startswith(b'\xff\xd8\xff'):
                        mime='image/png' if content.startswith(b'\x89PNG') else 'image/jpeg'
                        entry['image']='data:'+mime+';base64,'+base64.b64encode(content).decode();continue
                    try:
                        nested=mcdx.read_package(io.BytesIO(content),budget,1);document=mcdx.read_xml(nested['Xaml/Document.xaml'])
                        texts.append(' '.join(' '.join(document.itertext()).split()));flows.append(flow(document))
                    except (ValueError,KeyError,mcdx.zipfile.BadZipFile):texts.append('[embedded content; native Mathcad view required]')
            entry.update(kind='text',text=' '.join(texts) or '[image or layout region]',flows=flows)
        regions.append(entry)
    return regions


def template_inputs(path, envelope=None, input_map=None):
    """Read literal input candidates and explicit source mappings; never infer design semantics."""
    path=Path(path)
    mcdx.validate(path)
    root=mcdx.read_xml(mcdx.read_package(path)['mathcad/worksheet.xml'])
    definitions=mcdx.input_definitions(root)
    mapping=mcdx.resolve_input_map(root,input_map,required=False)
    rows=[]
    for variable,entries in definitions.items():
        for region,definition in entries:
            try:
                value=float(mcdx.scalar(definition).text)
                if not math.isfinite(value):continue
            except (ValueError,TypeError):continue
            units=[mcdx.name(n) for n in definition[1].iter() if mcdx.tag(n)=='id']
            component=mapping.get(variable)
            ambiguous=len(entries)!=1
            replacement=({'value':envelope[component], 'unit':'kip-in' if component.startswith('M') else 'kip'}
                         if component and envelope is not None and not ambiguous else None)
            rows.append({'variable':variable,'region':region.get('region-id'),'expression':expression(definition),
                         'value':value,'unit':'·'.join(units) or '1','component':component,
                         'replacement':replacement,'status':'ambiguous' if ambiguous else
                         'matched' if replacement else 'selection_required' if component else 'retained'})
    return {'name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'inputs':rows,
            'input_map':mapping,'mapping_required':not mapping,
            'missing_load_inputs':[key for key in mcdx.LOADS if key not in definitions] if input_map is None else [],
            'mapping_basis':'fixed-head pile preset' if input_map is None and mapping else 'explicit input map',
            'native_execution_verified':False}


def template_report(template, report, input_map=None, overrides=None):
    """Preflight the selected template against parsed raw data without generating or calculating."""
    validation={'status':'blocked','errors':[],'warnings':[],
                'native_execution_verified':False,'scope':'Input mapping, units, layout and observed pile capacity; not equation execution or engineering approval'}
    try:
        _,_,_,_,mapped,counts,observed=mcdx.prepare_template(
            template,[{'pile_ids':[report['largest_observed_pile_id']]}],overrides,input_map,require_mapping=False)
        inputs=template_inputs(template,report['envelope'],input_map)
        validation.update(template_pile_count=int(math.prod(counts)) if counts else None,largest_observed_pile_id=observed,
                          status='needs_mapping' if not mapped else 'needs_cases' if not report['selected_cases'] else 'ready')
        if not counts:validation['warnings'].append('No n_z/n_y layout pair; pile capacity is unverified.')
    except ValueError as error:
        validation['errors'].append(str(error))
        inputs=None
    return {'template_inputs':inputs,'template_validation':validation}


def worksheet(path):
    budget=mcdx.Budget();parts=mcdx.read_package(path,budget)
    cache={n.get('result-id'):n.find('m:result',mcdx.NS) for n in mcdx.read_xml(parts['mathcad/result.xml'])}
    regions=read_regions(parts,'mathcad/worksheet.xml',cache,budget)
    page={'width':816,'height':1056,'margins':[48,144,48,48],'paper':'Letter'}
    if 'mathcad/settings/presentation.xml' in parts:
        model=next((n for n in mcdx.read_xml(parts['mathcad/settings/presentation.xml']).iter() if mcdx.tag(n)=='pageModel'),None)
        if model is not None:
            sizes={'Letter':(816,1056),'A4':(793.7,1122.5),'Legal':(816,1344)}
            width,height=sizes.get(model.get('paper-code'),(816,1056))
            if model.get('orientation')=='Landscape':width,height=height,width
            page.update(width=width,height=height,paper=model.get('paper-code'))
            try:
                margins=[float(v) for v in model.get('page-margin','48,144,48,48').split(',')]
                if len(margins)==4 and all(math.isfinite(v) and 0<=v<min(width,height)/2 for v in margins):page['margins']=margins
            except ValueError:pass
    page['content_height']=page['height']-page['margins'][1]-page['margins'][3]
    page['count']=max(1,math.ceil(max((float(r['top'])+float(r['height']) for r in regions),default=0)/page['content_height']))
    header=read_regions(parts,'mathcad/header.xml',{},budget) if 'mathcad/header.xml' in parts else []
    footer=read_regions(parts,'mathcad/footer.xml',{},budget) if 'mathcad/footer.xml' in parts else []
    return {'name':path.name,'regions':regions,'page':page,'header':header,'footer':footer,
            'has_cached_results':any(r.get('cached_result') for r in regions),'native_execution_verified':False,
            'view':'Browser reconstruction from stored region coordinates, text, images and equations; not Prime-native rendering.'}


def diff(template, output):
    before = worksheet(template); after = worksheet(output)
    previous = {r['id']: r for r in before['regions'] if r['kind'] == 'math'}
    changes = []
    for region in after['regions']:
        if region['kind'] != 'math':
            continue
        old = previous.pop(region['id'], None)
        if old is None or old['expression'] != region['expression']:
            changes.append({'kind': 'added' if old is None else 'changed', 'variable': region['variable'],
                            'region': region['id'], 'before': old['expression'] if old else None,
                            'after': region['expression']})
    for old in previous.values():
        changes.append({'kind': 'removed', 'variable': old['variable'], 'region': old['id'],
                        'before': old['expression'], 'after': None})
    return {'changes': changes, 'native_execution_verified': False,
            'scope': 'Native math expression changes. Layout, cached results and text are not compared.'}
