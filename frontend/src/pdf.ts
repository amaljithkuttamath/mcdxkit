import {request} from './api';
type PDFPageProxy = {getViewport:(options:{scale:number})=>{width:number;height:number};render:(options:unknown)=>{promise:Promise<void>;cancel:()=>void}};
/** PDF.js keeps destroy() on the loading task, not the PDFDocumentProxy; expose one owned handle. */
export type PDFDocument = {numPages:number;destroy:()=>Promise<void>;getPage:(n:number)=>Promise<PDFPageProxy>};
export async function loadPDF(url:string):Promise<PDFDocument> {
  const path='/pdfjs/pdf.mjs';const pdf=await import(/* @vite-ignore */ path);
  pdf.GlobalWorkerOptions.workerSrc='/pdfjs/pdf.worker.mjs';
  const data=await (await request(url)).arrayBuffer();
  const task=pdf.getDocument({data,isEvalSupported:false,useSystemFonts:true,standardFontDataUrl:'/pdfjs/standard_fonts/',cMapUrl:'/pdfjs/cmaps/',cMapPacked:true,wasmUrl:'/pdfjs/wasm/'});
  let proxy:{numPages:number;getPage:(n:number)=>Promise<PDFPageProxy>};
  // Cleanup failure must not replace the load failure the user needs to see.
  try{proxy=await task.promise;}catch(e){await task.destroy().catch(()=>{});throw e;}
  let destroyed:Promise<void>|undefined;
  return {numPages:proxy.numPages,getPage:n=>proxy.getPage(n),destroy:()=>destroyed??=task.destroy()};
}
