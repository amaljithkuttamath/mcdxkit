"""Build native input/formula regions in a compatible Mathcad Prime template."""
import hashlib
import io
import math
import posixpath
import tempfile
import zipfile
from pathlib import Path
from lxml import etree as E
if __package__:
    from .group_report import envelope
else:
    from group_report import envelope  # type: ignore[no-redef]

W='http://schemas.mathsoft.com/worksheet50'
M='http://schemas.mathsoft.com/math50'
X='http://schemas.microsoft.com/winfx/2006/xaml/presentation'
R='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
NS={'w':W,'m':M}
LOADS={'P_a':'P','V_u':'Vy','M_uy':'My','M_uz':'Mz'}
CODES={'P':'P','Vy':'V','Vz':'W','My':'Y','Mz':'Z'}
q=lambda namespace, tag:'{'+namespace+'}'+tag
tag=lambda e:e.tag.split('}')[-1]


def read_xml(content):
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('XML document types/entities are not supported')
    return E.fromstring(content, E.XMLParser(resolve_entities=False, no_network=True))


def xml(e):return E.tostring(e,encoding='utf-8',xml_declaration=True)


def name(e):
    if tag(e)=='Subscript':return '_'+''.join(e.itertext())
    return (e.text or '')+''.join(name(c)+(c.tail or '') for c in e)


MAX_PARTS=3000
MAX_BYTES=64*1024*1024
MAX_NESTED=1000
MAX_DEPTH=1
REQUIRED=['mathcad/worksheet.xml','mathcad/result.xml','[Content_Types].xml',
          'mathcad/_rels/worksheet.xml.rels','mathcad/settings/calculation.xml']


class Budget:
    """Uncompressed bytes and nested packages shared by one package and everything nested in it."""
    def __init__(self):self.bytes=MAX_BYTES;self.nested=MAX_NESTED


def read_package(path,budget=None,depth=0):
    if budget is None:budget=Budget()
    if depth:
        if depth>MAX_DEPTH:raise ValueError('Nested packages are too deep')
        budget.nested-=1
        if budget.nested<0:raise ValueError('Template has too many nested packages')
    with zipfile.ZipFile(path) as z:
        info=z.infolist()
        budget.bytes-=sum(i.file_size for i in info)
        if len(info)>MAX_PARTS or budget.bytes<0:raise ValueError('Template exceeds package size limits')
        if len({i.filename for i in info})!=len(info):raise ValueError('Duplicate package parts')
        if any(i.flag_bits & 1 for i in info):raise ValueError('Encrypted packages are not supported')
        # Reads stop at the declared size and verify each CRC.
        try:return {i.filename:z.read(i) for i in info}
        except zipfile.BadZipFile as error:raise ValueError('Corrupt ZIP package') from error


def validate(path):
    budget=Budget();data=read_package(path,budget)
    if any(p not in data for p in REQUIRED):raise ValueError('Missing required MCDX package part')
    types=read_xml(data['[Content_Types].xml'])
    defaults={e.get('Extension') for e in types if tag(e)=='Default'}
    overrides={e.get('PartName','').lstrip('/') for e in types if tag(e)=='Override'}
    if any(n!='[Content_Types].xml' and n not in overrides and n.rsplit('.',1)[-1] not in defaults for n in data):
        raise ValueError('Package part has no declared content type')
    nested=[n for n in data if n.endswith('.XamlPackage')]
    if len(nested)>MAX_NESTED:raise ValueError('Template has too many nested packages')
    for n,b in data.items():
        if n.endswith(('.xml','.rels')):read_xml(b)
        if n.endswith('.rels'):
            folder=posixpath.dirname(posixpath.dirname(n))
            for rel in read_xml(b):
                if rel.get('TargetMode')=='External':raise ValueError('External package relationships are not supported')
                dest=rel.get('Target','')
                dest=dest.lstrip('/') if dest.startswith('/') else posixpath.normpath(posixpath.join(folder,dest))
                if dest not in data:raise ValueError('Missing relationship target: '+dest)
    for n in nested:
        parts=read_package(io.BytesIO(data[n]),budget,1)
        if 'Xaml/Document.xaml' not in parts:raise ValueError('Missing XAML document')
        for key,value in parts.items():
            if key.endswith(('.xml','.rels','.xaml')):read_xml(value)
    root=read_xml(data['mathcad/worksheet.xml']);regions=root.findall('.//w:region',NS)
    if len({r.get('region-id') for r in regions})!=len(regions):raise ValueError('Duplicate region IDs')
    references=[m.get('resultRef') for m in root.findall('.//w:math',NS) if m.get('resultRef')]
    if len(set(references))!=len(references):raise ValueError('Duplicate math result references')
    relids={e.get('Id') for e in read_xml(data['mathcad/_rels/worksheet.xml.rels'])}
    for e in root.iter():
        if e.get('item-idref') and e.get('item-idref') not in relids:raise ValueError('Unresolved worksheet item reference')
    return {'package_parts':len(data),'math_regions':len(root.findall('.//w:math',NS)),
            'cached_results':len(read_xml(data['mathcad/result.xml'])),
            'native_execution_verified':False,'validation':'ZIP/XML relationships; not PTC schema or native execution'}


