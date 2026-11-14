import argparse
import ast
import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
PIN = '908f67c4f5ed46a55aeee9744a913efecd06e358'
INSTALLER = 'src/op_mode/image_installer.py'
GRUB = 'python/vyos/system/grub.py'
PATCHES = (
    'data/vyos-1x-006-install-image-reserve-gap.patch',
    'data/vyos-1x-007-prefer-emmc-default.patch',
    'data/vyos-1x-011-vyos-env-boot.patch',
    'data/vyos-1x-028-add-image-uboot-env.patch',
    'data/vyos-1x-048-restore-hide-usb-live-media.patch',
    'data/vyos-1x-060-installer-kdump-auto.patch',
)
OPTIONS = 'quiet crashkernel=512M'


def git(repo, *args, input_data=None):
    return subprocess.run(
        ['git', '-C', str(repo), *args], input=input_data, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def patched_source(upstream, patch_ref):
    configured_pin = re.findall(
        r'^VYOS1X_COMMIT=([0-9a-f]{40})$',
        (ROOT / 'bin/ci-setup-vyos1x.sh').read_text(), re.MULTILINE,
    )
    if configured_pin != [PIN]:
        raise ValueError(f'expected configured upstream pin {PIN}, found {configured_pin}')
    resolved = git(upstream, 'rev-parse', f'{PIN}^{{commit}}').decode().strip()
    if resolved != PIN:
        raise ValueError(f'upstream revision mismatch: {resolved}')
    with tempfile.TemporaryDirectory(prefix='installer-uboot-') as directory:
        clone = Path(directory) / 'vyos-1x'
        subprocess.run(
            ['git', 'clone', '--quiet', '--no-checkout', '--no-local',
             str(upstream.resolve()), str(clone)], check=True,
        )
        git(clone, 'checkout', '--quiet', '--detach', PIN)
        if git(clone, 'status', '--porcelain').strip():
            raise ValueError('disposable checkout is not clean')
        for patch in PATCHES:
            if patch.endswith('028-add-image-uboot-env.patch'):
                data = (git(ROOT, 'show', f'{patch_ref}:{patch}') if patch_ref
                        else (ROOT / patch).read_bytes())
            else:
                data = git(ROOT, 'show', f'HEAD:{patch}')
            try:
                git(clone, 'apply', '--3way', '--whitespace=nowarn', '-',
                    input_data=data)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(
                    f'failed to apply {patch}:\n'
                    f'{error.stderr.decode(errors="replace")}'
                ) from error
        return ((clone / INSTALLER).read_text(), (clone / GRUB).read_text(),
                (clone / 'python/vyos/defaults.py').read_text())


def load_add_image(source, namespace):
    tree = ast.parse(source, filename=INSTALLER)
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'add_image']
    if len(functions) != 1:
        raise ValueError('expected exactly one add_image function')
    functions[0].decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(module, INSTALLER, 'exec'), namespace)
    return namespace['add_image']


