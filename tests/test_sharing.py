import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = load_module('benchmark_test', ROOT / 'vps-fit-benchmark.py')
collector = load_module('collector_test', ROOT / '.github' / 'scripts' / 'collect-results.py')


def sample_report():
    disk = {'iops': 2000.0, 'mib_s': 500.0, 'p99_ms': 2.0, 'io_bytes': 1048576}
    fio = {name: dict(disk) for name in benchmark.FIO_TESTS}
    fio['randwrite_fsync_4k']['fsync_p99_ms'] = 10.0
    return {
        'system': {
            'timestamp_utc': '2026-11-06T17:00:00+00:00',
            'os': 'Debian GNU/Linux 12', 'architecture': 'x86_64', 'virtualization': 'kvm',
            'kernel': 'private-kernel',
            'cpu': {'model': 'Test CPU', 'logical_cpus': 8,
                    'quota_cores_if_cgroup_limited': None, 'physical_cores_if_visible': 4},
            'memory': {'total_gib': 16.0, 'available_gib': 12.0, 'swap_gib': 0.0},
            'test_volume': {'filesystem': 'ext4', 'total_gib': 200.0, 'free_gib': 100.0,
                            'path': '/private/volume', 'source': '/dev/private-device'},
            'network_interfaces': [{'interface': 'private-interface'}],
            'block_devices': [{'name': 'private-device'}]},
        'measurements': {'cpu_single_events_s': 1600.0, 'cpu_multi_events_s': 7500.0,
                         'memory_write_mib_s': 9000.0, 'cpu_steal_percent': 1.0, 'fio': fio},
        'errors': {'sample': 'private-error'},
    }


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.local = sample_report()
        self.public = benchmark.public_report(self.local, 8, 512, 8)

    def test_public_allowlist_preserves_scoring_and_local_report(self):
        original = copy.deepcopy(self.local)
        serialized = json.dumps(self.public)
        for secret in ('private-', 'available_gib', 'physical_cores_if_visible',
                       'network_interfaces', 'block_devices', 'errors'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(original, self.local)
        self.assertEqual(benchmark.compute_score(self.local['system'], self.local['measurements'])[1],
                         benchmark.validate_public_report(self.public))

    def test_submission_files_round_trip_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / 'test'
            with contextlib.redirect_stdout(io.StringIO()) as output:
                benchmark.write_submission(self.local, prefix, 8, 512, 8)
            body = prefix.with_suffix('.issue.md').read_text(encoding='utf-8')
            public = json.loads(prefix.with_suffix('.public.json').read_text(encoding='utf-8'))
            self.assertEqual(public, collector.parse_submission(body))
            self.assertIn('Nothing has been uploaded', output.getvalue())
            self.assertIn(benchmark.ISSUES_URL, output.getvalue())

    def test_invalid_reports_are_rejected(self):
        cases = []
        for value in (True, float('nan'), float('inf'), -1, '1600', 10**400):
            report = copy.deepcopy(self.public)
            report['measurements']['cpu_single_events_s'] = value
            cases.append(report)
        for key, value in [('duration', 4), ('duration', 61), ('duration', 5.5),
                           ('size_mb', 127), ('size_mb', 4097), ('threads', 33)]:
            report = copy.deepcopy(self.public)
            report['settings'][key] = value
            cases.append(report)
        for key, value in [('public_schema', True), ('benchmark_version', 'unknown'),
                           ('timestamp_utc', 'not-a-date')]:
            report = copy.deepcopy(self.public)
            report[key] = value
            cases.append(report)
        report = copy.deepcopy(self.public)
        report['system']['cpu']['model'] = 'CPU\n| injected row'
        cases.append(report)
        report = copy.deepcopy(self.public)
        report['system']['test_volume']['path'] = '/private'
        cases.append(report)
        report = copy.deepcopy(self.public)
        report['measurements']['fio']['randread_4k'] = None
        cases.append(report)
        report = copy.deepcopy(self.public)
        report['system']['test_volume']['free_gib'] = 201
        cases.append(report)
        for report in cases:
            with self.subTest(report=report), self.assertRaises(ValueError):
                benchmark.validate_public_report(report)

    def test_setting_boundaries(self):
        for duration, size, threads in ((5, 128, 1), (60, 4096, 32)):
            public = benchmark.public_report(self.local, duration, size, threads)
            benchmark.validate_public_report(public)

    def test_fences_and_body_size(self):
        body = '```json\n' + json.dumps(self.public) + '\n```\n'
        for invalid in (None, '', '{}', body + body, 'x' * 32769):
            with self.subTest(body=invalid), self.assertRaises(ValueError):
                collector.parse_submission(invalid)

    def test_leaderboard_escapes_text_and_sorts_recomputed_scores(self):
        slow = copy.deepcopy(self.public)
        slow['measurements']['cpu_single_events_s'] = 1
        self.public['system']['cpu']['model'] = '<script>x</script>|`CPU`![image](https://example.com)'
        rendered = collector.render_leaderboard([
            {'issue_number': 2, 'report': slow}, {'issue_number': 1, 'report': self.public}])
        self.assertLess(rendered.index('[#1]'), rendered.index('[#2]'))
        self.assertNotIn('<script>', rendered)
        self.assertIn('&#124;', rendered)
        self.assertIn('&#96;', rendered)
        self.assertNotIn('![image]', rendered)
        self.assertIn('No approved results yet.', collector.render_leaderboard([]))

    def test_collector_persists_approved_result_and_is_idempotent(self):
        body = '```json\n' + json.dumps(self.public) + '\n```\n'
        issue = {'body': body, 'labels': [{'name': 'benchmark-approved'}]}
        event = {'action': 'labeled', 'label': {'name': 'benchmark-approved'},
                 'sender': {'login': 'BerretW'}, 'issue': {'number': 42, 'body': body}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_path = root / 'event.json'
            event_path.write_text(json.dumps(event), encoding='utf-8')
            env = {'GITHUB_EVENT_PATH': str(event_path),
                   'GITHUB_REPOSITORY': 'BerretW/vps_fit_benchmark', 'GITHUB_TOKEN': 'test-only'}
            for _ in range(2):
                response = io.BytesIO(json.dumps(issue).encode('utf-8'))
                with patch.object(collector, 'ROOT', root), patch.dict(os.environ, env), \
                        patch.object(collector, 'urlopen', return_value=response):
                    collector.main()
            stored = json.loads((root / 'results' / 'issue-42.json').read_text(encoding='utf-8'))
            self.assertEqual(stored, {'issue_number': 42, 'report': self.public})
            self.assertEqual(len(list((root / 'results').glob('*.json'))), 1)
            self.assertIn('[#42]', (root / 'RESULTS.md').read_text(encoding='utf-8'))

    def test_collector_rejects_untrusted_approver_and_post_approval_edits(self):
        body = '```json\n' + json.dumps(self.public) + '\n```\n'
        event = {'action': 'labeled', 'label': {'name': 'benchmark-approved'},
                 'sender': {'login': 'outsider'}, 'issue': {'number': 42, 'body': body}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_path = root / 'event.json'
            env = {'GITHUB_EVENT_PATH': str(event_path),
                   'GITHUB_REPOSITORY': 'BerretW/vps_fit_benchmark', 'GITHUB_TOKEN': 'test-only'}
            for sender, issue in [
                    ('outsider', {'body': body, 'labels': [{'name': 'benchmark-approved'}]}),
                    ('BerretW', {'body': body + 'edit', 'labels': [{'name': 'benchmark-approved'}]}),
                    ('BerretW', {'body': body, 'labels': []}),
                    ('BerretW', {'body': body, 'labels': [{'name': 'benchmark-approved'}],
                                 'pull_request': {}})]:
                event['sender']['login'] = sender
                event_path.write_text(json.dumps(event), encoding='utf-8')
                response = io.BytesIO(json.dumps(issue).encode('utf-8'))
                with patch.object(collector, 'ROOT', root), patch.dict(os.environ, env), \
                        patch.object(collector, 'urlopen', return_value=response), \
                        self.assertRaises(ValueError):
                    collector.main()
                self.assertFalse((root / 'results').exists())

    def test_main_preserves_local_outputs_and_opt_in_sharing(self):
        for share in (False, True):
            with self.subTest(share=share), tempfile.TemporaryDirectory() as directory:
                prefix = Path(directory) / 'report'
                args = ['vps-fit-benchmark.py', '--path', directory, '--output', str(prefix)]
                if share:
                    args.append('--share')
                stat = SimpleNamespace(f_bavail=4096 * 1024, f_frsize=1024)
                with patch.object(benchmark.sys, 'argv', args), \
                        patch.object(benchmark.sys, 'platform', 'linux'), \
                        patch.object(benchmark, 'system_info', return_value=self.local['system']), \
                        patch.object(benchmark.os, 'statvfs', return_value=stat, create=True), \
                        patch.object(benchmark.shutil, 'which', return_value='/usr/bin/tool'), \
                        patch.object(benchmark, 'cpu_times', side_effect=[(100, 0), (200, 1)]), \
                        patch.object(benchmark, 'run_sysbench',
                                     side_effect=[(1600.0, ''), (7500.0, ''), (9000.0, '')]), \
                        patch.object(benchmark, 'run_fio',
                                     side_effect=[(self.local['measurements']['fio'][name], '')
                                                  for name in benchmark.FIO_TESTS]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(benchmark.main(), 0)
                self.assertTrue(prefix.with_suffix('.json').exists())
                self.assertTrue(prefix.with_suffix('.txt').exists())
                self.assertEqual(prefix.with_suffix('.public.json').exists(), share)
                self.assertEqual(prefix.with_suffix('.issue.md').exists(), share)
                local = json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8'))
                self.assertIn('path', local['system']['test_volume'])
                self.assertEqual(local['scores']['Total'],
                                 benchmark.compute_score(local['system'], local['measurements'])[1]['Total'])


if __name__ == '__main__':
    unittest.main()
