"""The download gate must reject hidden failures but allow mirror fallback."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
DRIVER = PORT / "ci/download_sources.py"
spec = importlib.util.spec_from_file_location("download_sources", DRIVER)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class TerminalLogTests(unittest.TestCase):
    def test_make_error_is_terminal_even_if_outer_make_ignored_status(self):
        self.assertTrue(driver.terminal_failure("make[3]: *** [Makefile:95: dl/a.tar.xz] Error 1"))
        self.assertTrue(driver.terminal_failure("\033[31m ERROR: package/x failed to build.\033[0m"))

    def test_fallback_messages_alone_are_not_a_failed_download(self):
        for line in ("curl: (22) The requested URL returned error: 404",
                     "No more mirrors to try - giving up.",
                     "Hash mismatch, downloading again",
                     "fatal: unable to access the first mirror",
                     "Trying next mirror; downloaded and verified"):
            self.assertFalse(driver.terminal_failure(line), line)


@unittest.skipIf(os.name == "nt", "The OpenWrt download job runs on Linux")
class DownloadIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "include").mkdir()
        (self.root / "include/toplevel.mk").write_text("# prepared tree\n")
        (self.root / ".config").write_text("CONFIG_CCACHE=y\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        make = bin_dir / "make"
        make.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys
from pathlib import Path
with Path('calls.jsonl').open('a') as f:
    f.write(json.dumps(sys.argv[1:])+'\n')
target = next(x for x in sys.argv[1:] if '/' in x)
if target == os.environ.get('FAIL_TARGET'):
    print('download stopped without an error keyword')
    sys.exit(2)
if target == os.environ.get('HIDDEN_FAIL_TARGET'):
    print('make[3]: *** [Makefile:95: dl/missing.tar.xz] Error 1')
if target == os.environ.get('MIRROR_TARGET'):
    print('curl: (22) The requested URL returned error: 404')
    print('No more mirrors to try - giving up.')
    print('Hash mismatch, downloading again')
    print('Trying upstream git; downloaded and verified')
''')
        make.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])

    def run_gate(self, **env):
        self.env.update(env)
        result = subprocess.run([sys.executable, str(DRIVER), str(self.root)],
                                env=self.env, capture_output=True, text=True)
        calls = [json.loads(x) for x in (self.root / "calls.jsonl").read_text().splitlines()]
        report = json.loads((self.root / "download-check.json").read_text())
        return result, calls, report

    def test_toolchain_failure_stops_before_packages_and_compile(self):
        result, calls, report = self.run_gate(FAIL_TARGET='toolchain/download')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stages'][-1]['returncode'], 2)
        self.assertIn('IGNORE_ERRORS=', calls[-1])

    def test_hidden_make_failure_is_not_a_success(self):
        result, calls, report = self.run_gate(HIDDEN_FAIL_TARGET='package/download')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 4)
        self.assertEqual(report['stages'][-1]['returncode'], 0)
        self.assertTrue(report['stages'][-1]['terminal_errors'])
        self.assertFalse(report['ok'])

    def test_successful_mirror_fallback_finishes_all_stages(self):
        result, calls, report = self.run_gate(MIRROR_TARGET='package/download')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 5)
        self.assertTrue(report['ok'])
        self.assertTrue(all(x['ok'] for x in report['stages']))
        self.assertIn('curl: (22)', (self.root / 'download.log').read_text())

    def test_previous_success_report_is_replaced_on_failure(self):
        (self.root / 'download-check.json').write_text('{"ok":true}')
        result, calls, report = self.run_gate(FAIL_TARGET='tools/flock/compile')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(report['ok'])


class CacheConfigurationTests(unittest.TestCase):
    def check(self, config):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.config'
            path.write_text(config)
            return subprocess.run([sys.executable, str(PORT / 'check_config.py'), str(path)],
                                  capture_output=True, text=True)

    def complete_config(self):
        # Read the required package list without invoking the check module.
        import ast
        tree = ast.parse((PORT / 'check_config.py').read_text())
        required = next(ast.literal_eval(node.value) for node in tree.body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'required' for t in node.targets))
        return ''.join('CONFIG_' + name + '=y\n' for name in required)

    def test_wrong_cache_path_is_rejected(self):
        result = self.check(self.complete_config() + 'CONFIG_CCACHE_DIR="/tmp/other-cache"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CCACHE_DIR', result.stderr)

    def test_disabled_cache_is_rejected(self):
        config = self.complete_config().replace('CONFIG_CCACHE=y\n', '# CONFIG_CCACHE is not set\n')
        result = self.check(config)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CCACHE', result.stderr)

    def test_default_openwrt_cache_path_is_accepted(self):
        result = self.check(self.complete_config() + 'CONFIG_CCACHE_DIR=""\n')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
