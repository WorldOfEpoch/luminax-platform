"""Offline setup regression checks. No app imports, providers, services, or Docker starts."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1]
PAYLOAD = TOOLS.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load('initialize_local_config')
check = load('check_public_config')
atlas = load('workspace_atlas_builder')
HASH = '$6$salt$' + 'a' * 86


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luminax-config-test-')
        self.addCleanup(self.temp.cleanup)
        self.payload = Path(self.temp.name) / 'payload'
        self.payload.mkdir()
        shutil.copyfile(PAYLOAD / '.env.example', self.payload / '.env.example')

    def initialize_mocked(self, model='fixture-model:1'):
        with patch.object(setup, 'find_openssl', return_value='fixture-openssl'), patch.object(
                setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, HASH + '\n', '')) as run:
            created = setup.initialize(self.payload, model)
        return created, run

    def values(self):
        return dict(line.split('=', 1) for line in (self.payload / '.env').read_text().splitlines()
                    if line and not line.startswith('#'))

    def test_independent_generated_secrets_and_disabled_runtime(self):
        created, run = self.initialize_mocked()
        values = self.values()
        keys = ['CONTROL_TOKEN', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD',
                'UNDERFOOT_VOICE_JOIN_SECRET', 'UNDERFOOT_VOICE_TURN_SHARED_SECRET', 'LUMINAX_WEB_PASSWORD']
        self.assertEqual(len({values[key] for key in keys}), len(keys))
        for key in keys:
            self.assertRegex(values[key], r'^[0-9a-f]{64}$')
        self.assertEqual(values['LUMINAX_RUNTIME_ENABLED'], 'false')
        self.assertEqual(values['OLLAMA_CHAT_MODEL_DEFAULT'], 'fixture-model:1')
        self.assertEqual(values['UNDERFOOT_DB_PASSWORD'], values['MYSQL_PASSWORD'])
        self.assertEqual(len(created), 3)
        self.assertEqual(run.call_args.args[0], ['fixture-openssl', 'passwd', '-6', '-stdin'])
        self.assertNotIn(values['LUMINAX_WEB_PASSWORD'], ' '.join(run.call_args.args[0]))
        self.assertIn('static-auth-secret=' + values['UNDERFOOT_VOICE_TURN_SHARED_SECRET'],
                      (self.payload / 'runtime/turnserver.conf').read_text())

    def test_existing_config_is_preserved(self):
        self.initialize_mocked()
        before = (self.payload / '.env').read_bytes()
        with self.assertRaises(FileExistsError):
            setup.initialize(self.payload)
        self.assertEqual(before, (self.payload / '.env').read_bytes())

    def test_partial_existing_auth_is_preserved(self):
        runtime = self.payload / 'runtime'
        runtime.mkdir()
        (runtime / 'luminax.htpasswd').write_text('retained')
        with self.assertRaises(FileExistsError):
            setup.initialize(self.payload)
        self.assertFalse((self.payload / '.env').exists())

    def test_rejects_model_injection(self):
        for model in ['x\nCONTROL_TOKEN=anything', 'x; whoami', '$(whoami)', 'space model']:
            with self.subTest(model=model), self.assertRaises(ValueError):
                setup.initialize(self.payload, model)
        self.assertFalse((self.payload / '.env').exists())

    def test_bad_hash_leaves_no_credentials(self):
        with patch.object(setup, 'find_openssl', return_value='fixture'), patch.object(
                setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'plaintext', '')):
            with self.assertRaises(RuntimeError):
                setup.initialize(self.payload)
        self.assertFalse((self.payload / '.env').exists())

    def test_failed_create_removes_only_new_files(self):
        original = setup.os.open
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise PermissionError('synthetic write failure')
            return original(*args, **kwargs)
        with patch.object(setup.os, 'open', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                self.initialize_mocked()
        self.assertFalse((self.payload / '.env').exists())
        self.assertTrue((self.payload / '.env.example').is_file())

    def test_discovers_openssl_from_git_installation(self):
        git = Path(self.temp.name) / 'Git/cmd/git.exe'
        openssl = git.parents[1] / 'usr/bin/openssl.exe'
        openssl.parent.mkdir(parents=True)
        openssl.touch()
        with patch.object(setup.shutil, 'which', side_effect=lambda name: str(git) if name == 'git' else None):
            self.assertTrue(Path(setup.find_openssl()).samefile(openssl))

    def test_real_openssl_generates_hash_in_temporary_fixture(self):
        try:
            executable = setup.find_openssl()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        setup.initialize(self.payload, openssl=executable)
        self.assertRegex((self.payload / 'runtime/luminax.htpasswd').read_text(), r'^operator:\$6\$')

    def test_source_configuration_is_safe_and_parseable(self):
        summary = check.check_source(PAYLOAD)
        self.assertGreater(summary['json_files'], 50)

    def test_missing_docker_is_explicit_failure(self):
        with patch.object(check.shutil, 'which', return_value=None), self.assertRaisesRegex(RuntimeError, 'not run'):
            check.check_compose(PAYLOAD)

    def test_checker_never_loads_private_env_or_starts_services(self):
        private_env = self.payload / '.env'
        private_env.write_text('SECRET=must-not-be-read\n')
        self.write_compose_fixture()
        original_open = Path.open
        def reject_private_read(path, *args, **kwargs):
            if path.resolve() == private_env.resolve():
                self.fail('Checker attempted to open the private service .env.')
            return original_open(path, *args, **kwargs)
        with patch.object(check.shutil, 'which', return_value='fixture-docker'), patch.object(
                check.subprocess, 'run', side_effect=self.legacy_compose_config), patch.dict(
                    os.environ, {'CONTROL_TOKEN': 'private'}), patch.object(Path, 'open', reject_private_read):
            self.assertEqual(check.check_compose(self.payload)['containers_started'], 0)
        self.assertEqual(private_env.read_text(), 'SECRET=must-not-be-read\n')

    def write_compose_fixture(self):
        text = ('services:\n  fixture:\n    profiles: [core]\n    restart: "no"\n'
                '    env_file:\n    - .env\n')
        (self.payload / 'docker-compose.yml').write_text(text)
        return text

    def legacy_compose_config(self, command, **kwargs):
        """Emulate clients that inspect env_file despite --no-env-resolution."""
        self.assertEqual(command[-4:], ['config', '--format', 'json', '--no-env-resolution'])
        self.assertEqual(Path(command[command.index('--project-directory') + 1]), self.payload)
        sample = Path(command[command.index('--env-file') + 1])
        recipe = Path(command[command.index('-f') + 1])
        self.assertFalse(recipe.is_relative_to(self.payload))
        self.assertNotIn('CONTROL_TOKEN', kwargs['env'])
        lines = recipe.read_text().splitlines()
        self.assertEqual(sum(line.strip() == 'env_file:' for line in lines), 1)
        for index, line in enumerate(lines):
            if line.strip() != 'env_file:':
                continue
            scalar = lines[index + 1].strip().removeprefix('- ')
            reference = json.loads(scalar) if scalar.startswith('"') else scalar
            env_file = self.payload / reference
            if not env_file.is_file():
                return subprocess.CompletedProcess(command, 1, '', 'Service env file is missing.')
            self.assertTrue(env_file.samefile(sample))
            synthetic = env_file.read_text()
            self.assertNotIn('must-not-be-read', synthetic)
            self.assertIn('synthetic-check-value-', synthetic)
        model = {'services': {'fixture': {'profiles': ['core'], 'restart': 'no',
                                         'ports': [{'host_ip': '127.0.0.1'}]}}}
        return subprocess.CompletedProcess(command, 0, json.dumps(model), '')

    def test_legacy_compose_validates_with_missing_private_env(self):
        original_recipe = self.write_compose_fixture()
        self.assertFalse((self.payload / '.env').exists())
        with patch.object(check.shutil, 'which', return_value='fixture-docker'), patch.object(
                check.subprocess, 'run', side_effect=self.legacy_compose_config) as run:
            self.assertEqual(check.check_compose(self.payload)['services'], 1)
            self.assertEqual(run.call_count, 1)
        self.assertFalse((self.payload / '.env').exists())
        self.assertEqual((self.payload / 'docker-compose.yml').read_text(), original_recipe)

    def test_unsupported_env_file_layout_fails_before_compose(self):
        (self.payload / 'docker-compose.yml').write_text('services:\n  fixture:\n    env_file: .env\n')
        with patch.object(check.shutil, 'which', return_value='fixture-docker'), patch.object(
                check.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'isolated from private files'):
                check.check_compose(self.payload)
            run.assert_not_called()

    def test_rejects_external_port_socket_and_host_bind(self):
        for extra in [{'ports': [{'host_ip': '0.0.0.0'}]}, {'privileged': True},
                      {'volumes': [{'type': 'bind', 'source': '/var/run/docker.sock', 'target': '/var/run/docker.sock'}]},
                      {'volumes': [{'type': 'bind', 'source': str(self.payload.parent), 'target': '/host', 'read_only': True}]}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                check.validate_compose_model(self.payload, {'services': {'fixture': {
                    'profiles': ['core'], 'restart': 'no', **extra}}})


class AtlasTests(unittest.TestCase):
    def test_source_root_never_escapes_payload(self):
        root = PAYLOAD.resolve()
        self.assertEqual(atlas._resolve_project_root('/workspace/luminax', root, {}), root)
        with self.assertRaises(ValueError):
            atlas._resolve_project_root('../outside', root, {})

    def test_index_omits_private_and_generated_content(self):
        with tempfile.TemporaryDirectory(prefix='luminax-atlas-test-') as folder:
            root = Path(folder)
            for name in ['README.md', '.env', '.env.local', '.env.example', 'memory/chat/private.json',
                         '.codex/sessions/session.json', 'node_modules/private/package.json']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            entries, _ = atlas._collect_file_index(root, {}, {})
            self.assertEqual({row['path'] for row in entries}, {'README.md', '.env.example'})

    def test_default_cli_is_plan_without_writes(self):
        before = (PAYLOAD / 'reports').exists()
        result = subprocess.run([os.sys.executable, str(TOOLS / 'workspace_atlas_builder.py')],
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertIn('"status": "plan"', result.stdout)
        self.assertEqual(before, (PAYLOAD / 'reports').exists())


class SeedTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'nt':
            git = shutil.which('git')
            self.bash = next((str(root / 'bin/bash.exe') for root in Path(git).resolve().parents
                              if (root / 'bin/bash.exe').is_file()), None) if git else None
        else:
            self.bash = shutil.which('bash')
        if not self.bash:
            self.skipTest('Bash unavailable; config seed shell behavior was not checked.')
        self.temp = tempfile.TemporaryDirectory(prefix='luminax-seed-test-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source, self.target = root / 'seed', root / 'target'
        self.source.mkdir()
        self.target.mkdir()
        (self.source / 'example.json').write_text('{"example":true}')

    def run_seed(self):
        env = {key: value for key, value in os.environ.items() if key not in {'BASH_ENV', 'ENV', 'CDPATH'}}
        env.update(LUMINAX_CONFIG_SEED_DIR=self.source.as_posix(), LUMINAX_CONFIG_TARGET_DIR=self.target.as_posix())
        return subprocess.run([self.bash, '--noprofile', '--norc', str(TOOLS / 'seed_config.sh')],
                              env=env, capture_output=True, text=True, timeout=15)

    def test_seed_once_and_retain_operator_changes(self):
        result = self.run_seed()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / '.initialized').is_file())
        (self.target / 'example.json').write_text('operator change')
        self.assertEqual(self.run_seed().returncode, 0)
        self.assertEqual((self.target / 'example.json').read_text(), 'operator change')

    def test_refuses_nonempty_unmarked_destination(self):
        (self.target / 'retained.txt').write_text('operator data')
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.target / 'retained.txt').read_text(), 'operator data')
        self.assertFalse((self.target / 'example.json').exists())

    def test_local_certificate_helper_is_non_overwriting(self):
        helper = Path(self.temp.name) / 'generate-local-certs.sh'
        shutil.copyfile(PAYLOAD / helper.name, helper)
        try:
            executable = setup.find_openssl()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        env = {key: value for key, value in os.environ.items() if key not in {'BASH_ENV', 'ENV', 'CDPATH'}}
        env['PATH'] = str(Path(executable).parent) + os.pathsep + env.get('PATH', '')
        command = [self.bash, '--noprofile', '--norc', str(helper)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        certificate = helper.parent / 'certs/luminax.crt'
        key = helper.parent / 'certs/luminax.key'
        before = key.read_bytes()
        inspected = subprocess.run([executable, 'x509', '-in', str(certificate), '-noout', '-ext', 'subjectAltName'],
                                   capture_output=True, text=True, timeout=15, check=True)
        self.assertIn('DNS:localhost', inspected.stdout)
        self.assertIn('IP Address:127.0.0.1', inspected.stdout)
        self.assertNotEqual(subprocess.run(command, env=env, capture_output=True, timeout=15).returncode, 0)
        self.assertEqual(before, key.read_bytes())


if __name__ == '__main__':
    unittest.main()
