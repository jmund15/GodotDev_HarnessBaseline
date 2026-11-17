"""Cache post-processing shares one batch's directory reads, never a later batch's."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from threading import Event
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import harness_tests as h


class Postprocess(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='harness_postprocess_')
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        subprocess.run(['git','init','-q',str(self.root)],check=True,capture_output=True)
        self.proof=self.root/'.claude/tests/test_a.py'
        self.proof.parent.mkdir(parents=True)
        self.proof.write_text('pass\n')
        self.listed=self.root/'.claude/listed'
        self.listed.mkdir()
        with patch.object(h,'PASSES_PATH',str(self.root/'passes.json')):
            self.cache=h._PassCache(str(self.root))
        self.cache.case_path=str(self.root/'cases')
        self.manifest={'files':['.claude/tests/test_a.py'],'probes':[],
                       'dirs':['.claude/listed'],'static':[],'git':[],'opaque':False}

    def test_each_directory_is_read_once_per_case_record_batch(self):
        with patch.object(self.cache,'_listing',wraps=self.cache._listing) as listing:
            self.cache.record_cases(str(self.proof),{'one':self.manifest,'two':self.manifest})
        self.assertEqual(listing.call_count,1)
        before=self.cache.reusable_cases(str(self.proof))
        self.assertEqual(set(before),{'one','two'})
        (self.listed/'new.txt').write_text('new')
        self.assertEqual(self.cache.reusable_cases(str(self.proof)),{})
        with patch.object(self.cache,'_listing',wraps=self.cache._listing) as listing:
            self.cache.record_cases(str(self.proof),{'one':self.manifest,'two':self.manifest})
        self.assertEqual(listing.call_count,1)
        self.assertEqual(set(self.cache.reusable_cases(str(self.proof))),{'one','two'})

    def test_non_close_checkout_has_no_exhaust_to_stat(self):
        self.assertFalse(self.cache._close_checkout)
        with patch.object(h.os.path,'lexists',wraps=h.os.path.lexists) as stat:
            self.assertEqual(self.cache._hidden(['.claude/listed','.claude/missing']),set())
        self.assertEqual(stat.call_count,0)

    def test_a_later_proofs_key_does_not_hold_back_the_first_launch(self):
        launched=Event()
        blocked=[]
        later=str(self.proof.parent/'test_b.py')
        def key(path,**kwargs):
            if path==later and not launched.wait(2):
                blocked.append(path)
            return 'key'
        cache=SimpleNamespace(root=str(self.root),key=key,snapshot_git=lambda:None,hit=lambda *a:False)
        def launch(path,*a,**kwargs):
            if path==str(self.proof): launched.set()
            return 'fail',0.01,'FAIL planted'
        with patch.object(h,'DURATIONS_PATH',str(self.root/'durations.json')), \
                patch.object(h,'_behavior_plan',return_value={}),patch.object(h,'_jobs',return_value=2), \
                patch.object(h,'_machine_slots',return_value=0),patch.object(h,'_launch',side_effect=launch), \
                contextlib.redirect_stdout(io.StringIO()):
            h._run_selected([str(self.proof),later],False,options=h.RunOptions(cache=cache))
        self.assertEqual(blocked,[])

    def test_run_accounts_for_time_outside_the_longest_proof(self):
        row={}
        with patch.object(h,'DURATIONS_PATH',str(self.root/'durations.json')), \
                patch.object(h,'_behavior_plan',return_value={}), patch.object(h,'_record_run'), \
                patch.object(h,'_launch',return_value=('pass',0.01,'')), \
                patch.object(h,'_machine_slots',return_value=0), \
                contextlib.redirect_stdout(io.StringIO()):
            h._run_selected([str(self.proof)],False,options=h.RunOptions(mode='for',row=row))
        phases=row.get('phase_s',{})
        self.assertEqual(set(phases),{'setup','pool','serial_wait','serial','retry_wait','retry','post'})
        self.assertAlmostEqual(sum(phases.values()),row['wall_s'],delta=0.11)
        self.assertIn('serial wait',h._phase_line(row))

    def test_manifest_resolves_a_shared_spawn_path_once_but_the_next_manifest_resolves_it_again(self):
        folder=str(self.root.parent/'fixture')
        events=[['s',{'argv':['git',command],'cwd':folder}] for command in ('status','diff')]
        for _ in range(2):
            with patch.object(h.os.path,'realpath',wraps=h.os.path.realpath) as real:
                self.cache.manifest(str(self.proof),{'events':events,'temp':None})
            calls=[c for c in real.call_args_list if c.args[0]==folder]
            self.assertEqual(len(calls),1)

    def test_location_resolves_each_path_only_once(self):
        outside=str(self.root.parent/'unrelated')
        with patch.object(self.cache,'_real',wraps=self.cache._real) as real:
            self.assertIsNone(self.cache._locate(outside))
        self.assertEqual(real.call_count,1)
        self.assertEqual(self.cache._locate(str(self.proof)),'checkout')
        sibling=str(self.root.parent/'peer')
        self.cache._siblings=[self.cache._real(sibling)]
        self.assertEqual(self.cache._locate(sibling),'sibling')
        self.cache._repo_dirs=tuple(self.cache._repo_dirs)+(self.cache._real(outside),)
        self.assertEqual(self.cache._locate(outside),'repository')

    def test_repeated_recorder_events_are_folded_once(self):
        event=['r',str(self.proof)]
        with patch.object(self.cache,'_take',wraps=self.cache._take) as take:
            manifest=self.cache.manifest(str(self.proof),{'events':[event,event],'temp':None})
        self.assertEqual(manifest['files'],['.claude/tests/test_a.py'])
        self.assertEqual(take.call_count,1)

    def test_close_checkout_still_keys_listed_files_and_hides_only_ignored_exhaust(self):
        self.cache._close_checkout=True
        (self.root/'.gitignore').write_text('.claude/listed/exhaust\n')
        (self.listed/'exhaust').write_text('noise')
        (self.listed/'input').write_text('input')
        self.assertEqual(self.cache._listing('.claude/listed'),['input'])


if __name__=='__main__':
    unittest.main()
