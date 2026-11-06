import html
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('benchmark', ROOT / 'vps-fit-benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def parse_submission(body):
    if not isinstance(body, str) or len(body.encode('utf-8')) > 32768:
        raise ValueError('Issue body is missing or exceeds 32 KiB')
    blocks = re.findall(r'^```json\s*\n(.*?)^```\s*$', body, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError('Expected exactly one fenced JSON report')
    report = json.loads(blocks[0])
    benchmark.validate_public_report(report)
    return report


def cell(value):
    escaped = html.escape(str(value), quote=True)
    return ''.join('&#%d;' % ord(c) if c in '\\|`[]()!*_~' else c for c in escaped)


def render_leaderboard(records):
    rows = []
    for record in records:
        report = record['report']
        scores = benchmark.validate_public_report(report)
        rows.append((scores['Total'], record, scores))
    rows.sort(key=lambda row: (-row[0], row[1]['issue_number']))
    lines = [
        '# Community VPS results', '',
        'Self-reported, maintainer-approved submissions; not independently verified.',
        'Scores are heuristic, not player-count guarantees. Test duration and file size',
        'can affect comparability. One entry per approved issue.', '',
        '| Issue | CPU | vCPU | RAM GiB | Duration s | File MiB | Total | FiveM | Open77 | Databases | Web/Docker | Headroom |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for _, record, scores in rows:
        report = record['report']
        info = report['system']
        issue = record['issue_number']
        fields = [cell(info['cpu']['model']), str(info['cpu']['logical_cpus']),
                  str(info['memory']['total_gib']), str(report['settings']['duration']),
                  str(report['settings']['size_mb'])]
        fields += ['N/A' if scores[key] is None else '%.1f' % scores[key] for key in
                   ('Total', 'FiveM', 'Open77_CP2077', 'Databases', 'Web_Docker', 'Headroom')]
        lines.append('| [#%d](https://github.com/BerretW/vps_fit_benchmark/issues/%d) | %s |' %
                     (issue, issue, ' | '.join(fields)))
    if not rows:
        lines += ['', 'No approved results yet.']
    return '\n'.join(lines) + '\n'


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    repository = os.environ['GITHUB_REPOSITORY']
    if repository.lower() != 'berretw/vps_fit_benchmark':
        raise ValueError('This collector is configured for BerretW/vps_fit_benchmark only')
    if event.get('action') != 'labeled' or event['label']['name'] != 'benchmark-approved':
        raise ValueError('Expected a benchmark-approved label event')
    if event['sender']['login'].lower() != repository.split('/')[0].lower():
        raise ValueError('Only the repository owner can approve benchmark results')
    issue_number = event['issue']['number']
    if type(issue_number) is not int or issue_number < 1:
        raise ValueError('Invalid issue number')
    request = Request(
        'https://api.github.com/repos/%s/issues/%d' % (repository, issue_number),
        headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'User-Agent': 'vps-fit-collector'})
    with urlopen(request, timeout=30) as response:
        issue = json.load(response)
    if 'pull_request' in issue or 'benchmark-approved' not in {label['name'] for label in issue['labels']}:
        raise ValueError('Issue is not an approved benchmark submission')
    if issue['body'] != event['issue']['body']:
        raise ValueError('Issue was edited after approval; remove and reapply the label')
    report = parse_submission(issue['body'])
    destination = ROOT / 'results'
    destination.mkdir(exist_ok=True)
    record = {'issue_number': issue_number, 'report': report}
    (destination / ('issue-%d.json' % issue_number)).write_text(
        json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    records = [json.loads(path.read_text(encoding='utf-8'))
               for path in sorted(destination.glob('issue-*.json'))]
    (ROOT / 'RESULTS.md').write_text(render_leaderboard(records), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print('Result collection failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
