#!/usr/bin/env python3
"""Prepare a pinned upstream RAGFlow Compose stack. Does not start containers."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

REVISION = '14bb02ee4584c1ab18f0c722c48beecd6bbf9860'  # v1.0.0-rc1
REPOSITORY = 'https://github.com/infiniflow/ragflow.git'


def private_config(upstream):
    value = copy.deepcopy(upstream)
    value['name'] = 'mcdxkit-ragflow'
    for service in value['services'].values():
        service['ports'] = []
        service.pop('profiles', None)  # `compose config` has already selected active profiles.
        service.pop('container_name', None)
    for name, config in value.get('volumes', {}).items():
        config['name'] = 'mcdxkit-ragflow_' + name
    for name, config in value.get('networks', {}).items():
        config['name'] = 'mcdxkit-ragflow_' + name
    # Upstream gives Elasticsearch almost the whole 8 GiB Docker VM; JVM automatic
    # sizing then starves the parser and databases during real PDF ingestion.
    if 'es01' in value['services']:
        search = value['services']['es01']
        search.setdefault('environment', {})['ES_JAVA_OPTS'] = '-Xms1g -Xmx1g'
        search['mem_limit'] = 2 * 1024**3
    api = value['services']['ragflow-cpu']
    api['ports'] = [{'target': port, 'published': str(host), 'host_ip': '127.0.0.1', 'protocol': 'tcp'}
                    for host, port in ((8081, 80), (9380, 9380), (9381, 9381))]
    api['networks']['knowledge'] = {'aliases': ['ragflow-api']}
    value['networks']['knowledge'] = {'name': 'mcdxkit-knowledge', 'driver': 'bridge'}
    return value


def prepare(directory):
    directory = directory.resolve()
    # Refuse reuse: preserving passwords and existing state is more important than convenience.
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    upstream = directory / 'upstream'
    subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', REPOSITORY, str(upstream)], check=True)
    subprocess.run(['git', '-C', str(upstream), 'sparse-checkout', 'set', 'docker'], check=True)
    subprocess.run(['git', '-C', str(upstream), 'checkout', '--detach', REVISION], check=True)
    actual = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != REVISION: raise ValueError('Upstream revision verification failed.')
    docker = upstream / 'docker'
    env_path = docker / '.env'
    env_text = env_path.read_text()
    # All active upstream credentials are unique to this local deployment.
    keys = ('ELASTIC_PASSWORD', 'MYSQL_PASSWORD', 'MINIO_PASSWORD', 'REDIS_PASSWORD', 'CLICKHOUSE_PASSWORD', 'ADMIN_DEFAULT_PASSWORD')
    for key in keys:
        env_text = re.sub(r'^' + key + r'=.*$', key + '=' + secrets.token_hex(24), env_text, flags=re.M)
    env_text += '\nSECRET_KEY=' + secrets.token_hex(32) + '\n'
    env_path.write_text(env_text); env_path.chmod(0o600)
    env = os.environ.copy()
    # Do not inherit unrelated Compose/model/password overrides from the caller.
    for key in re.findall(r'^([A-Z][A-Z0-9_]*)=', env_text, flags=re.M): env.pop(key, None)
    env.update(DOC_ENGINE='elasticsearch', DEVICE='cpu', METADATA_DB_PROFILE='mysql',
               COMPOSE_PROFILES='elasticsearch,cpu,metadata-mysql,ragflow-go,clickhouse')
    rendered = subprocess.check_output(['docker', 'compose', '--project-directory', str(docker), '--env-file', str(env_path),
                                        '-f', str(docker / 'docker-compose.yml'), 'config', '--format', 'json'], env=env, text=True)
    output = directory / 'compose.json'
    output.write_text(json.dumps(private_config(json.loads(rendered)), indent=2)); output.chmod(0o600)
    subprocess.run(['docker', 'compose', '-f', str(output), 'config', '--quiet'], check=True)
    (directory / 'UPSTREAM.txt').write_text(REPOSITORY + '\n' + REVISION + '\nApache-2.0. See upstream/LICENSE.\n')
    print('Prepared and validated Compose configuration at ' + str(output))
    print('Start on a supported Linux Docker host: docker compose -f ' + str(output) + ' up -d')
    print('RAGFlow UI: http://127.0.0.1:8081 ; API: http://127.0.0.1:9380')
    print('Configuration contains generated secrets. Keep this directory private and preserve it with its Docker volumes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.mcdxkit/ragflow'))
    args = parser.parse_args()
    prepare(args.directory)
