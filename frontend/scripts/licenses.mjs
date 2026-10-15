import {readFileSync,readdirSync,writeFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const lock=JSON.parse(readFileSync(resolve(root,'package-lock.json'),'utf8'));
const notices=['Third-party licenses for the bundled MCDXKit browser workspace.\n'];
for(const [path,entry] of Object.entries(lock.packages).sort()){
  if(!path||entry.dev)continue;
  const directory=resolve(root,path);
  const manifest=JSON.parse(readFileSync(resolve(directory,'package.json'),'utf8'));
  notices.push(`\n--- ${manifest.name} ${manifest.version} (${manifest.license||'see below'}) ---\n`);
  const files=readdirSync(directory).filter(name=>/^(license|licence|notice)(\.|$)/i.test(name));
  // React's empty client-only marker omits LICENSE from its npm tarball.
  // It belongs to facebook/react and uses that repository's MIT license.
  if(!files.length&&manifest.name==='client-only'&&manifest.version==='0.0.1'){
    notices.push('License source: https://github.com/facebook/react/blob/main/LICENSE');
    notices.push(readFileSync(resolve(root,'node_modules/react/LICENSE'),'utf8'));
    continue;
  }
  if(!files.length)throw new Error('Missing license for '+manifest.name);
  for(const name of files)notices.push(readFileSync(resolve(directory,name),'utf8'));
}
writeFileSync(resolve(root,'../src/mcdxkit/web/workspace-dist/LICENSES.txt'),notices.join('\n'));