def node(t,*children,**attrs):
    e=E.Element(q(M,t),attrs);e.extend(children);return e


def ident(value,label='VARIABLE'):
    e=node('id',labels=label);e.set('{http://www.w3.org/XML/1998/namespace}space','preserve');e.text=value;return e


def real(value):
    e=node('real');e.text=format(value,'.15g');return e


def maximum(*args):return node('apply',ident('max','FUNCTION'),node('sequence',*args))


def quantity(expression,unit):return node('apply',node('mult'),expression,ident(unit,'UNIT'))


def text_package(text):
    section=E.Element(q(X,'Section'),nsmap={None:X},FontFamily='Arial',FontSize='12')
    for line in text.split('\n'):
        p=E.SubElement(section,q(X,'Paragraph'),Margin='0,0,0,0')
        E.SubElement(p,q(X,'Run')).text=line
    types=E.Element(q(CT,'Types'),nsmap={None:CT})
    E.SubElement(types,q(CT,'Default'),Extension='xaml',ContentType='application/vnd.ms-wpf.xaml+xml')
    E.SubElement(types,q(CT,'Default'),Extension='rels',ContentType='application/vnd.openxmlformats-package.relationships+xml')
    rels=E.Element(q(R,'Relationships'),nsmap={None:R})
    E.SubElement(rels,q(R,'Relationship'),Id='rId1',Target='/Xaml/Document.xaml',Type='http://schemas.microsoft.com/wpf/2005/10/xaml/entry')
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as z:
        for n,b in [('Xaml/Document.xaml',xml(section)),('[Content_Types].xml',xml(types)),('_rels/.rels',xml(rels))]:z.writestr(n,b)
    return result.getvalue()


def scalar(definition):
    rhs=definition[1]
    allowed={'apply','real','id','mult','scale'}
    if any(tag(e) not in allowed or (tag(e)=='id' and e.get('labels')!='UNIT') for e in rhs.iter()):
        raise ValueError('Override requires a literal scalar input, with optional units')
    reals=rhs.findall('.//m:real',NS) if tag(rhs)!='real' else [rhs]
    # A powered unit may contain a second literal exponent; do not edit it blindly.
    if len(reals)!=1:raise ValueError('Override is not a single literal scalar')
    return reals[0]


def input_definitions(root):
    """Top-level native definitions only; program locals/functions are not worksheet inputs."""
    definitions={}
    for region in root.findall('w:regions/w:region',NS):
        definition=region.find('w:math/m:define',NS)
        if definition is not None and len(definition)==2 and tag(definition[0])=='id':
            definitions.setdefault(name(definition[0]),[]).append((region,definition))
    return definitions


def resolve_input_map(root, input_map=None, *, required=True):
    definitions=input_definitions(root)
    mapping=dict(LOADS) if input_map is None else input_map
    if not isinstance(mapping,dict) or len(mapping)>100:
        raise ValueError('Input map must be a dictionary of template variables to P, Vy, Vz, My or Mz')
    if input_map is None and not all(len(definitions.get(key,[]))==1 for key in mapping):
        mapping={}
    if required and not mapping:
        raise ValueError('Detect template inputs and provide an input map (--map VARIABLE=COMPONENT)')
    for key,component in mapping.items():
        if not isinstance(key,str) or not key or len(key)>100 or not isinstance(component,str) or component not in CODES:
            raise ValueError('Invalid input map; use template variables and P, Vy, Vz, My or Mz')
        if len(definitions.get(key,[]))!=1 or key.startswith('Gd'):
            raise ValueError('Input map needs one unambiguous template definition of '+key)
        value=float(scalar(definitions[key][0][1]).text)
        if not math.isfinite(value):raise ValueError('Mapped template input must be finite: '+key)
        units=[name(n) for n in definitions[key][0][1][1].iter() if tag(n)=='id']
        force={'kip','lbf','N','kN'};length={'in','ft','m','cm','mm'}
        correct=(len(units)==2 and sum(u in force for u in units)==1 and sum(u in length for u in units)==1
                 if component.startswith('M') else len(units)==1 and units[0] in force)
        if not correct:raise ValueError('Input map has incompatible or unsupported units for '+key+' -> '+component)
    return dict(mapping)


