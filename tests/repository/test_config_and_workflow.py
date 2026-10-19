"""Exercise config generation and the real auxiliary workflow without network writes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = load_module('device_config', '工具与脚本/gen_config.py')
HELPER = load_module('workflow_helper', 'scripts/imm410_workflow.py')
WORKFLOW = yaml.load((ROOT / '.github/workflows/Build_高通410 imm.yml').read_text(),
                     Loader=yaml.BaseLoader)
STEPS = {step.get('name'): step for step in WORKFLOW['jobs']['build']['steps']}


class ConfigGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / 'config/ufi003.config').read_text()

    def test_real_template_converts_to_every_supported_device_and_roundtrips(self):
        for device in GEN.DEVICES:
            with self.subTest(device=device):
                result = GEN.convert_config(self.template, 'ufi003', device)
                enabled = [line for line in result.splitlines()
                           if line.startswith('CONFIG_TARGET_') and '_DEVICE_' in line
                           and line.endswith('=y')]
                self.assertEqual(enabled, [GEN.TARGET + device + '=y'])
                self.assertIn('CONFIG_TARGET_PROFILE="DEVICE_openstick-' + device + '"', result)
                self.assertEqual(GEN.convert_config(result, device, 'ufi003'), self.template)

    def test_invalid_templates_leave_existing_destination_and_source_untouched(self):
        target = GEN.TARGET
        mutations = [
            self.template.replace(target + 'ufi003=y', '# ' + target + 'ufi003 is not set'),
            self.template + target + 'mf601=y\n',
            self.template.replace('DEVICE_openstick-ufi003"', 'DEVICE_openstick-mf601"'),
            self.template.replace('# ' + target + 'mf32 is not set\n', ''),
            self.template + '# ' + target + 'mf32 is not set\n',
            self.template.replace('CONFIG_PACKAGE_qcom-msm8916-modem-openstick-ufi003-firmware=y',
                                  'CONFIG_PACKAGE_qcom-msm8916-modem-openstick-mf601-firmware=y'),
        ]
        for content in mutations:
            with self.subTest(case=mutations.index(content)), tempfile.TemporaryDirectory() as work:
                folder = Path(work) / 'config'; folder.mkdir()
                source, destination = folder / 'ufi003.config', folder / 'mf32.config'
                source.write_text(content)
                destination.write_bytes(b'EXISTING CONFIG\n')
                result = subprocess.run([sys.executable, str(ROOT / '工具与脚本/gen_config.py'),
                                         'ufi003', 'mf32'], cwd=work, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(destination.read_bytes(), b'EXISTING CONFIG\n')
                self.assertEqual(source.read_text(), content)
                self.assertEqual(sorted(p.name for p in folder.iterdir()),
                                 ['mf32.config', 'ufi003.config'])

    def test_cli_success_and_unsupported_names(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work) / 'config'; folder.mkdir()
            (folder / 'ufi003.config').write_text(self.template)
            command = [sys.executable, str(ROOT / '工具与脚本/gen_config.py')]
            good = subprocess.run(command + ['ufi003', 'mf32'], cwd=work, capture_output=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            converted = (folder / 'mf32.config').read_bytes()
            for src, dst in [('ufi00x', 'mf32'), ('ufi003', '../outside'), ('ufi003', 'typo')]:
                bad = subprocess.run(command + [src, dst], cwd=work, capture_output=True)
                self.assertNotEqual(bad.returncode, 0)
                self.assertEqual((folder / 'mf32.config').read_bytes(), converted)
            self.assertFalse((Path(work) / 'outside.config').exists())


class WorkflowInputTests(unittest.TestCase):
    def test_both_events_use_the_same_declared_defaults(self):
        defaults = {key: value['default'] for key, value in
                    WORKFLOW['on']['workflow_dispatch']['inputs'].items()}
        manual = HELPER.normalize_event('workflow_dispatch', {'inputs': defaults}, ROOT)
        api = HELPER.normalize_event('repository_dispatch', {}, ROOT)
        self.assertEqual(api, manual)
        self.assertEqual(api['REPO_URL'], 'https://github.com/lkiuyu/immortalwrt')
        self.assertEqual(api['CONFIG_FILE'], 'config/ufi003.config')
        self.assertEqual(api['SSH_DEBUG'], 'false')

    def test_explicit_api_payload_and_boolean_false(self):
        event = {'client_payload': {'profile': 'ufi003', 'ssh': 'false',
                 'custom_hash': 'a' * 40,
                 'extra_packages': 'luci-app-example, __ip__192.168.8.1\n__hostname__ufi-modem'}}
        result = HELPER.normalize_event('repository_dispatch', event, ROOT)
        self.assertEqual(result['EXTRA_PACKAGES'],
                         'luci-app-example __ip__192.168.8.1 __hostname__ufi-modem')
        self.assertEqual(result['CUSTOM_HASH'], 'a' * 40)
        self.assertEqual(result['SSH_DEBUG'], 'false')
        event['client_payload']['ssh'] = True
        self.assertEqual(HELPER.normalize_event('repository_dispatch', event, ROOT)['SSH_DEBUG'], 'true')

    def test_invalid_input_does_not_write_github_env_or_execute_shell(self):
        invalid = [
            {'profile': '../other'}, {'upstream': 'other/repository'},
            {'custom_hash': '$(touch EXECUTED)'}, {'ssh': 'false; touch EXECUTED'},
            {'extra_packages': "luci-app-test'; touch EXECUTED; #"},
            {'extra_packages': 'valid\nINJECTED=1'},
            {'extra_packages': '__ip__999.1.1.1'},
            {'extra_packages': '__hostname__bad/host'},
            {'extra_packages': '__ip__192.168.1.1 __ip__192.168.2.1'},
        ]
        for values in invalid:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as work:
                root = Path(work)
                event = root / 'event.json'; event.write_text(json.dumps({'client_payload': values}))
                output = root / 'env'; output.write_text('EXISTING=1\n')
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/imm410_workflow.py'),
                                         'event-env'], cwd=work, capture_output=True, env={
                    **os.environ, 'GITHUB_EVENT_NAME': 'repository_dispatch',
                    'GITHUB_EVENT_PATH': str(event), 'GITHUB_ENV': str(output)}, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_text(), 'EXISTING=1\n')
                self.assertFalse((root / 'EXECUTED').exists())

    def test_no_input_expression_is_inserted_into_shell_source(self):
        for step in STEPS.values():
            code = step.get('run', '')
            self.assertNotRegex(code, r'\$\{\{[^}]*inputs\.')
        self.assertEqual(STEPS['Validate manual or API build inputs']['run'],
                         'python3 scripts/imm410_workflow.py event-env')


class WorkflowFileTests(unittest.TestCase):
    def test_real_staging_steps_merge_files_once_preserving_upstream_and_source(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'files/etc').mkdir(parents=True)
            (root / 'files/etc/custom').write_text('CUSTOM\n')
            (root / 'files/.hidden').write_text('HIDDEN\n')
            (root / 'files/etc/link').symlink_to('custom')
            (root / 'openwrt/files/etc').mkdir(parents=True)
            (root / 'openwrt/files/etc/upstream').write_text('UPSTREAM\n')
            (root / 'device.config').write_text('CONFIG_FIXTURE=y\n')
            diy = root / 'diy.sh'; diy.write_text('#!/bin/sh\nexit 0\n')
            environment = {**os.environ, 'CONFIG_FILE': 'device.config',
                           'DIY_P2_SH': 'diy.sh', 'GITHUB_WORKSPACE': str(root)}
            for name in ('Merge custom rootfs files once', 'Load custom configuration'):
                result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', STEPS[name]['run']],
                                         cwd=work, env=environment, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'openwrt/files/etc/custom').read_text(), 'CUSTOM\n')
            self.assertEqual((root / 'openwrt/files/etc/upstream').read_text(), 'UPSTREAM\n')
            self.assertTrue((root / 'openwrt/files/.hidden').is_file())
            self.assertTrue((root / 'openwrt/files/etc/link').is_symlink())
            self.assertFalse((root / 'openwrt/files/files').exists())
            self.assertTrue((root / 'files/etc/custom').is_file())
            self.assertEqual((root / 'openwrt/.config').read_text(), 'CONFIG_FIXTURE=y\n')

    def test_batch_normalization_is_idempotent_for_lf_crlf_and_old_double_cr(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / 'установка test.bat'
            path.write_bytes(b'@echo off\nline 1\r\nline 2\r\r\n')
            HELPER.normalize_bat(work)
            self.assertEqual(path.read_bytes(), b'@echo off\r\nline 1\r\nline 2\r\n')
            before = path.read_bytes()
            HELPER.normalize_bat(work)
            self.assertEqual(path.read_bytes(), before)


def run_fixture(run_id, day, status='completed', workflow_id=42):
    return {'id': run_id, 'workflow_id': workflow_id, 'status': status,
            'created_at': '2026-10-' + str(day).zfill(2) + 'T00:00:00Z'}


class WorkflowRetentionTests(unittest.TestCase):
    CUTOFF = 1791763200  # 2026-10-12T00:00:00Z, not an age in seconds.

    def test_keep_minimum_applies_before_age_filter(self):
        runs = [run_fixture(i, 11-i) for i in range(1, 10)]
        self.assertEqual(HELPER.select_old_runs({'workflow_runs': runs}, self.CUTOFF, 5, 42),
                         [6, 7, 8, 9])
        self.assertEqual(HELPER.select_old_runs({'workflow_runs': runs[:4]}, self.CUTOFF, 5, 42), [])

    def test_recent_active_and_other_workflow_runs_are_preserved(self):
        runs = [run_fixture(1, 18), run_fixture(2, 17), run_fixture(3, 16),
                run_fixture(4, 15), run_fixture(5, 14), run_fixture(6, 10),
                run_fixture(7, 9, 'in_progress'), run_fixture(8, 8),
                run_fixture(90, 1, workflow_id=99)]
        self.assertEqual(HELPER.select_old_runs({'workflow_runs': runs}, self.CUTOFF, 5, 42), [6, 8])

    def test_real_cleanup_shell_only_requests_eligible_deletions_using_mock_api(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'scripts').mkdir(); (root / 'bin').mkdir()
            shutil.copyfile(ROOT / 'scripts/imm410_workflow.py', root / 'scripts/imm410_workflow.py')
            (root / 'current.json').write_text(json.dumps({'workflow_id': 42}))
            runs = [run_fixture(i, 20-i) for i in range(1, 12)]
            runs += [run_fixture(90, 1, workflow_id=99), run_fixture(91, 1, 'queued')]
            (root / 'runs.json').write_text(json.dumps({'workflow_runs': runs}))
            curl = root / 'bin/curl'
            curl.write_text('''#!/bin/sh
for arg do last="$arg"; done
case "$*" in
  *"-X DELETE"*) printf '%s\\n' "$last" >> "$DELETIONS" ;;
  *"/actions/runs/12345"*) cp "$FIXTURE_ROOT/current.json" "$last" ;;
  *"/actions/workflows/42/runs?per_page=100"*) cp "$FIXTURE_ROOT/runs.json" "$last" ;;
  *) exit 99 ;;
esac
''')
            curl.chmod(0o755)
            date = root / 'bin/date'; date.write_text('#!/bin/sh\necho ' + str(self.CUTOFF) + '\n')
            date.chmod(0o755)
            deleted = root / 'deleted'
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c',
                                     STEPS['Delete old workflow runs via GitHub API']['run']],
                cwd=work, capture_output=True, timeout=10, env={**os.environ,
                    'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
                    'FIXTURE_ROOT': str(root), 'DELETIONS': str(deleted),
                    'RUNNER_TEMP': str(root), 'GITHUB_RUN_ID': '12345',
                    'GITHUB_REPOSITORY': 'fixture/repository', 'GITHUB_TOKEN': 'test-only',
                    'RETAIN_DAYS': '7', 'KEEP_MINIMUM_RUNS': '5'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(deleted.read_text().splitlines(), [
                'https://api.github.com/repos/fixture/repository/actions/runs/' + str(i)
                for i in (9, 10, 11)])


if __name__ == '__main__':
    unittest.main()
