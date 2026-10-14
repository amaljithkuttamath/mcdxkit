# RAGFlow and OpenRouter setup

This deployment extends MCDXKit with a separate RAGFlow stack. The application integration is described in [Document workflow](../docs/document-workflow.md). No cloud resources are provisioned.

## Prepare the upstream stack

On a supported Linux Docker host, install Git, Python 3 and Docker Compose. RAGFlow recommends starting with 4 CPU cores, 16 GB RAM and 50 GB disk; allow more for MCDXKit, document storage and local models. The pinned upstream release is **v1.0.0-rc1**, commit `14bb02ee4584c1ab18f0c722c48beecd6bbf9860` (a release candidate, not a stability guarantee). Its current README does not claim macOS support. Preparing/validating Compose on a Mac does not establish runtime compatibility.

From the repository:

```bash
python3 scripts/setup_ragflow.py
# Review .mcdxkit/ragflow/compose.json locally; it contains generated credentials.
docker compose -f .mcdxkit/ragflow/compose.json up -d
docker compose -f .mcdxkit/ragflow/compose.json ps
```

The helper fetches the exact upstream revision, reuses its active Compose services, generates unique passwords, removes published database/search/storage ports, and exposes the RAGFlow UI/API/admin only on loopback (`8081`, `9380`, `9381`). A separate `mcdxkit-knowledge` network connects only the RAGFlow API service to our application. The helper refuses to overwrite an existing deployment. Preserve its generated configuration and Docker volumes; upgrades require an explicit backup/migration. Startup needs internet to pull images/models. Check upstream Elasticsearch host requirements such as `vm.max_map_count` before startup.

Open MCDXKit **Documents → Connect services**. Enter one OpenRouter key and choose free answer and search models from the live catalogs, then save. The app creates a dedicated private account/API credential in the bundled local RAGFlow deployment and configures RAGFlow's native OpenRouter embedding adapter. No Ollama, local weights, manual RAGFlow URL, or second user-supplied API key is needed. The internal account file is owner-only `account.json` beside Compose; back it up with the deployment, and never commit it. Existing configured service credentials are preserved.

RAGFlow is Apache-2.0. Its code, images, inference dependencies and model weights retain their respective licenses. Our helper wraps its deployment; it does not fork its services. See [upstream Compose](https://github.com/infiniflow/ragflow/tree/14bb02ee4584c1ab18f0c722c48beecd6bbf9860/docker).

## Configure MCDXKit

For browser setup, use the one-key flow above. **Advanced connection settings** remains available for administrators of separately managed RAGFlow deployments. Automatic account creation is restricted to the prepared loopback bundle; container/cloud deployments provision their internal service credential through their administrator. The app does not mount the Docker socket.

For environment-based host setup, create `.secrets/ragflow-api-key` and `.secrets/openrouter-api-key` using your editor or secret manager. Set permissions to owner-only. Store the OpenRouter key in the latter. Copy `deploy/documents.env.example` to a local `.env` and set the exact installed embedding identifier and an available explicit `provider/model:free` from OpenRouter. The example has no hard-coded model because availability changes. `openrouter/free`, paid models and automatic paid fallbacks are rejected.

For a host-run CLI/server, export these values into its process environment. The CLI does not automatically execute or load `.env` files. Environment secrets or `_FILE` paths are supported. Verify without revealing keys:

```bash
mcdxkit documents config
mcdxkit serve --output-dir ./results
```

For the existing container deployment, keep its `.secrets/access-token` setup, then:

```bash
docker compose -f compose.yaml -f compose.documents.yaml config --quiet
docker compose -f compose.yaml -f compose.documents.yaml up -d --build
```

Compose reads `.env`; the overlay uses browser-saved credentials in the persistent app data volume. The app access token is still required. For centrally managed container credentials, add a local Compose override mounting API-key files and setting their `_FILE` paths; do not put keys in the checked-in Compose file. Start the prepared RAGFlow stack first so the external network exists. The overlay raises the app memory limit to 2 GiB for PDF uploads. Caddy remains the optional HTTPS entrypoint for MCDXKit. RAGFlow administration is local-only; on a remote host use an SSH tunnel rather than publishing its admin ports.

OpenRouter receives document text for embedding during indexing and queries during retrieval, plus retrieved excerpts and template input definitions when a user requests a draft. Rate limits are surfaced with no automatic retry or paid fallback. Draft requests deny provider training; embedding requests use RAGFlow’s native adapter and the OpenRouter account/provider privacy policy. These are distinct policies; free model availability does not prove private-data suitability. Check provider retention and project sharing requirements before sending private documents. An OpenRouter account/key is still required; the app provisions only its bundled RAGFlow account.

## Operations and offline limits

Monitor RAGFlow health and indexing status. Keep source hashes, project database and generated artifacts backed up together. Local ports, disk, RAM and model storage must be available. This is a single-host trusted-workspace deployment. Do not add replicas or claim multi-user isolation without the separate identity/job/storage work in [distributed architecture](../docs/distributed-architecture.md).

Stored originals, reviewed documents and calculation packages remain inspectable offline. Ordinary conversion still runs locally. **The OpenRouter authoring path needs internet.** A local answering-provider adapter is future work; do not describe this release as offline AI drafting.

### Browser credential setup

One OpenRouter key is saved privately server-side and configured into RAGFlow's native OpenRouter provider. Blank key fields preserve existing keys. The model catalogs are refreshed without sending documents. Answer models follow account preferences; the embedding catalog is global, so listing does not prove that a particular key/provider can execute it. No paid models or paid fallback are selected. Changing an existing embedding model is blocked to preserve the meaning of stored vectors; use a new workspace for a different model.

The browser detects Docker/service health and can start a prepared local stack. Set `MCDXKIT_RAGFLOW_DIRECTORY` if the bundle is outside `.mcdxkit/ragflow`. The setup script still prepares Compose explicitly; the app does not install Docker or provision cloud resources. Local account setup and service health have been exercised against the pinned image; live embedding/answer execution requires a working OpenRouter key and remains a separate validation step.