def prepare_template(template, geometry_cases, overrides=None, input_map=None, *, require_mapping=True):
    """Read and validate source/template compatibility; mutate only an in-memory XML copy."""
    validate(template)
    data=read_package(template);root=read_xml(data['mathcad/worksheet.xml'])
    regions=root.find('w:regions',NS)
    if regions is None:raise ValueError('Unsupported worksheet structure')
    if root.find('.//w:regions/w:region/w:area',NS) is not None:raise ValueError('Nested areas need a custom template adapter')
    definitions={}
    for d in root.findall('.//m:define',NS):definitions.setdefault(name(d[0]),[]).append(d)
    mapped=resolve_input_map(root,input_map,required=require_mapping)
    count_keys=['n_z','n_y'] if (input_map is None and mapped) or all(k in definitions for k in ('n_z','n_y')) else []
    for key in [*mapped,*count_keys]:
        if len(definitions.get(key,[]))!=1:raise ValueError('Template needs one unambiguous definition of '+key)
    if any(n.startswith('Gd') for n in definitions):raise ValueError('Use a clean template without reserved Gd variables, not an already generated worksheet')
    overrides={} if overrides is None else overrides
    if not isinstance(overrides,dict) or len(overrides)>50:raise ValueError('Overrides must be a dictionary of template inputs')
    for key,value in overrides.items():
        if key in mapped or len(definitions.get(key,[]))!=1:raise ValueError('Unknown, ambiguous, or report-controlled override: '+key)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Overrides must be finite and nonnegative')
        if key in ('n_z','n_y') and (value<1 or value!=int(value)):raise ValueError('Pile counts must be positive integers')
        scalar(definitions[key][0]).text=format(value,'.15g')
    counts=[float(scalar(definitions[k][0]).text) for k in count_keys]
    if any(n<1 or not n.is_integer() for n in counts):raise ValueError('Invalid template pile counts')
    observed=max((p for c in geometry_cases for p in c['pile_ids']),default=0)
    if counts and observed>math.prod(counts):
        raise ValueError(f'Local summary references pile {observed}, beyond template count {int(math.prod(counts))}; review geometry and explicitly override n_z/n_y')
    presentation=data.get('mathcad/settings/presentation.xml')
    if presentation:
        page=next((e for e in read_xml(presentation).iter() if tag(e)=='pageModel'),None)
        if page is not None and (page.get('paper-code')!='Letter' or page.get('orientation')!='Portrait' or page.get('page-margin')!='48,144,48,48'):
            raise ValueError('This adapter requires Letter portrait with page-margin 48,144,48,48')
    return data,root,regions,definitions,mapped,counts,observed


