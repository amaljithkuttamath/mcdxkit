"""Input discovery reads the actual template and shares the converter's declared mapping."""
import tempfile
import unittest
import zipfile
import json
import subprocess
import sys
from pathlib import Path

from mcdxkit import engine, mcdx
from test_pipeline import template
from test_summary_only import summary_only


def custom_template(path):
    template(path)
    parts=mcdx.read_package(path);root=mcdx.read_xml(parts['mathcad/worksheet.xml'])
    for node in root.findall('.//m:id',mcdx.NS):
        if node.text=='P_a':node.text='AxialCustom'
        elif node.text=='V_u':node.text='TransverseCustom'
    regions=root.find('w:regions',mcdx.NS)
    for region in list(regions):
        definition=region.find('w:math/m:define',mcdx.NS)
        if definition is not None and mcdx.name(definition[0]) in ('n_z','n_y'):
            regions.remove(region)
    parts['mathcad/worksheet.xml']=mcdx.xml(root)
    with zipfile.ZipFile(path,'w') as archive:
        for name,value in parts.items():archive.writestr(name,value)


class TemplateInputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.template = self.root / 'reference.mcdx'
        template(self.template)

    def test_detects_literal_inputs_excludes_calculated_outputs_and_preserves_file(self):
        original = self.template.read_bytes()
        found = engine.inspect_template(self.template)
        rows = {r['variable']: r for r in found['inputs']}
        self.assertEqual(set(rows), {'P_a', 'V_u', 'M_uy', 'M_uz', 'n_z', 'n_y', 'P_downdrag'})
        self.assertEqual(rows['n_z']['value'], 3)
        self.assertEqual(rows['P_downdrag']['unit'], 'kip')
        self.assertEqual(rows['P_downdrag']['status'], 'retained')
        self.assertEqual(rows['P_a']['component'], 'P')
        self.assertEqual(rows['P_a']['region'], '0')
        self.assertEqual(rows['P_a']['status'], 'selection_required')
        self.assertIsNone(rows['P_a']['replacement'])
        self.assertEqual(found['missing_load_inputs'], [])
        self.assertEqual(self.template.read_bytes(), original)

    def test_matches_summary_to_selected_template_without_replacing_retained_inputs(self):
        source = self.root / 'summary.txt';source.write_text(summary_only())
        found = engine.inspect_report(source, cases=[1, 7], template=self.template, checks='none')
        rows = {r['variable']: r for r in found['template_inputs']['inputs']}
        self.assertEqual(rows['P_a']['replacement'], {'value': 140, 'unit': 'kip'})
        self.assertEqual(rows['M_uy']['replacement'], {'value': 190, 'unit': 'kip-in'})
        self.assertEqual(rows['M_uz']['replacement'], {'value': 850, 'unit': 'kip-in'})
        self.assertEqual(rows['V_u']['replacement'], {'value': 24, 'unit': 'kip'})
        self.assertEqual(rows['n_z']['value'], 3)
        self.assertIsNone(rows['n_z']['replacement'])
        self.assertEqual(rows['P_a']['status'], 'matched')

    def test_unknown_and_duplicate_variables_are_never_guessed_as_load_mappings(self):
        parts = mcdx.read_package(self.template)
        root = mcdx.read_xml(parts['mathcad/worksheet.xml'])
        defs = root.findall('.//m:define', mcdx.NS)
        defs[0][0].text = 'AxialCustom'
        defs[1][0].text = 'n_z'
        parts['mathcad/worksheet.xml'] = mcdx.xml(root)
        with zipfile.ZipFile(self.template, 'w') as archive:
            for name, value in parts.items():archive.writestr(name, value)
        found = engine.inspect_template(self.template)
        self.assertEqual(found['missing_load_inputs'], ['P_a', 'V_u'])
        custom = next(r for r in found['inputs'] if r['variable'] == 'AxialCustom')
        self.assertIsNone(custom['component'])
        self.assertEqual(custom['status'], 'retained')
        duplicates = [r for r in found['inputs'] if r['variable'] == 'n_z']
        self.assertEqual(len(duplicates), 2)
        self.assertTrue(all(r['status'] == 'ambiguous' and r['replacement'] is None for r in duplicates))

    def test_custom_variables_and_six_cases_generate_dynamic_native_inputs(self):
        custom_template(self.template)
        block=summary_only().split('LOAD CASE : 1',1)[1].split('LOAD CASE : 2',1)[0]
        text='SUMMARY FOR LOAD CASES AND COMBINATIONS\n'+''.join(f'LOAD CASE : {i}\n'+block for i in range(1,7))
        source=self.root/'six.txt';source.write_text(text)
        mapping={'AxialCustom':'P','TransverseCustom':'Vz'}
        with self.assertRaisesRegex(ValueError,'map|definition'):
            engine.convert(source,self.template,self.root/'no-map.mcdx',cases=list(range(1,7)))
        result=engine.convert(source,self.template,self.root/'dynamic.mcdx',cases=list(range(1,7)),input_map=mapping)
        self.assertEqual(result['input_map'],mapping)
        self.assertIsNone(result['template_pile_count'])
        from evaluate_fixture import evaluate
        values=evaluate(Path(result['output']))
        self.assertEqual((values['AxialCustom'],values['TransverseCustom'],values['P_u']),(120,7,125))
        self.assertEqual(values['GdWG2'],7)
        self.assertEqual(values['GdP6'],120)
        self.assertTrue(result['calculation']['calculated'])
        self.assertEqual(result['validation']['cached_results'],0)

    def test_bad_or_ambiguous_mappings_fail_before_output(self):
        source=self.root/'summary.txt';source.write_text(summary_only())
        for mapping in ({},{'absent':'P'},{'P_a':'GlobalP'},{'P_u':'P'},['P_a'],{'P_a':None}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                engine.convert(source,self.template,self.root/'invalid.mcdx',cases=[1,7],input_map=mapping)
            self.assertFalse((self.root/'invalid.mcdx').exists())

    def test_custom_mapping_controls_override_protection(self):
        source=self.root/'summary.txt';source.write_text(summary_only())
        with self.assertRaisesRegex(ValueError,'override'):
            engine.convert(source,self.template,self.root/'override.mcdx',cases=[1],
                           input_map={'P_downdrag':'P'},overrides={'P_downdrag':7})

    def test_dimension_mismatch_cannot_map_a_count_or_moment_to_axial_load(self):
        for mapping in ({'n_z':'P'},{'M_uy':'P'},{'V_u':'My'}):
            with self.subTest(mapping=mapping), self.assertRaisesRegex(ValueError,'units'):
                engine.inspect_template(self.template,input_map=mapping)

    def test_all_five_components_have_native_envelopes_within_input_page(self):
        source=self.root/'summary.txt';source.write_text(summary_only())
        mapping={'P_a':'P','V_u':'Vy','P_downdrag':'Vz','M_uy':'My','M_uz':'Mz'}
        result=engine.convert(source,self.template,self.root/'five.mcdx',cases=[1,7],input_map=mapping)
        from evaluate_fixture import evaluate
        self.assertEqual(evaluate(Path(result['output']))['P_u'],148)
        root=mcdx.read_xml(mcdx.read_package(result['output'])['mathcad/worksheet.xml'])
        for region in root.findall('w:regions/w:region',mcdx.NS):
            if float(region.get('top'))<864:
                self.assertLessEqual(float(region.get('top'))+float(region.get('actualHeight')),864)
        from mcdxkit.standards import from_audit
        rows={r['component']:r for r in from_audit(result)['values']}
        self.assertEqual(rows['Vz']['template_mapping'],'P_downdrag')

    def test_pair_preflight_checks_geometry_mapping_and_selection_without_generation(self):
        from unittest.mock import patch
        source=self.root/'summary.txt';source.write_text(summary_only())
        original=self.template.read_bytes()
        with patch('mcdxkit.calcpad.calculate',side_effect=AssertionError('Preflight cannot calculate')):
            needs_cases=engine.inspect_report(source,template=self.template,checks='none')
            self.assertEqual(needs_cases['template_validation']['status'],'needs_cases')
            ready=engine.inspect_report(source,cases=[1,7],template=self.template,checks='none')
            self.assertEqual(ready['template_validation']['status'],'ready')
            self.assertEqual(ready['template_validation']['template_pile_count'],3)
            too_small=engine.inspect_report(source,cases=[1],template=self.template,overrides={'n_z':1},checks='none')
            self.assertEqual(too_small['template_validation']['status'],'blocked')
            self.assertIn('beyond template count',too_small['template_validation']['errors'][0])
            invalid=engine.inspect_report(source,cases=[1],template=self.template,input_map={'n_z':'P'},checks='none')
            self.assertEqual(invalid['template_validation']['status'],'blocked')
            self.assertIn('units',invalid['template_validation']['errors'][0])
            self.assertEqual(self.template.read_bytes(),original)
            custom_template(self.template)
            unmapped=engine.inspect_report(source,cases=[1],template=self.template,checks='none')
            self.assertEqual(unmapped['template_validation']['status'],'needs_mapping')
            custom=engine.inspect_report(source,cases=[1],template=self.template,input_map={'AxialCustom':'P'},checks='none')
            self.assertEqual(custom['template_validation']['status'],'ready')
            self.assertTrue(custom['template_validation']['warnings'])
        self.assertEqual(set(p.name for p in self.root.iterdir()),{'reference.mcdx','summary.txt'})

    def test_cli_discovers_and_maps_custom_template(self):
        custom_template(self.template)
        source=self.root/'summary.txt';source.write_text(summary_only())
        cli=Path(__file__).resolve().parents[1]/'scripts/cli.py'
        def run(*args):
            return subprocess.run([sys.executable,str(cli),*map(str,args)],text=True,capture_output=True)
        inspected=run('inspect',self.template)
        self.assertEqual(inspected.returncode,0,inspected.stderr)
        self.assertTrue(json.loads(inspected.stdout)['mapping_required'])
        output=self.root/'cli.mcdx'
        converted=run('convert',source,'--template',self.template,'--cases','1,7','--output',output,
                      '--map','AxialCustom=P','--map','TransverseCustom=Vz')
        self.assertEqual(converted.returncode,0,converted.stderr)
        self.assertEqual(json.loads(output.with_suffix('.audit.json').read_text())['input_map'],
                         {'AxialCustom':'P','TransverseCustom':'Vz'})
        duplicate=run('inspect',self.template,'--map','AxialCustom=P','--map','AxialCustom=Vy')
        self.assertNotEqual(duplicate.returncode,0)

    def test_batch_resume_uses_mapping_in_job_identity(self):
        from mcdxkit.batch import convert_batch
        custom_template(self.template)
        source=self.root/'summary.txt';source.write_text(summary_only());output=self.root/'batch'
        options=dict(cases=[1,7],input_map={'AxialCustom':'P','TransverseCustom':'Vz'})
        first=convert_batch([source],self.template,output,**options)
        self.assertEqual(first['succeeded'],1)
        again=convert_batch([source],self.template,output,resume=True,**options)
        self.assertEqual(again['skipped'],1)
        options['input_map']['TransverseCustom']='Vy'
        changed=convert_batch([source],self.template,output,resume=True,**options)
        self.assertEqual((changed['succeeded'],changed['skipped']),(1,0))


if __name__ == '__main__':
    unittest.main()
