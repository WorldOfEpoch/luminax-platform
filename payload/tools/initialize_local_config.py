"""Create local configuration once, without starting services or contacting providers."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess


def find_openssl(requested: str = 'openssl') -> str:
    executable = shutil.which(requested)
    if executable:
        return executable
    if requested == 'openssl':
        git = shutil.which('git')
        if git:
            # Git for Windows ships OpenSSL; derive the location from its actual executable.
            for parent in Path(git).resolve().parents:
                candidate = parent / 'usr' / 'bin' / 'openssl.exe'
                if candidate.is_file():
                    return str(candidate)
    raise RuntimeError('OpenSSL is required for the local web password hash. Put it on PATH or use --openssl.')


def initialize(payload: Path, model: str = '', openssl: str = 'openssl') -> list[Path]:
    payload = payload.resolve()
    if model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}', model):
        raise ValueError('Use an installed model tag without whitespace or shell syntax.')
    targets = [payload / '.env', payload / 'runtime/luminax.htpasswd', payload / 'runtime/turnserver.conf']
    if any(path.exists() or path.is_symlink() for path in targets):
        raise FileExistsError('Local configuration already exists; it will not be overwritten.')
    if not (payload / 'runtime').resolve().is_relative_to(payload):
        raise ValueError('The runtime directory must be inside this checkout, not a symlink.')
    executable = find_openssl(openssl)
    template = payload / '.env.example'
    values = {}
    for line in template.read_text(encoding='utf-8').splitlines():
        if re.match(r'^[A-Z][A-Z0-9_]*=', line):
            key, value = line.split('=', 1)
            values[key] = value
    web_password = secrets.token_hex(32)
    for key in ['CONTROL_TOKEN', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD', 'UNDERFOOT_VOICE_JOIN_SECRET', 'UNDERFOOT_VOICE_TURN_SHARED_SECRET']:
        values[key] = secrets.token_hex(32)
    values['UNDERFOOT_DB_PASSWORD'] = values['MYSQL_PASSWORD']
    values['TESTER_ACCOUNT_VAULT_KEY'] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
    values['EXTERNAL_ACCESS_PASSWORD'] = values['LUMINAX_WEB_PASSWORD'] = web_password
    values['EXTERNAL_ACCESS_USER'] = values['LUMINAX_WEB_USER'] = 'operator'
    for key in list(values):
        if key.startswith('OLLAMA_CHAT_MODEL_'):
            values[key] = model
    result = subprocess.run([executable, 'passwd', '-6', '-stdin'], input=web_password + '\n',
                            capture_output=True, text=True, timeout=15, check=True)
    encoded = result.stdout.strip()
    if not re.fullmatch(r'\$6\$[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}', encoded):
        raise RuntimeError('OpenSSL did not return the expected password hash.')
    env_text = '# Private local configuration. Never publish this file.\n' + '\n'.join(f'{k}={v}' for k, v in values.items()) + '\n'
    turn_text = '\n'.join([
        '# Generated local-only TURN configuration. Never publish this file.',
        'listening-port=3478', 'min-port=49160', 'max-port=49200', 'fingerprint',
        'use-auth-secret', 'realm=localhost', 'server-name=localhost',
        'static-auth-secret=' + values['UNDERFOOT_VOICE_TURN_SHARED_SECRET'],
        'no-cli', 'no-tls', 'no-dtls', 'no-multicast-peers', 'no-loopback-peers',
        'log-file=stdout', 'simple-log',
    ]) + '\n'
    contents = [env_text, 'operator:' + encoded + '\n', turn_text]
    created = []
    try:
        for path, content in zip(targets, contents):
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(content)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='', help='Exact tag of an already installed model; no download is performed.')
    parser.add_argument('--openssl', default='openssl', help='OpenSSL executable name or absolute path.')
    args = parser.parse_args()
    initialize(Path(__file__).resolve().parents[1], args.model, args.openssl)
    print('Created .env and runtime authentication files. No services were started.')
    print('Open .env privately for the operator password and control token. Runtime remains disabled.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
