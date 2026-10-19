"""Manual browser fixture: synthetic upstream responses, real app/calculator. Never a live AI demo.
Run: python tests/document_browser_fixture.py --output-dir /tmp/mcdxkit-document-demo
"""
import argparse
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from test_pipeline import report, template
from mcdxkit.document_clients import ModelConfig, OpenRouter
from mcdxkit.documents import DocumentWorkflow
from mcdxkit.server import create_server


def pdf():
    content = b'BT /F1 18 Tf 50 740 Td (Synthetic specification - TEST ONLY) Tj 0 -40 Td (Use downdrag of 6 kip for this example.) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
    data = b'%PDF-1.4\n'; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(data); data += b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets[1:]: data += f'{offset:010d} 00000 n \n'.encode()
    return data + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'


class Provider(BaseHTTPRequestHandler):
    def do_GET(self): self.reply({'code': 0, 'data': {'docs': [{'id': 'document-1', 'run': 'DONE', 'progress': 1, 'chunk_count': 1}]}})
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path.endswith('/datasets'): value = {'code': 0, 'data': {'id': 'dataset-1'}}
        elif self.path.endswith('/documents'): value = {'code': 0, 'data': [{'id': 'document-1'}]}
        elif self.path.endswith('/retrieval'):
            value = {'code': 0, 'data': {'chunks': [{'id': 'chunk-1', 'document_id': 'document-1',
                'content': 'Synthetic specification: Use downdrag of 6 kip for this example.', 'positions': [[1, 50, 550, 40, 100]]}]}}
        elif self.path.endswith('/chat/completions'):
            content = {'sections': [{'heading': 'Foundation design basis', 'body': 'This synthetic example uses the supplied downdrag requirement. Confirm its applicability before accepting it.', 'evidence_ids': ['E1']}],
                       'suggestions': [{'variable': 'P_downdrag', 'value': 6, 'unit': 'kip', 'reason': 'The synthetic specification sets downdrag at 6 kip.', 'evidence_ids': ['E1']}],
                       'gaps': ['No engineering standards or independent engineering review have been supplied for this synthetic fixture.']}
            value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]}
        else: value = {'code': 0}
        self.reply(value)
    def reply(self, value):
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(value).encode())
    def log_message(self, *args): pass


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output-dir', type=Path, required=True); args = parser.parse_args()
    root = args.output_dir; root.mkdir(parents=True, exist_ok=True)
    raw = root / 'synthetic-loads.txt'; raw.write_text(report())
    tpl = root / 'synthetic-template.mcdx'; template(tpl)
    flow = DocumentWorkflow(root / '.documents')
    project = flow.create('Synthetic protocol test — not live AI', raw, tpl)
    flow.add_source(project['id'], 'synthetic-specification.pdf', pdf())
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:' + str(provider.server_port)
    os.environ.update(MCDXKIT_RAGFLOW_URL=url, MCDXKIT_RAGFLOW_API_KEY='synthetic-fixture',
                      MCDXKIT_RAGFLOW_EMBEDDING_MODEL='synthetic@local', OPENROUTER_API_KEY='synthetic-fixture',
                      MCDXKIT_OPENROUTER_MODEL='synthetic/fixture:free')
    def writer(_self):
        client = OpenRouter('synthetic-fixture', 'synthetic/fixture:free'); client.url = url; return client
    server = create_server(port=0, output_dir=root)
    print(f'CONTROLLED PROTOCOL FIXTURE: http://127.0.0.1:{server.server_port}/documents?project={project["id"]}', flush=True)
    try:
        with patch.object(ModelConfig, 'writer', writer): server.serve_forever()
    finally:
        server.server_close(); provider.shutdown(); provider.server_close()


if __name__ == '__main__': main()