def generate(template, output, cases, source_name, title=None, overrides=None, geometry_cases=None, review_note=None, input_map=None):
    template=Path(template).resolve();output=Path(output).resolve()
    if output==template or output.exists():raise ValueError('Output must be a new file, distinct from the template')
    if not cases or len(cases)>100:raise ValueError('Select between 1 and 100 load cases')
    data,root,regions,definitions,mapped,counts,observed=prepare_template(
        template,geometry_cases if geometry_cases is not None else cases,overrides,input_map)
    overrides=overrides or {}
    original=list(regions);pages=math.ceil(len(cases)/5)
    all_ids=[int(r.get('region-id')) for r in root.findall('.//w:region',NS)]
    rid=max(all_ids+[0])+1
    result_ids=[int(e.get('resultRef')) for e in root.iter() if e.get('resultRef')]
    resultid=max(result_ids+[0])+1
    rels=read_xml(data['mathcad/_rels/worksheet.xml.rels'])
    types=read_xml(data['[Content_Types].xml'])
    if not any(e.get('Extension')=='XamlPackage' for e in types):
        E.SubElement(types,q(CT,'Default'),Extension='XamlPackage',ContentType='application/zip')
    inserted=[]
    def region(top,left,width,height):
        nonlocal rid
        r=E.Element(q(W,'region'),{'region-id':str(rid),'top':str(top),'left':str(left),'actualWidth':str(width),'actualHeight':str(height)})
        rid+=1;inserted.append(r);return r
    def text_region(text,top,height=20):
        r=region(top,9.6,715,height);r.set('width','715');ref='Rmcdxkit'+r.get('region-id')
        t=E.SubElement(r,q(W,'text'),{'item-idref':ref})
        E.SubElement(t,q(X,'FlowDocument'),FontFamily='Arial',FontSize='12')
        part='mathcad/xaml/mcdxkit'+r.get('region-id')+'.XamlPackage';data[part]=text_package(text)
        E.SubElement(rels,q(R,'Relationship'),Id=ref,Target='/'+part,Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/flowDocument')
    def math_region(variable,rhs,top,left):
        nonlocal resultid
        r=region(top,left,345,27);m=E.SubElement(r,q(W,'math'),resultRef=str(resultid));resultid+=1
        m.append(node('define',ident(variable),rhs))
        E.SubElement(m,q(W,'formattingOverride'),BackgroundColor='#FFC6E7ED')
    title=(title or 'LOCAL LOAD INPUTS')[:80]
    source_name=Path(source_name).name[:100]
    mapping=cases[0]['load_source']
    for page in range(pages):
        offset=page*864;batch=cases[page*5:(page+1)*5]
        text_region(title+f' — {page+1}/{pages}',offset+9.6)
        text_region(f'Source: {source_name}. Final local summary; selected cases only.\n'
                    f'P: axial compression. Vy/Vz/My/Mz: {mapping} magnitudes. Native max formulas below.\n'
                    'Inherited design assumptions require review. Recalculate in Mathcad (Ctrl+F5).',offset+33.6,48)
        for row,c in enumerate(batch):
            y=offset+96+row*134.4;index=page*5+row+1
            text_region(f'Case {c["id"]}: {(c["name"] or "explicit selection")[:65]} | local extrema magnitudes',y)
            for key,x,dy in [('P',9.6,28.8),('Vy',374.4,28.8),('Vz',9.6,57.6),('My',374.4,57.6),('Mz',9.6,86.4)]:
                pair=c['pairs'][key];pair=pair if key=='P' else list(map(abs,pair))
                rhs=quantity(maximum(*map(real,pair)),'kip')
                if key.startswith('M'):rhs=quantity(rhs,'in')
                math_region(f'Gd{CODES[key]}{index}',rhs,y+dy,x)
        components=[key for key in CODES if key in mapped.values()]
        for i,key in enumerate(components):
            args=[ident(f'Gd{CODES[key]}{j}') for j in range(page*5+1,page*5+len(batch)+1)]
            if page:args.insert(0,ident(f'Gd{CODES[key]}G{page}'))
            # Single-argument max is kept as an identity reference for portability.
            rhs=maximum(*args) if len(args)>1 else args[0]
            top=784+(i//2)*26 if len(components)==5 else 787.2+(i//2)*28.8
            math_region(f'Gd{CODES[key]}G{page+1}',rhs,offset+top,9.6 if i%2==0 else 374.4)
    if review_note:
        # Plain text only, created last so every other region keeps its id; it sits in the gap
        # between the last case row and the envelope formulas of the last generated page.
        text_region(str(review_note)[:400],(pages-1)*864+752.0,32)
    for key,load in mapped.items():
        d=definitions[key][0];d.remove(d[1]);d.append(ident(f'Gd{CODES[load]}G{pages}'))
    for r in original:r.set('top',str(float(r.get('top'))+pages*864))
    for r in list(regions):regions.remove(r)
    regions.extend(sorted(inserted+original,key=lambda r:(float(r.get('top')),float(r.get('left')))))
    results=read_xml(data['mathcad/result.xml'])
    for c in list(results):results.remove(c)
    calc=read_xml(data['mathcad/settings/calculation.xml'])
    behavior=next((e for e in calc if tag(e)=='calculationBehavior'),None)
    if behavior is None:raise ValueError('Missing calculation behavior settings')
    behavior.set('automatic-recalculation','true')
    data.update({'mathcad/worksheet.xml':xml(root),'mathcad/result.xml':xml(results),
                 'mathcad/_rels/worksheet.xml.rels':xml(rels),'[Content_Types].xml':xml(types),
                 'mathcad/settings/calculation.xml':xml(calc)})
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent,suffix='.mcdx',delete=False) as f:temp=Path(f.name)
    try:
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
            for n,b in data.items():z.writestr(n,b)
        validation=validate(temp)
        # Exclusive creation prevents accidentally replacing another run's result.
        with output.open('xb') as dest:dest.write(temp.read_bytes())
    finally:temp.unlink(missing_ok=True)
    return {'output':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'template_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),
            'source':source_name,'cases':[c['id'] for c in cases],'load_source':mapping,
            'envelope':envelope(cases),'overrides':overrides,'largest_observed_pile_id':observed,
            'template_pile_count':int(math.prod(counts)) if counts else None,'input_map':mapped,'validation':validation,
            'notes':['Separate component envelopes; not concurrent pile/case loads.',
                     'Report components feed the recorded template input map; unmapped template values are retained.',
                     'Pile count checked from n_z/n_y.' if counts else 'No n_z/n_y layout pair; pile capacity was not checked.',
                     'Template geometry, materials, headers and dates are inherited unless explicitly changed.',
                     'No cached output values. Native Mathcad opening, rendering and execution are unverified.'],
            'source_cases':cases}