class Scenario:
    def __init__(self, *, default=True, postinstall=True, mtd=True,
                 config=True, failure=None):
        self.events = []
        self.entries = {}
        self.selected = 'old'
        self.env_options = 'old-options'
        self.default = default
        self.postinstall = postinstall
        self.mtd = mtd
        self.config = config
        self.failure = failure
        self.output = ''

    def execute(self, source, no_prompt=False):
        scenario = self

        class FakePath:
            def __init__(self, path):
                self.path = PurePosixPath(str(path))

            def __str__(self):
                return str(self.path)

            def joinpath(self, *parts):
                return FakePath(self.path.joinpath(*parts))

            def exists(self):
                path = str(self.path)
                if path == '/usr/local/bin/vyos-postinstall':
                    return scenario.postinstall
                if path == '/dev/mtd2':
                    return scenario.mtd
                if path == '/dev/mapper/vyos_config':
                    return False
                if path.endswith('/config.boot'):
                    return scenario.config
                if path == '/iso/sha256sum.txt':
                    return True
                raise AssertionError(f'unexpected exists: {path}')

            def mkdir(self, *args, **kwargs):
                pass

            def touch(self):
                pass

            def iterdir(self):
                if str(self.path) in ('/iso', '/iso/live'):
                    return iter(())
                raise AssertionError(f'unexpected iterdir: {self.path}')

        def version_add(name, root):
            self.events.append('version_add')
            self.entries[name] = 'old-options'
            assert root == '/target'

        def update(options, *, root_dir, version):
            self.events.append('update_kernel_cmdline_options')
            if self.failure == 'update':
                raise RuntimeError('kernel option update failed')
            assert root_dir == '/target' and version == 'new'
            self.entries[version] = options

        def set_default(name, root):
            self.events.append('set_default')
            assert root == '/target'
            self.selected = name
            self.env_options = self.entries[name]

        def run(command):
            if command == 'cd /iso && sha256sum --status -c sha256sum.txt':
                return 0
            if command == '/usr/local/bin/vyos-postinstall --root /target new':
                self.events.append('postinstall')
                if self.failure == 'exception':
                    raise RuntimeError('postinstall failed')
                return 1 if self.failure == 'exit' else 0
            raise AssertionError(f'unexpected command: {command}')

        noop = lambda *args, **kwargs: None
        namespace = {
            'Path': FakePath,
            'image': SimpleNamespace(
                is_live_boot=lambda: False,
                get_image_tools_version=lambda _: '1',
                get_image_version=lambda _: 'new',
                get_running_image=lambda: 'old',
                validate_name=lambda _: True,
            ),
            'disk': SimpleNamespace(partition_mount=noop, partition_umount=noop,
                                    find_persistence=lambda: '/target'),
            'grub': SimpleNamespace(version_list=lambda: ['old'],
                                    version_add=version_add, set_default=set_default),
            'grub_util': SimpleNamespace(update_kernel_cmdline_options=update),
            'disk_usage': lambda _: SimpleNamespace(free=4 * 1024**3),
            'unsaved_commits': lambda: False,
            'environ': {},
            'image_fetch': lambda *args: FakePath('/image.iso'),
            'DIR_ISO_MOUNT': '/iso',
            'DIR_ROOTFS_SRC': '/rootfs',
            'DIR_CONFIG': '/config',
            'SYSTEM_CFG_VER': '1',
            'validate_compatibility': noop,
            'validate_secure_boot': noop,
            'cleanup': noop,
            'run': run,
            'ask_input': lambda *args: 'new',
            'ask_yes_no': lambda *args, **kwargs: self.default,
            'MSG_INPUT_IMAGE_NAME': 'name',
            'MSG_INPUT_IMAGE_DEFAULT': 'default',
            'migrate_config': lambda: self.config,
            'copy_ssh_host_keys': lambda: False,
            'copy_ssh_known_hosts': lambda: False,
            'copy_bash_history': lambda: False,
            'chown': noop,
            'chmod_2775': noop,
            'copytree': noop,
            'copy_preserve_owner': noop,
            'write_file': noop,
            'dumps': lambda value: str(value),
            'copy': noop,
            'glob': lambda _: [],
            'migrate_known_hosts': noop,
            'get_cli_kernel_options': lambda _: OPTIONS.split(),
            'ENOSPC': 28,
        }
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                load_add_image(source, namespace)('/image.iso', no_prompt=no_prompt)
        finally:
            self.output = output.getvalue()


class InstallerOrderingTests(unittest.TestCase):
    source = ''

    def scenario(self, **kwargs):
        scenario = Scenario(**kwargs)
        scenario.execute(self.source)
        return scenario

    def test_default_with_postinstall(self):
        scenario = self.scenario()
        self.assertEqual(scenario.events, ['version_add', 'update_kernel_cmdline_options',
                                           'set_default', 'postinstall'])
        self.assertEqual((scenario.selected, scenario.env_options), ('new', OPTIONS))

    def test_default_without_postinstall_or_mtd(self):
        for missing in ('postinstall', 'mtd'):
            with self.subTest(missing=missing):
                scenario = self.scenario(**{missing: False})
                self.assertEqual(scenario.events, ['version_add',
                    'update_kernel_cmdline_options', 'set_default'])
                self.assertEqual((scenario.selected, scenario.env_options), ('new', OPTIONS))

    def test_kernel_options_reach_default_without_postinstall_or_mtd(self):
        for missing in ('postinstall', 'mtd'):
            with self.subTest(missing=missing):
                scenario = self.scenario(**{missing: False})
                self.assertEqual((scenario.selected, scenario.env_options), ('new', OPTIONS))

    def test_nondefault_does_not_select_or_postinstall(self):
        scenario = self.scenario(default=False)
        self.assertEqual(scenario.events, ['version_add', 'update_kernel_cmdline_options'])
        self.assertEqual((scenario.selected, scenario.env_options), ('old', 'old-options'))
        self.assertEqual(scenario.entries['new'], OPTIONS)

    def test_postinstall_failure_is_nonfatal(self):
        for failure in ('exit', 'exception'):
            with self.subTest(failure=failure):
                scenario = self.scenario(failure=failure)
                self.assertEqual(scenario.events, ['version_add',
                    'update_kernel_cmdline_options', 'set_default', 'postinstall'])
                self.assertEqual((scenario.selected, scenario.env_options), ('new', OPTIONS))
                self.assertIn('[ERROR] QSPI', scenario.output)
                self.assertIn('verify U-Boot configuration before reboot', scenario.output)

    def test_no_prompt_selects_new_default(self):
        scenario = Scenario(default=False)
        scenario.execute(self.source, no_prompt=True)
        self.assertEqual(scenario.events, ['version_add', 'update_kernel_cmdline_options',
                                           'set_default', 'postinstall'])
        self.assertEqual((scenario.selected, scenario.env_options), ('new', OPTIONS))

    def test_kernel_option_failure_does_not_change_default(self):
        scenario = Scenario(failure='update')
        with self.assertRaises(SystemExit):
            scenario.execute(self.source)
        self.assertEqual(scenario.events, ['version_add', 'update_kernel_cmdline_options'])
        self.assertEqual((scenario.selected, scenario.env_options), ('old', 'old-options'))

    def test_no_config(self):
        scenario = self.scenario(config=False)
        self.assertEqual(scenario.events, ['version_add', 'set_default', 'postinstall'])
        self.assertEqual((scenario.selected, scenario.env_options), ('new', 'old-options'))


