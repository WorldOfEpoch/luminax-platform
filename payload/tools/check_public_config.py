"""Check public configuration without reading local secrets or starting containers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


DISABLED_FLAGS = (
    'LUMINAX_RUNTIME_ENABLED', 'LUMINAX_HOST_EXECUTION_ENABLED',
    'LUMINAX_BROWSER_ACTIONS_ENABLED', 'LUMINAX_NETWORK_SCAN_ENABLED',
    'LUMINAX_FINANCE_SIMULATION_ENABLED', 'OPENAI_ENABLE', 'REALTIME_ENABLED',
    'LUMINAX_AUTO_APPLY_ENABLED',
    'SELF_UPGRADE_ENABLED', 'AUTOPILOT_ENABLED', 'PROACTIVE_AUTONOMY_ENABLED',
    'PROACTIVE_AUTO_EXECUTE', 'HIVE_DIRECTOR_ENABLED', 'OLLAMA_WARMUP_ON_BOOT',
    'TRUST_PROXY_AUTH_HEADERS', 'UNDERFOOT_NEWEDEN_ENABLED', 'UNDERFOOT_LOAD_DOTENV',
    'UNDERFOOT_DEDICATED_IMAGE_AUTO_BUILD', 'EDGE_IMAGE_WARMUP_ON_START',
)
PRIVATE_KEYS = (
    'CONTROL_TOKEN', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD',
    'UNDERFOOT_VOICE_JOIN_SECRET', 'UNDERFOOT_VOICE_TURN_SHARED_SECRET',
    'TESTER_ACCOUNT_VAULT_KEY', 'EXTERNAL_ACCESS_PASSWORD', 'LUMINAX_WEB_PASSWORD',
)


def template_values(payload: Path) -> dict[str, str]:
    result = {}
    for line in (payload / '.env.example').read_text(encoding='utf-8').splitlines():
        if re.match(r'^[A-Z][A-Z0-9_]*=', line):
            key, value = line.split('=', 1)
            if key in result:
                raise ValueError(f'Duplicate public variable: {key}')
            result[key] = value
    return result


def check_source(payload: Path) -> dict[str, int]:
    values = template_values(payload)
    for key in DISABLED_FLAGS:
        if values.get(key) != 'false':
            raise ValueError(f'Public default must be disabled: {key}')
    for key, value in values.items():
        if (key in PRIVATE_KEYS or key.endswith(('_TOKEN', '_API_KEY', '_PASSWORD', '_PUBLIC_KEY', '_APPLICATION_ID'))) and value:
            raise ValueError(f'Public credential/registration must be blank: {key}')
    configs = list((payload / 'config').rglob('*.json'))
    for path in configs:
        text = path.read_text(encoding='utf-8')
        if re.search(r'"[0-9]{17,20}"', text) or 'chat_attachment:' in text:
            raise ValueError(f'Public configuration retains a private account or conversation reference: {path.name}')
        data = json.loads(text)
        if not isinstance(data, (dict, list)):
            raise ValueError(f'Configuration must be an object or array: {path.name}')
    policy = json.loads((payload / 'config/runtime-policy.json').read_text())
    if not policy.get('workers_paused') or policy.get('execution_profile') not in {'pro_only', 'api_enabled'}:
        raise ValueError('Runtime policy must pause workers and use an implemented execution profile.')
    orchestration = policy.get('orchestration', {})
    if orchestration.get('brain', {}).get('enabled') or orchestration.get('memory_maintenance', {}).get('enabled'):
        raise ValueError('Public runtime policy must not enable background orchestration.')
    for alias in ['server-compose.yml', 'compose.server.yml', 'compose.pc.yml', 'edge-compose.yml']:
        lines = [line.strip() for line in (payload / alias).read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        if lines != ['include:', '- path: ./docker-compose.yml']:
            raise ValueError(f'{alias} must delegate to the canonical Compose definition.')
    registry = json.loads((payload / 'config/workspace-registry.json').read_text())
    for project in registry['projects']:
        key = project['project_key']
        if not re.fullmatch('[a-z0-9-]+', key):
            raise ValueError('Project key is not a safe identifier.')
        profile = payload / 'config/olympus/projects' / f'{key}.json'
        if not profile.is_file():
            raise ValueError(f'Project registry has no matching profile: {key}')
        if project.get('codex_thread') or project.get('sync_targets'):
            raise ValueError('Public registry must not contain private thread or deployment records.')
    nginx = (payload / 'nginx/default.conf').read_text()
    if 'auth-basic.conf' not in nginx or 'resolver 127.0.0.11' not in nginx:
        raise ValueError('Local proxy needs authentication and runtime upstream resolution.')
    return {'json_files': len(configs), 'template_variables': len(values)}


def validate_compose_model(payload: Path, model: dict) -> int:
    services = model.get('services', {})
    if not services:
        raise ValueError('Compose rendered no services.')
    generated_binds = {'runtime/luminax.htpasswd', 'runtime/turnserver.conf', 'certs'}
    for name, service in services.items():
        if not service.get('profiles') or service.get('restart', 'no') != 'no':
            raise ValueError(f'{name} must require an explicit profile with restart=no.')
        if service.get('privileged') or service.get('network_mode') == 'host' or service.get('pid') == 'host':
            raise ValueError(f'{name} has host authority enabled.')
        for port in service.get('ports', []):
            if port.get('host_ip') != '127.0.0.1':
                raise ValueError(f'{name} exposes a non-loopback host port.')
        for dependency in service.get('depends_on', {}):
            if dependency not in services:
                raise ValueError(f'{name} references missing dependency {dependency}.')
        build = service.get('build')
        if build:
            context = Path(build['context']).resolve()
            if not context.is_relative_to(payload.resolve()):
                raise ValueError(f'{name} builds from outside payload.')
            dockerfile = context / build.get('dockerfile', 'Dockerfile')
            if not dockerfile.is_file():
                raise ValueError(f'{name} has no Dockerfile.')
            for line in dockerfile.read_text().splitlines():
                if line.upper().startswith('COPY ') and '--from=' not in line:
                    parts = line.split()[1:-1]
                    for source in parts:
                        if source.startswith('--'):
                            continue
                        if not any(context.glob(source)):
                            raise ValueError(f'{name} COPY source is missing: {source}')
        for volume in service.get('volumes', []):
            if 'docker.sock' in str(volume):
                raise ValueError(f'{name} mounts the Docker socket.')
            if volume['type'] != 'bind':
                continue
            source = Path(volume['source']).resolve()
            if not source.is_relative_to(payload.resolve()):
                raise ValueError(f'{name} mounts a host path outside payload.')
            rel = source.relative_to(payload.resolve()).as_posix()
            if not source.exists() and rel not in generated_binds:
                raise ValueError(f'{name} bind source is missing: {rel}')
            if not volume.get('read_only'):
                raise ValueError(f'{name} has a writable source bind: {rel}')
    return len(services)


def check_compose(payload: Path) -> dict[str, object]:
    docker = shutil.which('docker')
    if not docker:
        raise RuntimeError('Docker Compose CLI unavailable; Compose validation was not run.')
    values = template_values(payload)
    for key in PRIVATE_KEYS:
        values[key] = 'synthetic-check-value-' + '0' * 48
    # Explicit file and no-env-resolution prevent reading a real service .env.
    env = dict(os.environ)
    interpolation_keys = set(values)
    for path in payload.glob('*.yml'):
        interpolation_keys.update(re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)', path.read_text()))
    for key in list(env):
        if key in interpolation_keys or key.startswith('COMPOSE_'):
            env.pop(key)
    env['COMPOSE_DISABLE_ENV_FILE'] = 'true'
    with tempfile.TemporaryDirectory(prefix='luminax-compose-check-') as folder:
        sample = Path(folder) / 'synthetic.env'
        sample.write_text('\n'.join(f'{k}={v}' for k, v in values.items()) + '\n', encoding='utf-8')
        command = [docker, 'compose', '--project-directory', str(payload), '--env-file', str(sample),
                   '--profile', '*', '-f', str(payload / 'docker-compose.yml'),
                   'config', '--format', 'json', '--no-env-resolution']
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=45)
        if result.returncode:
            # Only synthetic configuration was supplied; never include rendered environments in output.
            raise RuntimeError('Compose configuration validation failed: ' + result.stderr.strip())
        model = json.loads(result.stdout)
        count = validate_compose_model(payload, model)
    return {'services': count, 'compose': 'passed', 'containers_started': 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', action='store_true', help='Also use Docker Compose config; never starts containers.')
    args = parser.parse_args()
    payload = Path(__file__).resolve().parents[1]
    summary = check_source(payload)
    if args.compose:
        summary.update(check_compose(payload))
    else:
        summary['compose'] = 'not requested'
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
