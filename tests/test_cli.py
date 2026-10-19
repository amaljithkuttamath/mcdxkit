import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from test_pipeline import report, template
from test_summary_only import summary_only

ROOT=Path(__file__).resolve().parents[1]
CLI=ROOT/'scripts/generate.py'

class CliTests(unittest.TestCase):
    def run_cli(self,*args):
        return subprocess.run([sys.executable,str(CLI),*map(str,args)],capture_output=True,text=True)

    def test_summary_only_inspection_and_explicit_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'summary.txt';src.write_text(summary_only())
            ref=root/'template.mcdx';template(ref);out=root/'converted.mcdx'
            inspected=self.run_cli(src,'--inspect')
            self.assertEqual(inspected.returncode,0,inspected.stderr)
            data=json.loads(inspected.stdout)
            self.assertTrue(data['summary_only']);self.assertEqual(data['selected_cases'],[])
            rejected=self.run_cli(src,'--template',ref,'--output',out)
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn('--cases',rejected.stderr);self.assertFalse(out.exists())
            converted=self.run_cli(src,'--template',ref,'--output',out,'--cases','1,7')
            self.assertEqual(converted.returncode,0,converted.stderr)
            audit=json.loads(out.with_suffix('.audit.json').read_text())
            self.assertEqual(audit['envelope'],{'P':140,'Vy':24,'Vz':8,'My':190,'Mz':850})
            self.assertTrue(audit['calculation']['calculated'])
            self.assertEqual(src.read_text(),summary_only())

    def test_inspect_and_generate_report_audit_then_reject_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);src=d/'report.txt';src.write_text(report());t=d/'template.mcdx';template(t)
            inspected=self.run_cli(src,'--inspect')
            self.assertEqual(inspected.returncode,0,inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)['selected_cases'],[1,7])
            out=d/'result.mcdx';r=self.run_cli(src,'--template',t,'--output',out)
            self.assertEqual(r.returncode,0,r.stderr)
            audit=json.loads(out.with_suffix('.audit.json').read_text())
            self.assertEqual(audit['envelope']['P'],140)
            self.assertFalse(audit['validation']['native_execution_verified'])
            before=out.read_bytes()
            self.assertNotEqual(self.run_cli(src,'--template',t,'--output',out).returncode,0)
            self.assertEqual(out.read_bytes(),before)

    def test_missing_template_and_bad_cases_leave_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);src=d/'report.txt';src.write_text(report());out=d/'result.mcdx'
            r=self.run_cli(src,'--template',d/'absent.mcdx','--output',out)
            self.assertNotEqual(r.returncode,0);self.assertFalse(out.exists())
            self.assertNotEqual(self.run_cli(src,'--inspect','--cases','1,99').returncode,0)

    def test_named_cli_inspect_convert_and_validate(self):
        def command(*args):
            return subprocess.run([sys.executable,str(ROOT/'scripts/cli.py'),*map(str,args)],capture_output=True,text=True)
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);src=d/'report.txt';src.write_text(report());t=d/'template.mcdx';template(t);out=d/'output.mcdx'
            self.assertEqual(command('--version').returncode,0)
            a=command('inspect',src);self.assertEqual(a.returncode,0,a.stderr)
            self.assertEqual(json.loads(a.stdout)['selected_cases'],[1,7])
            b=command('convert',src,'--template',t,'--output',out);self.assertEqual(b.returncode,0,b.stderr)
            self.assertGreater(json.loads(b.stdout)['open_checks'],0);self.assertEqual(json.loads(b.stdout)['check_errors'],0)
            c=command('validate',out);self.assertEqual(c.returncode,0,c.stderr)
            self.assertEqual(json.loads(c.stdout)['cached_results'],0)

    def test_checks_option_and_history(self):
        def command(*args):
            return subprocess.run([sys.executable,str(ROOT/'scripts/cli.py'),*map(str,args)],capture_output=True,text=True)
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);src=d/'report.txt';src.write_text(report());t=d/'template.mcdx';template(t)
            on=json.loads(command('inspect',src).stdout)
            self.assertTrue(on['review_checks']['flags']);self.assertEqual(on['case_suggestions']['recommended_cases'],[1,7])
            off=command('inspect',src,'--checks','none');self.assertEqual(off.returncode,0,off.stderr)
            self.assertEqual(json.loads(off.stdout)['review_checks']['checks_run'],[])
            bad=command('inspect',src,'--checks','default,no_such_check')
            self.assertEqual(bad.returncode,2);self.assertIn('Unknown review check',bad.stderr)
            out=d/'none.mcdx';r=command('convert',src,'--template',t,'--output',out,'--checks','none')
            self.assertEqual(r.returncode,0,r.stderr)
            # No check ran: open_checks is null, not zero.
            self.assertEqual({k:json.loads(r.stdout)[k] for k in ('open_checks','check_errors')},{'open_checks':None,'check_errors':0})
            history=command('inspect',src,'--history',d);self.assertEqual(history.returncode,0,history.stderr)
            self.assertEqual(json.loads(history.stdout)['case_suggestions']['trained_on']['history'],3)
            misuse=command('convert',src,'--template',t,'--output',d/'x.mcdx','--history',d)
            self.assertEqual(misuse.returncode,2);self.assertIn('inspect only',misuse.stderr)
            self.assertNotEqual(command('learn',src).returncode,0)

    def test_geometry_accounts_for_excluded_service_cases(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);src=d/'report.txt';t=d/'template.mcdx';out=d/'output.mcdx';template(t)
            # Strength extrema mention piles <=3; the same model's service case reveals pile 15.
            src.write_text(report().replace('MAXIMUM 1000 150 80 1 2000 5000',
                'MAXIMUM 1000 150 80 1 2000 5000\nPile N. 15 15 15 15 15 15'))
            r=self.run_cli(src,'--template',t,'--output',out)
            self.assertNotEqual(r.returncode,0,r.stdout)
            self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
