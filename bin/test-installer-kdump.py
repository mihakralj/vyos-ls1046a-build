import argparse
import ast
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = 'src/op_mode/image_installer.py'
PATCH = ROOT / 'data/vyos-1x-060-installer-kdump-auto.patch'
AUTO = '1G-8G:512M,8G-64G:768M,64G-:1G'


def git(repo, *args):
    return subprocess.run(
        ['git', '-C', str(repo), *args], check=True, capture_output=True
    ).stdout


def load_function(source):
    tree = ast.parse(source, filename=INSTALLER)
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == 'get_cli_kernel_options'
    ]
    if len(functions) != 1:
        raise ValueError('expected exactly one get_cli_kernel_options function')

    class ConfigTree:
        def __init__(self, contents):
            self.contents = contents

        def to_json(self):
            return self.contents

    def dict_search(path, data):
        for key in path.split('.'):
            if not isinstance(data, dict) or key not in data:
                return None
            data = data[key]
        return data

    namespace = {
        'ConfigTree': ConfigTree,
        'read_file': lambda contents: contents,
        'loads': json.loads,
        'dict_search': dict_search,
        'KDUMP_DEFAULT_MEMORY_AUTO': AUTO,
    }
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(module, INSTALLER, 'exec'), namespace)
    return namespace['get_cli_kernel_options']


class KernelOptionsTests(unittest.TestCase):
    function = staticmethod(lambda _: [])

    def options(self, config):
        return type(self).function(json.dumps(config))

    def test_missing_system_option(self):
        self.assertEqual(self.options({}), [])

    def test_kdump_cases(self):
        cases = [
            ('absent', {}, []),
            ('empty', {'kdump': {}}, [f'crashkernel={AUTO}']),
            ('null', {'kdump': None}, [f'crashkernel={AUTO}']),
            ('dump-path-only', {'kdump': {'dump-path': '/var/crash'}},
             [f'crashkernel={AUTO}']),
            ('explicit-auto', {'kdump': {'memory': 'auto'}},
             [f'crashkernel={AUTO}']),
            ('explicit-numeric', {'kdump': {'memory': '512'}},
             ['crashkernel=512M']),
        ]
        for name, options, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(self.options({'system': {'option': options}}), expected)

    def test_other_options_preserved(self):
        options = {
            'kernel': {
                'disable-mitigations': None,
                'quiet': None,
                'cpu': {'isolate-cpus': '2-3'},
                'memory': {'hugepage-size': {'2M': {'hugepage-count': '64'}}},
            },
            'reboot-on-panic': {},
            'kdump': {'dump-path': '/var/crash'},
        }
        self.assertEqual(self.options({'system': {'option': options}}), [
            'mitigations=off', 'quiet', 'panic=60', 'isolcpus=2-3',
            'hugepagesz=2M', 'hugepages=64', f'crashkernel={AUTO}',
        ])


def run_suite(source):
    KernelOptionsTests.function = staticmethod(load_function(source))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(KernelOptionsTests)
    return unittest.TextTestRunner(verbosity=2).run(suite)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('upstream_repo', type=Path)
    args = parser.parse_args()

    pin_file = (ROOT / 'bin/ci-setup-vyos1x.sh').read_text()
    pins = re.findall(r'^VYOS1X_COMMIT=([0-9a-f]{40})$', pin_file, re.MULTILINE)
    if len(pins) != 1:
        raise SystemExit('could not identify the pinned vyos-1x revision')
    revision = git(args.upstream_repo, 'rev-parse', 'HEAD').decode().strip()
    if revision != pins[0]:
        raise SystemExit(f'upstream HEAD {revision} does not match pin {pins[0]}')

    defaults = git(args.upstream_repo, 'show', f'{revision}:python/vyos/defaults.py')
    assignments = [
        node.value for node in ast.parse(defaults).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name)
                and target.id == 'KDUMP_DEFAULT_MEMORY_AUTO'
                for target in node.targets)
    ]
    if len(assignments) != 1 or ast.literal_eval(assignments[0]) != AUTO:
        raise SystemExit('pinned kdump auto default does not match test expectation')
    source = git(args.upstream_repo, 'show', f'{revision}:{INSTALLER}')
    with tempfile.TemporaryDirectory(prefix='installer-kdump-test-') as directory:
        repo = Path(directory)
        target = repo / INSTALLER
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        git(repo, 'init', '-q')
        git(repo, 'add', INSTALLER)
        print(f'Pinned upstream: {revision}', flush=True)
        before = load_function(source.decode())
        config = json.dumps({'system': {'option': {'kdump': {}}}})
        if before(config) != []:
            raise SystemExit('unpatched source no longer reproduces missing crashkernel')
        print('Before patch: implicit kdump auto is missing (reproduced)', flush=True)
        git(repo, 'apply', '--3way', '--whitespace=nowarn', '--check', str(PATCH))
        git(repo, 'apply', '--3way', '--whitespace=nowarn', str(PATCH))
        print('After patch:', flush=True)
        after = run_suite(target.read_text())
        if not after.wasSuccessful():
            raise SystemExit('patched source failed the regression suite')
    print('installer kdump regression: OK')


if __name__ == '__main__':
    main()
