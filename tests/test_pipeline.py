"""Synthetic fixtures only: no engineering files or project identities."""
import io
import tempfile
import time
import unittest
from unittest import mock
import zipfile
from pathlib import Path
from lxml import etree as E

from mcdxkit import group_report, mcdx


def report():
    # Earlier detailed/global values and the service case must not govern STR.
    return '''LOAD CASE : 1
CASE NAME : STR-I
LOAD CASE : 2
CASE NAME : SER-I
LOAD CASE : 7
CASE NAME : STR-V
PILE TOP REACTIONS, LOCAL
MAXIMUM 99999 99999 99999 99999 99999 99999
SUMMARY FOR LOAD CASES AND COMBINATIONS
LOAD CASE : 1
* PILE TOP REACTIONS, GLOBAL *
MAXIMUM 99999 99999 99999 99999 99999 99999
* PILE TOP REACTIONS, LOCAL *
AXIAL,KIP LAT. y,KIP LAT. z,KIP MOM x,KIP-IN MOM y,KIP-IN MOM z,KIP-IN
MINIMUM 90 -20 -4 1 -100 -900
Pile N. 1 1 1 1 1 1
MAXIMUM 120 15 8 1 200 500
Pile N. 3 3 3 3 3 3
* EFFECTS FOR LATERALLY LOADED PILE *
PILE DISPL. DISPL. MOMENT MOMENT SHEAR SHEAR SOIL REACT SOIL REACT TOTAL
y-DIR z-DIR z-DIR y-DIR y-DIR z-DIR y-DIR z-DIR STRESS
IN IN KIP-IN KIP-IN KIP KIP KIP/IN KIP/IN KIP/IN**2
Min. -.1 -.2 -800 -80 -18 -3 -.4 -.5 1
Pile N. 1 1 1 1 1 1 1 1 1
Max. .2 .3 400 180 14 7 .4 .5 10
Pile N. 3 3 3 3 3 3 3 3 3
LOAD CASE : 2
* PILE TOP REACTIONS, LOCAL *
AXIAL,KIP LAT. y,KIP LAT. z,KIP MOM x,KIP-IN MOM y,KIP-IN MOM z,KIP-IN
MINIMUM 800 -200 -40 1 -1000 -9000
MAXIMUM 1000 150 80 1 2000 5000
* EFFECTS FOR LATERALLY LOADED PILE *
y-DIR z-DIR z-DIR y-DIR y-DIR z-DIR y-DIR z-DIR STRESS
IN IN KIP-IN KIP-IN KIP KIP KIP/IN KIP/IN KIP/IN**2
Min. -.1 -.2 -8000 -800 -180 -30 -.4 -.5 1
Max. .2 .3 4000 1800 140 70 .4 .5 10
LOAD CASE : 7
* PILE TOP REACTIONS, LOCAL *
AXIAL,KIP LAT. y,KIP LAT. z,KIP MOM x,KIP-IN MOM y,KIP-IN MOM z,KIP-IN
MINIMUM 100 -25 -5 1 -110 -950
MAXIMUM 140 18 9 1 220 550
* EFFECTS FOR LATERALLY LOADED PILE *
y-DIR z-DIR z-DIR y-DIR y-DIR z-DIR y-DIR z-DIR STRESS
IN IN KIP-IN KIP-IN KIP KIP KIP/IN KIP/IN KIP/IN**2
Min. -.1 -.2 -850 -90 -24 -4 -.4 -.5 1
Max. .2 .3 450 190 16 8 .4 .5 10
'''


