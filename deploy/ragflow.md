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

Open `http://127.0.0.1:8081`, complete RAGFlow account setup, configure a **local embedding model**, and create a RAGFlow API key. For any bootstrap/admin password, inspect your private upstream `.env`/startup instructions directly; do not paste secrets into logs or issues. Use upstream [model setup](https://ragflow.io/docs/v1.0.0-rc1/deploy_local_llm) for Ollama or Xinference. RAGFlow containers must be able to reach the model endpoint; container `localhost` is not the host. Keep model endpoints private. If adding a reranker, configure its exact identifier too.

RAGFlow is Apache-2.0. Its code, images, inference dependencies and model weights retain their respective licenses. Our helper wraps its deployment; it does not fork its services. See [upstream Compose](https://github.com/infiniflow/ragflow/tree/14bb02ee4584c1ab18f0c722c48beecd6bbf9860/docker).

## Configure MCDXKit

For browser setup, start MCDXKit and open **Design packages → Service settings**. Enter both keys, the RAGFlow URL and model identifiers there. The container overlay can start without model API-key files.

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

OpenRouter receives retrieved excerpts and template input definitions when a user requests a draft. Rate limits are surfaced with no automatic retry or paid fallback. Provider training is denied by the request; availability can be reduced. Check provider retention and project sharing requirements before sending private documents. Model accounts and keys are not provisioned automatically.

## Operations and offline limits

Monitor RAGFlow health and indexing status. Keep source hashes, project database and generated artifacts backed up together. Local ports, disk, RAM and model storage must be available. This is a single-host trusted-workspace deployment. Do not add replicas or claim multi-user isolation without the separate identity/job/storage work in [distributed architecture](../docs/distributed-architecture.md).

Stored originals, reviewed documents and calculation packages remain inspectable offline. Ordinary conversion still runs locally. **The OpenRouter authoring path needs internet.** A local answering-provider adapter is future work; do not describe this release as offline AI drafting.

### Browser credential setup

In **Design packages → Service settings**, users can save the RAGFlow endpoint/API key, embedding/reranker IDs, and OpenRouter API key/free model directly. These workspace overrides are stored server-side and also used by the CLI. Blank key fields preserve existing keys. See [credential storage and shared-workspace scope](../docs/document-workflow.md#browser). RAGFlow must still be running with its embedding model configured; saving credentials does not deploy it.
