"""Check that a built wheel includes the browser assets and calculator bridge."""
from email.parser import BytesParser
import re
import tarfile
from pathlib import Path
from zipfile import ZipFile


def check_sdist(path):
    required = {'scripts/install_skill.py', 'scripts/audit_repository.py', 'scripts/report_audit.py',
                'scripts/test_installed.py', 'scripts/cli.py', 'scripts/check_package.py',
                'tests/test_pipeline.py', 'tests/test_repository_audit.py', 'tests/evaluate_fixture.py',
                'tests/fixtures/review/seed529_clean.txt'}
    with tarfile.open(path, 'r:gz') as archive:
        prefix = path.name.removesuffix('.tar.gz') + '/'
        members = {m.name.removeprefix(prefix): m for m in archive.getmembers() if m.isfile()}
        missing = required - members.keys()
        if missing:
            raise ValueError('Source archive omits test helpers/fixtures: ' + ', '.join(sorted(missing)))
        if any(members[name].size == 0 for name in required):
            raise ValueError('Source archive has an empty required test helper/fixture')
    print(path.name + ': test helpers and synthetic fixtures included')


def main():
    wheels = list(Path('dist').glob('mcdxkit-*.whl'))
    if len(wheels) != 1:
        raise SystemExit('Expected exactly one mcdxkit wheel in dist/')
    required = {
        'mcdxkit/web/pdfjs/pdf.mjs', 'mcdxkit/web/pdfjs/pdf.worker.mjs', 'mcdxkit/web/pdfjs/LICENSE',
        'mcdxkit/web/workspace.css', 'mcdxkit/web/documents.html', 'mcdxkit/web/documents.js', 'mcdxkit/web/documents.css',
        'mcdxkit/web/index.html', 'mcdxkit/web/app.js', 'mcdxkit/web/style.css',
        'mcdxkit/web/fonts/plex-regular.ttf', 'mcdxkit/web/fonts/plex-medium.ttf',
        'mcdxkit/web/fonts/plex-semibold.ttf', 'mcdxkit/web/fonts/OFL.txt',
        'mcdxkit/calcpad_bridge/Program.cs', 'mcdxkit/calcpad_bridge/Bridge.csproj',
    }
    with ZipFile(wheels[0]) as wheel:
        metadata_path = next(name for name in wheel.namelist() if name.endswith('.dist-info/METADATA'))
        metadata = BytesParser().parsebytes(wheel.read(metadata_path))
        if metadata.get('License-Expression') != 'MIT':
            raise SystemExit('Package must declare the MIT license')
        license_dir = metadata_path.rsplit('/', 1)[0] + '/licenses/'
        for license_file in ('LICENSE', 'THIRD_PARTY.md'):
            if license_file not in metadata.get_all('License-File', []):
                raise SystemExit('Missing license metadata: ' + license_file)
            if license_dir + license_file not in wheel.namelist():
                raise SystemExit('Missing packaged license: ' + license_file)
            if not wheel.read(license_dir + license_file).strip():
                raise SystemExit('Empty packaged license: ' + license_file)
        description = metadata.get_payload()
        for target in re.findall(r'\]\(([^)]+)\)', description):
            if not re.match(r'(https?://|mailto:|#)', target):
                raise SystemExit('PyPI description contains a relative link: ' + target)
        missing = required - set(wheel.namelist())
        if missing:
            raise SystemExit('Missing package assets: ' + ', '.join(sorted(missing)))
        if any(not wheel.read(name) for name in required):
            raise SystemExit('Package contains an empty required asset')
    print(f'{wheels[0].name}: browser assets, licenses and bridge included')
    archives = list(Path('dist').glob('mcdxkit-*.tar.gz'))
    if len(archives) != 1:
        raise SystemExit('Expected exactly one mcdxkit source archive in dist/')
    check_sdist(archives[0])


if __name__ == '__main__':
    main()