def clean_report():
    """Internally consistent variant of report(): along-pile effects reach at least the
    pile-top reactions, and the service case stays below both strength cases."""
    return '''LOAD CASE : 1
CASE NAME : STR-I
LOAD CASE : 2
CASE NAME : SER-I
LOAD CASE : 7
CASE NAME : STR-V
SUMMARY FOR LOAD CASES AND COMBINATIONS
LOAD CASE : 1
* PILE TOP REACTIONS, LOCAL *
AXIAL,KIP LAT. y,KIP LAT. z,KIP MOM x,KIP-IN MOM y,KIP-IN MOM z,KIP-IN
MINIMUM 90 -20 -4 1 -100 -900
MAXIMUM 120 15 8 1 200 500
* EFFECTS FOR LATERALLY LOADED PILE *
y-DIR z-DIR z-DIR y-DIR y-DIR z-DIR y-DIR z-DIR STRESS
IN IN KIP-IN KIP-IN KIP KIP KIP/IN KIP/IN KIP/IN**2
Min. -.1 -.2 -950 -90 -20 -3 -.4 -.5 1
Max. .2 .3 400 210 14 8 .4 .5 10
LOAD CASE : 2
* PILE TOP REACTIONS, LOCAL *
AXIAL,KIP LAT. y,KIP LAT. z,KIP MOM x,KIP-IN MOM y,KIP-IN MOM z,KIP-IN
MINIMUM 70 -15 -3 1 -75 -680
MAXIMUM 90 11 6 1 150 375
* EFFECTS FOR LATERALLY LOADED PILE *
y-DIR z-DIR z-DIR y-DIR y-DIR z-DIR y-DIR z-DIR STRESS
IN IN KIP-IN KIP-IN KIP KIP KIP/IN KIP/IN KIP/IN**2
Min. -.1 -.2 -700 -60 -15 -2 -.4 -.5 1
Max. .2 .3 300 160 10 6 .4 .5 10
LOAD CASE : 7
* PILE TOP REACTIONS, LOCAL *
AXIAL,KIP LAT. y,KIP LAT. z,KIP MOM x,KIP-IN MOM y,KIP-IN MOM z,KIP-IN
MINIMUM 100 -25 -5 1 -110 -950
MAXIMUM 140 18 9 1 220 550
* EFFECTS FOR LATERALLY LOADED PILE *
y-DIR z-DIR z-DIR y-DIR y-DIR z-DIR y-DIR z-DIR STRESS
IN IN KIP-IN KIP-IN KIP KIP KIP/IN KIP/IN KIP/IN**2
Min. -.1 -.2 -1000 -90 -25 -4 -.4 -.5 1
Max. .2 .3 450 230 16 9 .4 .5 10
'''


def template(path):
    W, M = mcdx.W, mcdx.M
    root = E.Element('{'+W+'}worksheet', nsmap={None:W,'ml':M})
    regions = E.SubElement(root,'{'+W+'}regions')
    inputs = [('P_a',10,'kip'),('V_u',1,'kip'),('M_uy',10,'kip'),('M_uz',20,'kip'),
              ('n_z',3,None),('n_y',1,None),('P_downdrag',5,'kip')]
    for i,(name,value,unit) in enumerate(inputs):
        r=E.SubElement(regions,'{'+W+'}region',{'region-id':str(i),'top':str(30+i*40),'left':'10','actualWidth':'120','actualHeight':'24'})
        math=E.SubElement(r,'{'+W+'}math',{'resultRef':str(i)})
        d=E.SubElement(math,'{'+M+'}define');ident=E.SubElement(d,'{'+M+'}id',labels='VARIABLE');ident.text=name
        expr=E.SubElement(d,'{'+M+'}apply') if unit else d
        if unit:E.SubElement(expr,'{'+M+'}mult')
        E.SubElement(expr,'{'+M+'}real').text=str(value)
        if unit:E.SubElement(expr,'{'+M+'}id',labels='UNIT').text=unit
        if name in ('M_uy','M_uz'):
            d.remove(expr)
            d.append(mcdx.quantity(expr,'in'))
    r=E.SubElement(regions,'{'+W+'}region',{'region-id':'50','top':'400','left':'10','actualWidth':'180','actualHeight':'24'})
    math=E.SubElement(r,'{'+W+'}math',{'resultRef':'50'})
    d=E.SubElement(math,'{'+M+'}define');E.SubElement(d,'{'+M+'}id',labels='VARIABLE').text='P_u'
    expr=E.SubElement(d,'{'+M+'}apply');E.SubElement(expr,'{'+M+'}plus')
    for n in ['P_a','P_downdrag']:E.SubElement(expr,'{'+M+'}id',labels='VARIABLE').text=n
    r=E.SubElement(regions,'{'+W+'}region',{'region-id':'51','top':'440','left':'10','actualWidth':'180','actualHeight':'24'})
    math=E.SubElement(r,'{'+W+'}math',{'resultRef':'51'})
    ev=E.SubElement(math,'{'+M+'}eval');E.SubElement(ev,'{'+M+'}id',labels='VARIABLE').text='P_u'
    u=E.SubElement(ev,'{'+M+'}unitOverride');E.SubElement(u,'{'+M+'}id',labels='UNIT').text='kip'
    ct='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="XamlPackage" ContentType="application/zip"/></Types>'
    parts={'mathcad/worksheet.xml':E.tostring(root),'[Content_Types].xml':ct.encode(),
           'mathcad/_rels/worksheet.xml.rels':b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
           'mathcad/result.xml':b'<resultsList xmlns="http://schemas.mathsoft.com/result10"><resultData result-id="51"><value>12345</value></resultData></resultsList>',
           'mathcad/settings/calculation.xml':b'<calculation xmlns="http://schemas.ptc.com/mathcad/settings/calculation10"><calculationBehavior automatic-recalculation="false"/></calculation>'}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for n,b in parts.items():z.writestr(n,b)


