import {test,expect,vi} from 'vitest';
import {loadPDF} from './pdf';
import {request} from './api';
const mock=vi.hoisted(()=>({getDocument:vi.fn(),GlobalWorkerOptions:{workerSrc:''}}));
vi.mock('/pdfjs/pdf.mjs',()=>mock);
vi.mock('./api',()=>({request:vi.fn()}));

test('the PDF loading task owns cleanup even when the document proxy has no destroy',async()=>{
  const document={numPages:3,getPage:vi.fn(function(this:{numPages:number},n:number){return Promise.resolve({number:n,owner:this.numPages});})};
  const destroy=vi.fn().mockResolvedValue(undefined);
  mock.getDocument.mockReturnValue({promise:Promise.resolve(document),destroy});
  vi.mocked(request).mockResolvedValue(new Response(new Uint8Array([37,80,68,70])));
  const file=await loadPDF('/api/source/example');
  expect(file.numPages).toBe(3);
  expect(await file.getPage(2)).toEqual({number:2,owner:3});
  await Promise.all([file.destroy(),file.destroy()]);
  expect(destroy).toHaveBeenCalledTimes(1);
});

test('a cleanup rejection does not replace the actual PDF loading error',async()=>{
  const destroy=vi.fn().mockRejectedValue(new Error('cleanup failed'));
  mock.getDocument.mockReturnValue({promise:Promise.reject(new Error('source is invalid')),destroy});
  vi.mocked(request).mockResolvedValue(new Response(new Uint8Array([37,80,68,70])));
  await expect(loadPDF('/api/source/example')).rejects.toThrow('source is invalid');
  expect(destroy).toHaveBeenCalledTimes(1);
});
