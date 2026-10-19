#!/usr/bin/env python3
"""Validated inputs and local helpers for the auxiliary MSM8916 workflow."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re

PROFILES = (
    'ufi003', 'ufi001c', 'ufi001b', 'ufi103s', 'jz02v10', 'qrzl903', 'w001',
    'uz801', 'mf32', 'mf601', 'wf2', 'sp970v11', 'sp970v10',
)
UPSTREAM = 'lkiuyu/immortalwrt'


def normalize_packages(raw):
    if not isinstance(raw, str):
        raise ValueError('extra_packages must be a string')
    tokens = [token for token in re.split(r'[,\s]+', raw) if token]
    special = set()
    for token in tokens:
        if token.startswith('__ip__'):
            ipaddress.IPv4Address(token[6:])
            key = 'ip'
        elif token.startswith('__hostname__'):
            host = token[12:]
            labels = host.split('.')
            if len(host) > 253 or not all(re.fullmatch(
                    r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                    for label in labels):
                raise ValueError('Invalid hostname')
            key = 'hostname'
        else:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]*', token):
                raise ValueError('Invalid package name: ' + token)
            continue
        if key in special:
            raise ValueError('Duplicate setting: ' + key)
        special.add(key)
    return ' '.join(tokens)


def normalize_event(event_name, event, root):
    if event_name == 'workflow_dispatch':
        values = event.get('inputs', {})
    elif event_name == 'repository_dispatch':
        values = event.get('client_payload', {})
    else:
        raise ValueError('Unsupported build event: ' + event_name)
    if not isinstance(values, dict):
        raise ValueError('Build inputs must be an object')
    profile = values.get('profile', 'ufi003')
    upstream = values.get('upstream', UPSTREAM)
    custom_hash = values.get('custom_hash', '')
    ssh = values.get('ssh', False)
    if profile not in PROFILES or upstream != UPSTREAM:
        raise ValueError('Unsupported profile or upstream')
    if not (Path(root) / 'config' / (profile + '.config')).is_file():
        raise ValueError('Missing device config: ' + profile)
    if not isinstance(custom_hash, str) or (custom_hash and not re.fullmatch(
            r'[0-9a-fA-F]{7,40}', custom_hash)):
        raise ValueError('custom_hash must be a hexadecimal Git commit')
    if type(ssh) is bool:
        ssh_enabled = ssh
    elif isinstance(ssh, str) and ssh in ('true', 'false'):
        ssh_enabled = ssh == 'true'
    else:
        raise ValueError('ssh must be true or false')
    # Preserve the legacy repository_dispatch action shortcut.
    if event_name == 'repository_dispatch' and 'ssh' in str(event.get('action', '')):
        ssh_enabled = True
    return {
        'PROFILE': profile,
        'REPO_URL': 'https://github.com/' + upstream,
        'CONFIG_FILE': 'config/' + profile + '.config',
        'CUSTOM_HASH': custom_hash,
        'EXTRA_PACKAGES': normalize_packages(values.get('extra_packages', '')),
        'SSH_DEBUG': 'true' if ssh_enabled else 'false',
    }


def select_old_runs(document, cutoff, keep, workflow_id):
    """Keep the newest N runs of this workflow; select only completed old runs."""
    if keep < 0 or cutoff < 0:
        raise ValueError('Retention values must be nonnegative')
    runs = []
    for run in document['workflow_runs']:
        if run['workflow_id'] != workflow_id:
            continue
        if type(run['id']) is not int or run['id'] <= 0:
            raise ValueError('Invalid workflow run ID')
        created = datetime.strptime(run['created_at'], '%Y-%m-%dT%H:%M:%SZ')
        epoch = created.replace(tzinfo=timezone.utc).timestamp()
        runs.append((epoch, run))
    runs.sort(key=lambda item: (item[0], item[1]['id']), reverse=True)
    return [run['id'] for epoch, run in runs[keep:]
            if epoch < cutoff and run['status'] == 'completed']


def normalize_bat(folder):
    for path in sorted(Path(folder).glob('*.bat')):
        data = path.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'')
        path.write_bytes(data.replace(b'\n', b'\r\n'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('event-env')
    old = sub.add_parser('select-old-runs')
    old.add_argument('document', type=Path)
    old.add_argument('--cutoff', type=int, required=True)
    old.add_argument('--keep', type=int, required=True)
    old.add_argument('--workflow-id', type=int, required=True)
    crlf = sub.add_parser('normalize-bat')
    crlf.add_argument('folder', type=Path)
    args = parser.parse_args()
    if args.command == 'event-env':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        values = normalize_event(os.environ['GITHUB_EVENT_NAME'], event,
                                 Path(__file__).resolve().parents[1])
        # Every value is validated and single-line before touching GITHUB_ENV.
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as output:
            output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
    elif args.command == 'select-old-runs':
        ids = select_old_runs(json.loads(args.document.read_text()), args.cutoff,
                              args.keep, args.workflow_id)
        for run_id in ids:
            print(run_id)
    else:
        normalize_bat(args.folder)


if __name__ == '__main__':
    main()