class ParserTests(unittest.TestCase):
    def test_final_local_effects_envelope_excludes_service_and_earlier_tables(self):
        parsed=group_report.parse(report());cases=group_report.select(parsed)
        self.assertEqual([c['id'] for c in cases],[1,7])
        self.assertEqual(group_report.envelope(cases),{'P':140,'Vy':24,'Vz':8,'My':190,'Mz':850})

    def test_local_reactions_are_explicitly_distinct_from_pile_effects(self):
        cases=group_report.select(group_report.parse(report(),load_source='reactions'))
        self.assertEqual(group_report.envelope(cases)['Vy'],25)
        self.assertEqual(group_report.envelope(cases)['Mz'],950)

    def test_unknown_case_names_require_explicit_selection(self):
        parsed=group_report.parse(report().split('SUMMARY FOR')[1].join(['SUMMARY FOR','']))
        with self.assertRaisesRegex(ValueError,'cases'):group_report.select(parsed)
        self.assertEqual(len(group_report.select(parsed,[1,7])),2)

    def test_missing_local_extrema_does_not_fall_back_to_global(self):
        bad=report().replace('MAXIMUM 140 18 9 1 220 550','')
        with self.assertRaises(ValueError):group_report.parse(bad)

    def test_missing_final_summary_is_rejected(self):
        with self.assertRaises(ValueError):group_report.parse('LOAD CASE : 1\nMAXIMUM 1 2 3')

    def test_wrong_units_and_duplicate_cases_are_rejected(self):
        with self.assertRaises(ValueError):group_report.parse(report().replace('AXIAL,KIP','AXIAL,KN'))
        with self.assertRaises(ValueError):group_report.parse(report().replace('LOAD CASE : 7\n*','LOAD CASE : 1\n*'))

    def test_every_local_reaction_column_unit_is_checked(self):
        spaced=report().replace('AXIAL,KIP LAT. y,KIP','axial , kip   LAT. y\t, KIP')
        self.assertEqual(group_report.envelope(group_report.select(group_report.parse(spaced,load_source='reactions')))['Vy'],25)
        for old,new in [('LAT. y,KIP','LAT. y,KN'),('MOM y,KIP-IN','MOM y,KIP-FT')]:
            for source in ['effects','reactions']:
                with self.subTest(new=new,source=source),self.assertRaisesRegex(ValueError,'local reaction units'):
                    group_report.parse(report().replace(old,new),load_source=source)

    def test_long_blank_runs_parse_in_linear_time(self):
        # Line regexes once let \s span newlines: 100 KB of blank lines took ~28 s.
        bad='SUMMARY FOR LOAD CASES AND COMBINATIONS\nLOAD CASE: 1\n* PILE TOP REACTIONS, LOCAL *\n'+'\n'*200000+'x'
        start=time.perf_counter()
        with self.assertRaisesRegex(ValueError,'MINIMUM'):group_report.parse(bad)
        self.assertLess(time.perf_counter()-start,2)

    def test_unknown_selection_and_nonfinite_numbers_are_rejected(self):
        with self.assertRaises(ValueError):group_report.select(group_report.parse(report()),[99])
        with self.assertRaises(ValueError):group_report.parse(report().replace('MAXIMUM 140','MAXIMUM 1e999'))


