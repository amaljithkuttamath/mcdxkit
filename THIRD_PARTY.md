# Calculator attribution

The required calculator is [CalcpadCE](https://github.com/imartincei/CalcpadCE),
built at commit `0b20dba11ebd50b303eedb57e8ec042c272c68ab`, under the MIT license.
The setup command and Docker build include its LICENSE alongside the engine.
CalcpadCE's NuGet dependencies retain their respective licenses; see its project
files and upstream third-party notices. This repository's translator and bridge
are mcdxkit code. No PTC Mathcad software or engineering templates are distributed.

## Optional RAGFlow deployment

`scripts/setup_ragflow.py` fetches the separately deployed Apache-2.0 RAGFlow project at a pinned revision; it is not bundled in the Python wheel. Its services/models retain their own licenses. OpenRouter is an external API service, not a bundled dependency. See `deploy/ragflow.md`.

## Local environment configuration

`python-dotenv` (locked to 1.2.4, BSD-3-Clause) parses the launch directory's `.env` file locally. MCDXKit uses its `dotenv_values` API without interpolation or environment mutation; this requires no network service. Its distribution retains the upstream license.

## PDF.js

Original PDF viewing bundles PDF.js (`pdfjs-dist` 6.4.299), Apache-2.0, from the official npm distribution. License and font/codec notices are retained under `src/mcdxkit/web/pdfjs`. PDF JavaScript is not executed by the source-page renderer. Update the library, worker, fonts, cmaps and WASM assets together; verify original-page rendering after upgrades.

## Project workspace and orchestration libraries

The browser workspace reuses React and React DOM (MIT), React Aria Components and its Adobe React Spectrum dependencies (Apache-2.0), TanStack Query (MIT), eventsource-parser (MIT), react-markdown and remark-gfm (MIT). Exact resolved versions are in `frontend/package-lock.json`; the build ships upstream notices in `web/workspace-dist/LICENSES.txt`. React's `client-only` marker omits a license file from its npm archive, so the collector includes the parent React repository's MIT notice.

Python integrations reuse pypdf (BSD-3-Clause), Huey (MIT), the AG-UI protocol SDK `ag-ui-protocol` (MIT, https://github.com/ag-ui-protocol/ag-ui; used only for event models and SSE encoding), PyJWT (MIT) with `cryptography` (Apache-2.0 or BSD-3-Clause) for ChatGPT ID-token verification, HTTPX (BSD-3-Clause), the OpenAI Python SDK `openai` (Apache-2.0) for ChatGPT plan Responses requests, and LangChain/LangGraph/langchain-openai (MIT) at versions resolved in `requirements.lock`. Their installed distributions retain upstream license files. LangSmith tracing is disabled for document conversations. No model weights or proprietary Mathcad runtime are bundled.
