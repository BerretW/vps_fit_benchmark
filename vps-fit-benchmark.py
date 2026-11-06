#!/usr/bin/env python3
"""VPS Fit Benchmark: non-destructive, local benchmark for mixed hosting workloads.
Requires Python 3.8+, sysbench and fio. Linux only.
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

VERSION = '1.0.0'
PUBLIC_SCHEMA = 1
ISSUES_URL = 'https://github.com/BerretW/vps_fit_benchmark/issues/new'
FIO_TESTS = ('seqwrite_1m', 'seqread_1m', 'randwrite_4k', 'randread_4k',
             'randwrite_fsync_4k')

def command(args, timeout=30):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout, check=False)
        return p.stdout, p.stderr, p.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return '', str(exc), -1

def read(path):
    try:
        return Path(path).read_text(errors='replace')
    except OSError:
        return ''

def proc_meminfo():
    values = {}
    for line in read('/proc/meminfo').splitlines():
        parts = re.match(r'^(\w+):\s+(\d+)\s+kB$', line)
        if parts:
            values[parts.group(1)] = int(parts.group(2)) * 1024
    return values

def cpu_times():
    line = read('/proc/stat').splitlines()[0].split()
    if not line or line[0] != 'cpu':
        return None
    vals = [int(x) for x in line[1:]]
    return sum(vals), vals[7] if len(vals) > 7 else 0

def system_info(path):
    cpuinfo = read('/proc/cpuinfo')
    cpu_model = next((line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
                      if line.lower().startswith('model name')), platform.processor() or 'unknown')
    osr = read('/etc/os-release')
    pretty = re.search(r'^PRETTY_NAME="?([^"\n]*)', osr, re.M)
    virt, _, _ = command(['systemd-detect-virt'], timeout=4) if shutil.which('systemd-detect-virt') else ('', '', 1)
    lsblk_raw, _, _ = command(['lsblk', '-J', '-b', '-o', 'NAME,TYPE,SIZE,ROTA,MODEL,TRAN,FSTYPE,MOUNTPOINTS,DISC-MAX'], timeout=8) if shutil.which('lsblk') else ('', '', 1)
    try:
        disks = json.loads(lsblk_raw).get('blockdevices', [])
    except (ValueError, TypeError):
        disks = []
    nics = []
    for name in os.listdir('/sys/class/net') if os.path.isdir('/sys/class/net') else []:
        if name == 'lo':
            continue
        speed = read('/sys/class/net/%s/speed' % name).strip()
        nics.append({'interface': name, 'speed_mbps_reported': int(speed) if speed.isdigit() else None})
    st = os.statvfs(path)
    mount = ('unknown', 'unknown', 'unknown')
    mounts = []
    for line in read('/proc/self/mountinfo').splitlines():
        if ' - ' not in line:
            continue
        a, b = line.split(' - ', 1)
        l = a.split(); r = b.split()
        if len(l) >= 5 and len(r) >= 2:
            mounts.append((l[4].replace('\\040', ' '), r[0], r[1]))
    abs_path = os.path.realpath(path)
    for mp, fstype, source in sorted(mounts, key=lambda x: len(x[0]), reverse=True):
        if abs_path == mp or abs_path.startswith(mp.rstrip('/') + '/'):
            mount = (mp, fstype, source)
            break
    mem = proc_meminfo()
    return {
        'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'os': pretty.group(1) if pretty else platform.platform(),
        'kernel': platform.release(),
        'architecture': platform.machine(),
        'virtualization': virt.strip() or 'unknown',
        'cpu': {'model': cpu_model, 'logical_cpus': os.cpu_count(),
                'physical_cores_if_visible': _physical_cores(cpuinfo),
                'quota_cores_if_cgroup_limited': _cpu_quota()},
        'memory': {'total_gib': gib(mem.get('MemTotal', 0)), 'available_gib': gib(mem.get('MemAvailable', 0)),
                   'swap_gib': gib(mem.get('SwapTotal', 0)), 'swap_free_gib': gib(mem.get('SwapFree', 0))},
        'test_volume': {'path': abs_path, 'mountpoint': mount[0], 'filesystem': mount[1],
                        'source': mount[2], 'total_gib': gib(st.f_blocks * st.f_frsize),
                        'free_gib': gib(st.f_bavail * st.f_frsize)},
        'block_devices': disks,
        'network_interfaces': nics,
        'uptime_days': round(float(read('/proc/uptime').split()[0]) / 86400, 2) if read('/proc/uptime') else None,
    }

def _physical_cores(cpuinfo):
    pairs = set()
    for part in cpuinfo.strip().split('\n\n'):
        physical = re.search(r'^physical id\s*:\s*(\d+)', part, re.M)
        core = re.search(r'^core id\s*:\s*(\d+)', part, re.M)
        if physical and core:
            pairs.add((physical.group(1), core.group(1)))
    return len(pairs) if pairs else None

def _cpu_quota():
    # Linux cgroup v2, null means not found/unlimited
    v = read('/sys/fs/cgroup/cpu.max').split()
    if len(v) >= 2 and v[0] != 'max':
        try:
            return round(int(v[0])/int(v[1]), 2)
        except (ValueError, ZeroDivisionError):
            pass
    return None

def gib(n):
    return round(n / 1024**3, 2)

def parse_sysbench(output):
    m = re.search(r'events per second:\s*([\d.]+)', output, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r'\(([\d.]+)\s+MiB/sec\)', output, re.I)
    return float(m.group(1)) if m else None

def run_sysbench(test, threads, seconds):
    if test == 'cpu':
        args = ['sysbench', 'cpu', '--cpu-max-prime=20000', '--threads=%d' % threads,
                '--time=%d' % seconds, '--events=0', 'run']
    else:
        args = ['sysbench', 'memory', '--memory-block-size=1M', '--memory-total-size=100T',
                '--memory-oper=write', '--memory-access-mode=seq', '--threads=%d' % threads,
                '--time=%d' % seconds, 'run']
    out, err, rc = command(args, seconds+20)
    if rc:
        return None, err.strip() or out[-500:]
    result = parse_sysbench(out)
    return result, '' if result is not None else 'Unable to parse sysbench output'

def percentile_ns(sub, pct='99.000000'):
    if not isinstance(sub, dict):
        return None
    for k in ('lat_ns', 'clat_ns', 'lat_us', 'clat_us', 'lat_ms', 'clat_ms'):
        x = sub.get(k)
        if isinstance(x, dict) and isinstance(x.get('percentile'), dict):
            p = x['percentile']
            val = p.get(pct) or next((v for key, v in p.items() if abs(float(key)-float(pct)) < 0.01), None)
            if val is not None:
                scale = 1 if k.endswith('_ns') else 1000 if k.endswith('_us') else 1000000
                return round(float(val)*scale/1e6, 3)
    return None

def run_fio(filename, mode, seconds, size_mb, fsync=False):
    args = ['fio', '--name=vps_fit', '--filename=%s' % filename, '--size=%dM' % size_mb,
            '--rw=%s' % mode, '--bs=%s' % ('1M' if mode.startswith('read') or mode.startswith('write') else '4k'),
            '--ioengine=sync', '--iodepth=1', '--numjobs=1', '--direct=1',
            '--runtime=%d' % seconds, '--time_based=1', '--group_reporting=1',
            '--output-format=json', '--eta=never']
    if fsync:
        args += ['--fsync=1']
    out, err, rc = command(args, seconds+35)
    if rc:
        return None, err.strip() or out[-500:]
    try:
        job = json.loads(out)['jobs'][0]
        operation = 'read' if 'read' in mode else 'write'
        data = job[operation]
        value = {'iops': round(float(data.get('iops', 0)), 1),
                 'mib_s': round(float(data.get('bw_bytes', 0))/1048576, 1),
                 'p99_ms': percentile_ns(data),
                 'io_bytes': int(data.get('io_bytes', 0))}
        if fsync:
            value['fsync_p99_ms'] = percentile_ns(job.get('sync', {}))
        return value, ''
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return None, 'Could not parse fio JSON: ' + str(exc)

def sat(v, goal, exponent=0.65):
    if v is None:
        return None
    return round(min(100.0, 100 * max(0, float(v)/goal)**exponent), 1)

def inverse_sat(v, goal):
    if v is None:
        return None
    return round(min(100.0, 100 * (goal/max(float(v), 0.03))**0.48), 1)

def weighted(metrics):
    valid = [(v, w) for v, w in metrics if v is not None]
    return round(sum(v*w for v, w in valid)/sum(w for _, w in valid), 1) if valid else None

def compute_score(info, results):
    cpu = results.get('cpu_single_events_s')
    multi = results.get('cpu_multi_events_s')
    ram_speed = results.get('memory_write_mib_s')
    fio = results.get('fio', {})
    disk_read = fio.get('randread_4k', {}) or {}
    disk_write = fio.get('randwrite_4k', {}) or {}
    disk_sync = fio.get('randwrite_fsync_4k', {}) or {}
    seq = fio.get('seqwrite_1m', {}) or {}
    ram = info['memory']['total_gib']
    free_disk = info['test_volume']['free_gib']
    logical = info['cpu']['logical_cpus'] or 1
    quota = info['cpu']['quota_cores_if_cgroup_limited']
    cores = min(logical, quota) if quota else logical
    steal = results.get('cpu_steal_percent')
    # Fixed reference points, NOT standardized industry scores. Single core sysbench 20k.
    features = {
        'single_core': sat(cpu, 1600),
        'multi_core': sat(multi, 7500),
        'parallel_capacity': sat(cores, 8),
        'ram_speed': sat(ram_speed, 9000),
        'ram_16g': sat(ram, 16),
        'ram_24g': sat(ram, 24),
        'ram_32g': sat(ram, 32),
        'disk_read_iops': sat(disk_read.get('iops'), 25000),
        'disk_write_iops': sat(disk_write.get('iops'), 16000),
        'disk_fsync_iops': sat(disk_sync.get('iops'), 2000),
        'disk_read_p99': inverse_sat(disk_read.get('p99_ms'), 3),
        'disk_fsync_p99': inverse_sat(disk_sync.get('fsync_p99_ms'), 15),
        'disk_seq_write': sat(seq.get('mib_s'), 500),
        'free_disk': sat(free_disk, 100),
        'low_steal': inverse_sat(steal, 3) if steal is not None else None,
    }
    score = {
        'FiveM': weighted([(features['single_core'], .44), (features['multi_core'], .14),
                            (features['ram_16g'], .22), (features['disk_read_p99'], .10),
                            (features['low_steal'], .10)]),
        'Open77_CP2077': weighted([(features['single_core'], .35), (features['multi_core'], .20),
                                   (features['ram_24g'], .24), (features['disk_read_iops'], .10),
                                   (features['low_steal'], .11)]),
        'Databases': weighted([(features['disk_fsync_iops'], .32), (features['disk_fsync_p99'], .20),
                               (features['disk_read_iops'], .14), (features['ram_16g'], .19),
                               (features['single_core'], .08), (features['disk_write_iops'], .07)]),
        'Web_Docker': weighted([(features['multi_core'], .28), (features['parallel_capacity'], .24),
                                (features['ram_16g'], .26), (features['disk_read_iops'], .12),
                                (features['single_core'], .10)]),
        'Headroom': weighted([(features['parallel_capacity'], .27), (features['ram_32g'], .35),
                              (features['free_disk'], .15), (features['multi_core'], .13),
                              (features['low_steal'], .10)]),
    }
    score['Total'] = weighted([(score['FiveM'], 30), (score['Open77_CP2077'], 25),
                               (score['Databases'], 25), (score['Web_Docker'], 10),
                               (score['Headroom'], 10)])
    # If key measurements missing, explicitly suppress headline rating.
    essentials = [cpu, multi, disk_read.get('iops'), disk_sync.get('iops')]
    if any(x is None for x in essentials):
        score['Total'] = None
    return features, score

def rating(v):
    if v is None: return 'NEDOSTATEK DAT'
    if v >= 85: return 'velmi vhodny (orientacne)'
    if v >= 70: return 'vhodny, s rezervami'
    if v >= 50: return 'kompromis / nutne ladit'
    return 'slaby pro soubezny provoz'

def validate_public_report(report):
    def keys(value, expected):
        if not isinstance(value, dict) or set(value) != set(expected):
            raise ValueError('Invalid public report fields: expected ' + ', '.join(expected))

    def number(value, low, high, nullable=False):
        if value is None and nullable:
            return
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError('Public report contains an invalid numeric value')

    def text(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 160 or any(ord(c) < 32 for c in value):
            raise ValueError('Public report contains an invalid text value')

    keys(report, ('public_schema', 'benchmark_version', 'timestamp_utc', 'settings',
                  'system', 'measurements'))
    if type(report['public_schema']) is not int or report['public_schema'] != PUBLIC_SCHEMA:
        raise ValueError('Unsupported public report schema')
    if report['benchmark_version'] != VERSION:
        raise ValueError('Unsupported benchmark version')
    text(report['timestamp_utc'])
    timestamp = dt.datetime.fromisoformat(report['timestamp_utc'])
    if timestamp.utcoffset() != dt.timedelta(0):
        raise ValueError('Report timestamp must use UTC')
    settings = report['settings']
    keys(settings, ('duration', 'size_mb', 'threads'))
    for key, low, high in [('duration', 5, 60), ('size_mb', 128, 4096), ('threads', 1, 32)]:
        number(settings[key], low, high)
        if type(settings[key]) is not int:
            raise ValueError('Test settings must be integers')
    info = report['system']
    keys(info, ('os', 'architecture', 'virtualization', 'cpu', 'memory', 'test_volume'))
    for key in ('os', 'architecture', 'virtualization'):
        text(info[key])
    keys(info['cpu'], ('model', 'logical_cpus', 'quota_cores_if_cgroup_limited'))
    text(info['cpu']['model'])
    number(info['cpu']['logical_cpus'], 1, 65536)
    if type(info['cpu']['logical_cpus']) is not int:
        raise ValueError('Logical CPU count must be an integer')
    number(info['cpu']['quota_cores_if_cgroup_limited'], 0.01, 65536, nullable=True)
    keys(info['memory'], ('total_gib',))
    number(info['memory']['total_gib'], 0, 1e7)
    keys(info['test_volume'], ('filesystem', 'total_gib', 'free_gib'))
    text(info['test_volume']['filesystem'])
    for key in ('total_gib', 'free_gib'):
        number(info['test_volume'][key], 0, 1e10)
    if info['test_volume']['free_gib'] > info['test_volume']['total_gib']:
        raise ValueError('Free disk space exceeds total space')
    results = report['measurements']
    keys(results, ('cpu_single_events_s', 'cpu_multi_events_s', 'memory_write_mib_s',
                   'cpu_steal_percent', 'fio'))
    for key in ('cpu_single_events_s', 'cpu_multi_events_s', 'memory_write_mib_s'):
        number(results[key], 0, 1e12, nullable=True)
    number(results['cpu_steal_percent'], 0, 100, nullable=True)
    keys(results['fio'], FIO_TESTS)
    for name, value in results['fio'].items():
        if value is None:
            continue
        fields = ('iops', 'mib_s', 'p99_ms', 'io_bytes')
        if name == 'randwrite_fsync_4k':
            fields += ('fsync_p99_ms',)
        keys(value, fields)
        for key in fields:
            number(value[key], 0, 1e18, nullable=key.endswith('p99_ms'))
    _, scores = compute_score(info, results)
    if scores['Total'] is None:
        raise ValueError('Incomplete benchmark cannot be submitted to the leaderboard')
    return scores

def public_report(report, duration, size_mb, threads):
    info = report['system']
    public = {
        'public_schema': PUBLIC_SCHEMA, 'benchmark_version': VERSION,
        'timestamp_utc': info['timestamp_utc'],
        'settings': {'duration': duration, 'size_mb': size_mb, 'threads': threads},
        'system': {
            'os': info['os'], 'architecture': info['architecture'],
            'virtualization': info['virtualization'],
            'cpu': {key: info['cpu'][key] for key in
                    ('model', 'logical_cpus', 'quota_cores_if_cgroup_limited')},
            'memory': {'total_gib': info['memory']['total_gib']},
            'test_volume': {key: info['test_volume'][key] for key in
                            ('filesystem', 'total_gib', 'free_gib')}},
        'measurements': report['measurements'],
    }
    validate_public_report(public)
    return public

def write_submission(report, outpath, duration, size_mb, threads):
    public = public_report(report, duration, size_mb, threads)
    payload = json.dumps(public, indent=2, ensure_ascii=False, allow_nan=False)
    body = ('I consent to publishing this benchmark on GitHub and in the public leaderboard.\n'
            'This is a self-reported result, not independently verified.\n\n'
            '```json\n' + payload + '\n```\n')
    public_path = outpath.with_suffix('.public.json')
    issue_path = outpath.with_suffix('.issue.md')
    public_path.write_text(payload + '\n', encoding='utf-8')
    issue_path.write_text(body, encoding='utf-8')
    # Keep the link short; GitHub's URL/body limits vary by browser and proxy.
    url = ISSUES_URL + '?' + urlencode({'title': '[Benchmark] VPS result'})
    print('\nPublic report: %s\nIssue body: %s' % (public_path, issue_path))
    print('Review the public report before sharing. Paste the issue body at:\n' + url)
    print('Nothing has been uploaded. GitHub login and manual submission are required.')

def main():
    p = argparse.ArgumentParser(description='VPS suitability benchmark (Linux, sysbench + fio)')
    p.add_argument('--path', default='.', help='Directory on filesystem to benchmark (default: current directory)')
    p.add_argument('--duration', type=int, default=8, help='Seconds per test, 5-60 (default 8)')
    p.add_argument('--size-mb', type=int, default=512, help='Temporary fio file size, 128-4096 MB')
    p.add_argument('--output', default='vps-fit-report', help='Output filename prefix')
    p.add_argument('--share', action='store_true',
                   help='Prepare an allowlisted public report and GitHub issue body; uploads nothing')
    args = p.parse_args()
    if not sys.platform.startswith('linux'):
        p.error('Linux is required.')
    if not 5 <= args.duration <= 60 or not 128 <= args.size_mb <= 4096:
        p.error('Duration must be 5-60s; size-mb 128-4096.')
    path = Path(args.path).expanduser().resolve()
    if not path.is_dir() or not os.access(str(path), os.W_OK | os.X_OK):
        p.error('Path must exist and be writable: ' + str(path))
    for dep in ['sysbench', 'fio']:
        if not shutil.which(dep):
            p.error('Missing %s. On Debian/Ubuntu: sudo apt update && sudo apt install -y sysbench fio' % dep)
    info = system_info(str(path))
    st = os.statvfs(path)
    free_bytes = st.f_bavail * st.f_frsize
    if free_bytes < args.size_mb * 1048576 + 512 * 1048576:
        p.error('Insufficient free space: require size-mb + 512 MiB safety margin.')
    threads = min(info['cpu']['logical_cpus'] or 1, 32)
    if info['cpu']['quota_cores_if_cgroup_limited']:
        threads = min(threads, max(1, math.ceil(info['cpu']['quota_cores_if_cgroup_limited'])))
    print('VPS FIT %s | %s | %s logical CPUs | %.1f GiB RAM' % (VERSION, info['cpu']['model'], info['cpu']['logical_cpus'] or 0, info['memory']['total_gib']), flush=True)
    print('Test disk: %s (%s, %s), free %.1f GiB' % (path, info['test_volume']['filesystem'], info['test_volume']['source'], info['test_volume']['free_gib']), flush=True)
    print('WARNING: temporary disk I/O causes load; benchmark off-peak. Only benchmark a volume you own.', flush=True)
    print('No destructive raw-device writes. Tests use an isolated temporary file.', flush=True)
    results = {'fio': {}}
    errors = {}
    before = cpu_times()
    for name, test, nth in [('cpu_single_events_s', 'cpu', 1), ('cpu_multi_events_s', 'cpu', threads),
                            ('memory_write_mib_s', 'memory', threads)]:
        print('Testing %s ...' % name, flush=True)
        result, err = run_sysbench(test, nth, args.duration)
        results[name] = result
        if err: errors[name] = err
    after = cpu_times()
    if before and after and after[0] > before[0]:
        results['cpu_steal_percent'] = round(100*(after[1]-before[1])/(after[0]-before[0]), 2)
    else:
        results['cpu_steal_percent'] = None
    try:
        with tempfile.TemporaryDirectory(prefix='.vps-fit-', dir=str(path)) as tmpdir:
            filename = str(Path(tmpdir) / 'data.bin')
            for name, mode, sync in [('seqwrite_1m', 'write', False), ('seqread_1m', 'read', False),
                                     ('randwrite_4k', 'randwrite', False), ('randread_4k', 'randread', False),
                                     ('randwrite_fsync_4k', 'randwrite', True)]:
                print('Testing disk %s ...' % name, flush=True)
                value, err = run_fio(filename, mode, args.duration, args.size_mb, fsync=sync)
                results['fio'][name] = value
                if err: errors[name] = err
    except (OSError, KeyboardInterrupt):
        print('Interrupted or disk error. Temporary files are cleaned up.', file=sys.stderr)
        raise
    features, scores = compute_score(info, results)
    report = {'schema_version': VERSION, 'methodology': {
        'cpu': 'sysbench CPU prime=20000, 1 and N threads, events/s',
        'ram': 'sysbench sequential memory write, MiB/s',
        'storage': 'fio direct=1, sync ioengine, QD1, 1M sequential and 4k random, fsync per random write',
        'scores': 'Heuristic 0-100, NOT validated against real game TPS/player count, reference targets in source',
        'weighting': {'FiveM': 30, 'Open77_CP2077': 25, 'Databases': 25, 'Web_Docker': 10, 'Headroom': 10},
        'limitations': ['Virtualization can hide physical disk type, CPU quota and model.',
                        'Network bandwidth/latency are NOT benchmarked; these scores exclude network.',
                        'Disk test can temporarily affect running services and provider IO quotas.',
                        'Results of short runs do not establish long-term stability or CPU steal peaks.',
                        'Open77 score is a proxy, not based on a published Open77 capacity specification.']},
              'system': info, 'measurements': results, 'normalized_features': features,
              'scores': scores, 'errors': errors}
    outpath = Path(args.output)
    jsonpath = outpath.with_suffix('.json')
    txtpath = outpath.with_suffix('.txt')
    jsonpath.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = ['VPS FIT BENCHMARK — %s' % info['timestamp_utc'],
             '=' * 64, 'System: %s | %s' % (info['os'], info['kernel']),
             'Virtualization: %s' % info['virtualization'],
             'CPU: %s; logical: %s; physical (visible): %s; quota: %s' %
             (info['cpu']['model'], info['cpu']['logical_cpus'], info['cpu']['physical_cores_if_visible'], info['cpu']['quota_cores_if_cgroup_limited']),
             'RAM: %.2f GiB; available: %.2f; swap: %.2f' %
             (info['memory']['total_gib'], info['memory']['available_gib'], info['memory']['swap_gib']),
             'Volume: %s | %s | %s | total/free: %.1f/%.1f GiB' %
             (info['test_volume']['path'], info['test_volume']['filesystem'], info['test_volume']['source'],
              info['test_volume']['total_gib'], info['test_volume']['free_gib']), '',
             'MEASUREMENTS', '-' * 64]
    for key in ['cpu_single_events_s', 'cpu_multi_events_s', 'memory_write_mib_s', 'cpu_steal_percent']:
        lines.append('%-26s %s' % (key, results.get(key)))
    for key, val in results['fio'].items():
        lines.append('%-26s %s' % (key, json.dumps(val, ensure_ascii=False)))
    lines += ['', 'HEURISTIC SUITABILITY SCORE (0-100)', '-' * 64]
    for key, val in scores.items():
        lines.append('%-26s %-6s %s' % (key, ('%.1f' % val if val is not None else 'N/A'), rating(val)))
    lines += ['', 'WARNING: This is an orientation score, NOT a player-count guarantee.',
              'Network is NOT measured. CPU steal measured only during the CPU testing window.',
              'Disk benchmark uses a temporary file and can load live services.',
              'Complete hardware and block-device details: ' + str(jsonpath)]
    if errors:
        lines += ['', 'ERRORS', json.dumps(errors, indent=2, ensure_ascii=False)]
    txtpath.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n' + '\n'.join(lines), flush=True)
    print('\nSaved: %s and %s' % (jsonpath, txtpath))
    if args.share:
        try:
            write_submission(report, outpath, args.duration, args.size_mb, threads)
        except ValueError as exc:
            print('Cannot prepare public submission: ' + str(exc), file=sys.stderr)
            return 2
    return 0 if scores['Total'] is not None else 2

if __name__ == '__main__':
    sys.exit(main())
