"""Growth needs complete, dated time evidence; an old fastest scalar proves no recovery."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import gate_growth as g
import harness_tests as h


def row(ts,a,b=20,c=30):
    return {'ts':ts,'proofs':[{'proof':p,'status':'pass','run_s':s} for p,s in [('a.py',a),('b.py',b),('c.py',c)]]}


class Recovery(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='growth_recovery_')
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.store=self.root/'growth.json'
        self.durations=self.root/'durations.json'
        self.entry={'before_s':10.0,'now_s':30.0,'since':3,'last':4,'closes':2}
        self.seed()

    def seed(self):
        self.store.write_text(json.dumps({'a.py':self.entry}))

    def test_a_faster_raw_proof_cannot_be_lasting_growth_due_to_the_other_proofs_pace(self):
        history=[row(1,20,40,60),row(2,20,40,60),row(3,20,40,60),row(4,15)]
        self.assertEqual(g.grown(row(5,15),history,lasting=True),[])

    def test_old_fastest_scalar_never_closes_a_new_growth_entry(self):
        self.assertIn('a.py',g.reconcile(str(self.store),{'a.py':1.0}))
        self.assertIn('a.py',g.reconcile(str(self.store),{'__last_passes__':{'a.py':{'ts':2,'run_s':1.0}}}))

    def test_real_duration_writer_supplies_a_recent_complete_observation(self):
        with patch.object(h,'DURATIONS_PATH',str(self.durations)),patch.object(h.time,'time',return_value=5):
            h._record_durations({'a.py':('pass',9.0,'')})
        evidence=json.loads(self.durations.read_text())
        self.assertEqual(evidence.get('__last_passes__',{}).get('a.py'),{'ts':5,'run_s':9.0})
        self.assertEqual(g.reconcile(str(self.store),evidence),{})
        self.assertEqual(json.loads(self.store.read_text()),{})

    def test_a_new_loaded_receipt_never_dates_the_old_fastest_scalar_as_new(self):
        with patch.object(h,'DURATIONS_PATH',str(self.durations)):
            with patch.object(h.time,'time',return_value=2):
                h._record_durations({'a.py':('pass',1.0,'')})
            with patch.object(h.time,'time',return_value=5):
                h._record_durations({'a.py':('pass',90.0,'')})
        evidence=json.loads(self.durations.read_text())
        self.assertEqual(evidence['a.py'],1.0)
        self.assertEqual(evidence['__last_passes__']['a.py'],{'ts':5,'run_s':90.0})
        self.assertIn('a.py',g.reconcile(str(self.store),evidence))

    def test_legacy_entries_can_recover_on_a_later_recorded_whole_pass(self):
        history=[row(1,20),row(2,20),row(5,15)]
        self.assertEqual(g.reconcile(str(self.store),{},history),{})

    def test_partial_reused_and_failed_rows_cannot_claim_recovery(self):
        for fields in [{'cases_reused':1},{'reused':True},{'status':'fail'}]:
            observation=row(5,1)
            observation['proofs'][0].update(fields)
            self.assertIn('a.py',g.reconcile(str(self.store),{},[observation]))
        for receipt in [{'ts':5,'run_s':float('nan')},{'ts':5,'run_s':True},{'ts':False,'run_s':1},
                        {'ts':5,'run_s':90}]:
            self.assertIn('a.py',g.reconcile(str(self.store),{'__last_passes__':{'a.py':receipt}}))

    def test_listing_and_commit_advisory_share_the_reconciled_entries(self):
        self.durations.write_text(json.dumps({'__last_passes__':{'a.py':{'ts':5,'run_s':9.0}}}))
        runner=SimpleNamespace(durations_path=lambda:str(self.durations),runs_path=lambda:str(self.root/'absent.json'))
        with patch.object(g,'_runner',return_value=runner):
            self.assertEqual(g.commit_lines(['.claude/tests/a.py'],str(self.store)),[])
            self.seed()
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(g.main(['--store',str(self.store)]),0)
            self.assertIn('no open growth entry',output.getvalue())


if __name__=='__main__':
    unittest.main()