class PackageTests(unittest.TestCase):
    def test_generator_keeps_formulas_clears_cache_and_links_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'template.mcdx';out=Path(folder)/'output.mcdx';template(src)
            before=src.read_bytes();cases=group_report.select(group_report.parse(report()))
            result=mcdx.generate(src,out,cases,source_name='synthetic.gp11t',title='Test')
            self.assertEqual(src.read_bytes(),before)
            self.assertEqual(result['envelope']['P'],140)
            with zipfile.ZipFile(out) as z:
                self.assertEqual(len(E.fromstring(z.read('mathcad/result.xml'))),0)
                root=E.fromstring(z.read('mathcad/worksheet.xml'))
            self.assertGreater(len(root.findall('.//{'+mcdx.M+'}define')),8)
            check=mcdx.validate(out)
            self.assertFalse(check['native_execution_verified'])
            # An independent small interpreter follows the generated native expressions.
            from evaluate_fixture import evaluate
            env=evaluate(out);self.assertEqual(env['P_u'],145)
            self.assertEqual(env['V_u'],24);self.assertEqual(env['M_uz'],850)

    def test_pile_id_beyond_template_capacity_requires_override(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'t.mcdx';out=Path(folder)/'o.mcdx';template(src)
            cases=group_report.select(group_report.parse(report().replace('Pile N. 3','Pile N. 15')))
            with self.assertRaisesRegex(ValueError,'pile'):mcdx.generate(src,out,cases,source_name='x.txt')
            self.assertFalse(out.exists())
            mcdx.generate(src,out,cases,source_name='x.txt',overrides={'n_z':15})
            self.assertTrue(out.exists())

    def test_output_cannot_overwrite_source_or_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'t.mcdx';template(src);before=src.read_bytes()
            cases=group_report.select(group_report.parse(report()))
            with self.assertRaises(ValueError):mcdx.generate(src,src,cases,source_name='x.txt')
            self.assertEqual(src.read_bytes(),before)

    def test_ambiguous_input_definition_and_unknown_override_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'t.mcdx';out=Path(folder)/'o.mcdx';template(src)
            cases=group_report.select(group_report.parse(report()))
            with self.assertRaises(ValueError):mcdx.generate(src,out,cases,source_name='x',overrides={'unknown':2})

    def test_signed_expression_override_and_negative_pile_count_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'t.mcdx';out=Path(folder)/'o.mcdx';template(src)
            with zipfile.ZipFile(src) as z:parts={n:z.read(n) for n in z.namelist()}
            root=E.fromstring(parts['mathcad/worksheet.xml'])
            for d in root.findall('.//{'+mcdx.M+'}define'):
                if d[0].text=='n_z':
                    old=d[1];d.remove(old)
                    neg=E.SubElement(d,'{'+mcdx.M+'}apply');E.SubElement(neg,'{'+mcdx.M+'}neg');neg.append(old)
            parts['mathcad/worksheet.xml']=E.tostring(root)
            with zipfile.ZipFile(src,'w',zipfile.ZIP_DEFLATED) as z:
                for n,b in parts.items():z.writestr(n,b)
            cases=group_report.select(group_report.parse(report()))
            with self.assertRaises(ValueError):mcdx.generate(src,out,cases,source_name='x',overrides={'n_z':5})
            with self.assertRaises(ValueError):mcdx.generate(src,out,cases,source_name='x')

    def test_nested_packages_share_one_decompression_budget(self):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('Xaml/Document.xaml',b'<r/>');z.writestr('zeros.bin',bytes(1024*1024))
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'t.mcdx';template(src)
            with zipfile.ZipFile(src,'a',zipfile.ZIP_STORED) as z:
                for i in range(100):z.writestr('mathcad/xaml/p%d.XamlPackage'%i,inner.getvalue())
            self.assertLess(src.stat().st_size,1024*1024)
            with mock.patch.object(mcdx,'read_package',wraps=mcdx.read_package) as reads:
                with self.assertRaisesRegex(ValueError,'size limits'):mcdx.validate(src)
            self.assertLessEqual(reads.call_count,65)
            with mock.patch.object(mcdx,'MAX_NESTED',3):
                with self.assertRaisesRegex(ValueError,'too many nested'):mcdx.validate(src)
            with self.assertRaisesRegex(ValueError,'too deep'):mcdx.read_package(io.BytesIO(inner.getvalue()),depth=2)


if __name__=='__main__':unittest.main()
