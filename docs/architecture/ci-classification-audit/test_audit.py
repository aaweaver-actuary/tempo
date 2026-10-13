"""Deterministic accounting protections for the historical audit; no live API calls."""
import unittest
from audit import interval_union, job_duration, percentile, seconds, prose_files
from recover import checkout_sha, revision_records
from opportunities import replay_elapsed


class AccountingTests(unittest.TestCase):
    def test_parallel_jobs_do_not_double_count_elapsed_wait(self):
        self.assertEqual(interval_union([(0, 10), (2, 12), (20, 25)]), 17)
        self.assertEqual(sum(end - start for start, end in [(0, 10), (2, 12), (20, 25)]), 25)

    def test_skipped_unassigned_jobs_do_not_consume_runner_time(self):
        self.assertEqual(job_duration({'conclusion': 'skipped', 'runner_id': 42}), 0)
        self.assertEqual(job_duration({'conclusion': 'cancelled', 'runner_id': 0}), 0)

    def test_failed_cancelled_and_retried_execution_remains_compute(self):
        for conclusion in ('failure', 'cancelled', 'success'):
            self.assertEqual(job_duration({'conclusion': conclusion, 'runner_id': 42,
                'started_at': '2026-10-13T00:00:00Z', 'completed_at': '2026-10-13T00:01:00Z'}), 60)

    def test_missing_or_reversed_timestamps_are_not_successful_zeroes(self):
        self.assertIsNone(seconds(None, None))
        self.assertIsNone(seconds('2026-10-13T00:01:00Z', '2026-10-13T00:00:00Z'))
        self.assertIsNone(job_duration({'runner_id': 42, 'conclusion': None}))

    def test_nearest_rank_percentiles_and_empty_population(self):
        self.assertEqual(percentile(list(range(1, 11)), .9), 9)
        self.assertIsNone(percentile([], .5))

    def test_markdown_controls_exclude_executable_evidence(self):
        self.assertTrue(prose_files([{'filename': 'docs/a.md'}]))
        self.assertFalse(prose_files([{'filename': 'docs/a.md'}, {'filename': 'docs/a.py'}]))
        self.assertFalse(prose_files([]))

    def test_cutoff_censors_running_and_later_completed_jobs(self):
        job = {'runner_id': 42, 'conclusion': None, 'started_at': '2026-10-13T00:00:00Z'}
        self.assertEqual(job_duration(job, '2026-10-13T00:01:00Z'), 60)
        job['completed_at'] = '2026-10-13T00:05:00Z'
        self.assertEqual(job_duration(job, '2026-10-13T00:01:00Z'), 60)
        self.assertEqual(job_duration(job, '2026-10-12T23:59:00Z'), 0)

    def test_checkout_candidate_requires_the_logged_git_head(self):
        sha = 'a' * 40
        self.assertEqual(checkout_sha('2026-10-13 /usr/bin/git log -1 --format=%H\n2026-10-13 ' + sha), sha)
        self.assertIsNone(checkout_sha('unrelated fetch ' + sha))

    def test_checkout_recovery_does_not_assign_a_base_to_other_planless_runs(self):
        shared={'pr':180,'primary':True,'head':'a'*40,'draft':True,'tier':'UNKNOWN','created_at':'2026-10-13T00:00:00Z','gaps':['immutable_plan_unavailable'],'attempt':1}
        attempts=[{**shared,'run_id':1,'base':'b'*40,'integration':'c'*40,'candidate_source':'checkout log job 42'},
                  {**shared,'run_id':2,'base':None,'integration':None}]
        records=revision_records(attempts,[])
        self.assertEqual(len(records),2)
        unresolved=next(record for record in records if record['runs']==[[2,1]])
        self.assertIsNone(unresolved['base'])
        self.assertIsNone(unresolved['integration'])

    def test_noncritical_parallel_removal_does_not_reduce_gate_wait(self):
        def job(name, start, end, identity):
            return {'name':name,'started_at':f'2026-10-13T00:00:{start:02}Z','completed_at':f'2026-10-13T00:00:{end:02}Z','runner_seconds':end-start,'id':identity}
        jobs = [job('plan',0,2,1),job('frontend / verify',2,8,2),job('backend / verify',2,12,3),job('quality',12,13,4)]
        attempt = {'started_at':'2026-10-13T00:00:00Z','last_job_completed_at':'2026-10-13T00:00:13Z'}
        self.assertEqual(replay_elapsed(attempt,jobs,{}),13)
        self.assertEqual(replay_elapsed(attempt,jobs,{2:0}),13)
        self.assertEqual(replay_elapsed(attempt,jobs,{3:0}),9)

    def test_unknown_workflow_graph_is_not_an_elapsed_savings_estimate(self):
        job={'name':'future / verify','started_at':'2026-10-13T00:00:00Z','completed_at':'2026-10-13T00:00:10Z','runner_seconds':10,'id':1}
        gate={'name':'quality','started_at':'2026-10-13T00:00:10Z','completed_at':'2026-10-13T00:00:11Z','runner_seconds':1,'id':2}
        self.assertIsNone(replay_elapsed({'started_at':job['started_at'],'last_job_completed_at':gate['completed_at']},[job,gate],{1:0}))


if __name__ == '__main__':
    unittest.main()