class GrubEnvironmentTests(unittest.TestCase):
    source = ''
    installer_source = ''
    defaults_source = ''

    def test_real_grub_options_reach_uboot_helpers(self):
        config = {'system': {'option': {'kernel': {'quiet': None}, 'kdump': {}}}}

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

        assignments = [
            node.value for node in ast.parse(self.defaults_source).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == 'KDUMP_DEFAULT_MEMORY_AUTO'
                    for target in node.targets)
        ]
        self.assertEqual(len(assignments), 1)
        auto = ast.literal_eval(assignments[0])
        installer = ast.parse(self.installer_source, filename=INSTALLER)
        functions = [node for node in installer.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == 'get_cli_kernel_options']
        self.assertEqual(len(functions), 1)
        config_namespace = {
            'ConfigTree': ConfigTree,
            'read_file': lambda _: json.dumps(config),
            'loads': json.loads,
            'dict_search': dict_search,
            'KDUMP_DEFAULT_MEMORY_AUTO': auto,
        }
        config_module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
        exec(compile(config_module, INSTALLER, 'exec'), config_namespace)
        options = ' '.join(config_namespace['get_cli_kernel_options']('config.boot'))
        self.assertEqual(options, f'quiet crashkernel={auto}')

        tree = ast.parse(self.source, filename=GRUB)
        constants = {
            'GRUB_DIR_MAIN', 'GRUB_DIR_VYOS', 'CFG_VYOS_VARS',
            'GRUB_DIR_VYOS_VERS', 'BOOT_OPTS_STEM', 'REGEX_GRUB_BOOT_OPTS',
        }
        nodes = [
            node for node in tree.body
            if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id in constants)
            or (isinstance(node, ast.FunctionDef)
                and node.name in {'get_boot_opts', 'set_default'})
        ]
        saved_vars = {}
        namespace = {
            'Path': Path,
            're_compile': re.compile,
            'vars_read': lambda _: {},
            'vars_write': lambda _, values: saved_vars.update(values),
            'gen_version_uuid': lambda name: f'uuid-{name}',
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, GRUB, 'exec'), namespace)
        with tempfile.TemporaryDirectory(prefix='installer-grub-') as directory:
            root = Path(directory)
            version_dir = root / namespace['GRUB_DIR_VYOS_VERS'].lstrip('/')
            version_dir.mkdir(parents=True)
            boot_options = f'{namespace["BOOT_OPTS_STEM"]}new {options}'
            (version_dir / 'new.cfg').write_text(f'    set boot_opts="{boot_options}"\n')
            with patch('os.sync'), patch('fcntl.ioctl'):
                namespace['set_default']('new', str(root))
            self.assertEqual(saved_vars['default'], 'uuid-new')
            self.assertEqual((root / 'boot/vyos.env').read_text(),
                             f'vyos_image=new\nvyos_extra_args={options}\n')
            result = subprocess.run(
                ['bash', '-c', '. "$1"; grub_extra_args "$2" "$3"', 'bash',
                 str(ROOT / 'board/scripts/vyos-postinstall'), 'new', str(root)],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(result.stdout.strip(), options)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('upstream_repo', type=Path)
    parser.add_argument('--patch-ref')
    args = parser.parse_args()
    installer_source, grub_source, defaults_source = patched_source(
        args.upstream_repo, args.patch_ref
    )
    InstallerOrderingTests.source = installer_source
    GrubEnvironmentTests.source = grub_source
    GrubEnvironmentTests.installer_source = installer_source
    GrubEnvironmentTests.defaults_source = defaults_source
    print(f'Pinned upstream: {PIN}; 028: {args.patch_ref or "worktree"}', flush=True)
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(InstallerOrderingTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(GrubEnvironmentTests),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
