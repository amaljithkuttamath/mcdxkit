# Calculator attribution

The required calculator is [CalcpadCE](https://github.com/imartincei/CalcpadCE),
built at commit `0b20dba11ebd50b303eedb57e8ec042c272c68ab`, under the MIT license.
The setup command and Docker build include its LICENSE alongside the engine.
CalcpadCE's NuGet dependencies retain their respective licenses; see its project
files and upstream third-party notices. This repository's translator and bridge
are mcdxkit code. No PTC Mathcad software or engineering templates are distributed.

## Optional RAGFlow deployment

`scripts/setup_ragflow.py` fetches the separately deployed Apache-2.0 RAGFlow project at a pinned revision; it is not bundled in the Python wheel. Its services/models retain their own licenses. OpenRouter is an external API service, not a bundled dependency. See `deploy/ragflow.md`.

## PDF.js

Original PDF viewing bundles PDF.js (`pdfjs-dist` 6.4.299), Apache-2.0, from the official npm distribution. License and font/codec notices are retained under `src/mcdxkit/web/pdfjs`. PDF JavaScript is not executed by the source-page renderer. Update the library, worker, fonts, cmaps and WASM assets together; verify original-page rendering after upgrades.
