import {createElement, Fragment, useEffect, useRef, useState} from 'react';
import type {CSSProperties} from 'react';
import {Button} from 'react-aria-components';
import {loadPDF} from './pdf';
import type {PDFDocument} from './pdf';

type MathNode = {tag:string; text?:string|null; children?:MathNode[]};
type FlowNode = {tag:string; text?:string; style?:CSSProperties; children?:{node:FlowNode;tail?:string}[]};
type Region = {id:string;kind:string;top:string;left:string;width:string;height:string; presentation?:MathNode; expression?:string; variable?:string; text?:string; image?:string; flows?:FlowNode[];style?:CSSProperties; cached_result?:boolean};
export type WorksheetData = {name:string;page:{width:number;height:number;content_height:number;margins:number[];count:number}; regions:Region[];header:Region[];footer:Region[];has_cached_results:boolean;view:string};
const MATH=new Set(['mrow','mi','mn','mo','mtext','msub','msup','mfrac','msqrt','mroot','mtable','mtr','mtd']);
const FLOW=new Set(['div','p','span','b','i','u','br','ul','li','sub','sup']);
function math(node:MathNode):React.ReactNode {return createElement(MATH.has(node.tag)?node.tag:'mtext',{},node.text,...(node.children||[]).map((n,i)=><Fragment key={i}>{math(n)}</Fragment>));}
function flow(node:FlowNode):React.ReactNode {return createElement(FLOW.has(node.tag)?node.tag:'span',{style:node.style},node.text,...(node.children||[]).map((n,i)=><Fragment key={i}>{flow(n.node)}{n.tail}</Fragment>));}

/** Reuse the backend's bounded worksheet/MathML projection; never evaluate formulas here. */
export function Worksheet({data,note}:{data:WorksheetData;note?:string}) {
  const [number,setNumber]=useState(1),[selected,setSelected]=useState<Region|null>(null),[scale,setScale]=useState(1);
  const host=useRef<HTMLDivElement>(null); const page=data.page;
  useEffect(()=>{setNumber(1);setSelected(null);},[data.name]);
  useEffect(()=>{const observer=new ResizeObserver(()=>{if(host.current)setScale(Math.max(.25,Math.min(1,(host.current.clientWidth-48)/page.width)));}); if(host.current)observer.observe(host.current); return ()=>observer.disconnect();},[page.width]);
  function region(r:Region, top:number) {
    return <div key={r.id} className={'file-region '+r.kind} style={{...r.style,left:page.margins[0]+Number(r.left),top,width:Number(r.width),minHeight:Number(r.height)}}>
      <Button className="region-content" aria-label={'Inspect '+(r.variable||'region '+r.id)} onPress={()=>setSelected(r)}>
        {r.kind==='math'&&r.presentation?createElement('math',{},math(r.presentation)):<>
          {r.image&&r.image.startsWith('data:image/')&&<img src={r.image} alt="Diagram from worksheet" style={{width:'100%',height:Number(r.height)}}/>}
          {r.flows?.map((f,i)=><Fragment key={i}>{flow(f)}</Fragment>)}{!r.image&&!r.flows?.length?r.text:null}</>}
      </Button>
    </div>;
  }
  return <div className="worksheet-view" ref={host}>
    <div className="page-toolbar"><Button isDisabled={number<=1} onPress={()=>setNumber(number-1)}>Previous</Button><label>Page <select aria-label="Worksheet page" value={number} onChange={e=>setNumber(Number(e.target.value))}>{Array.from({length:page.count},(_,i)=><option key={i} value={i+1}>{i+1} of {page.count}</option>)}</select></label><Button isDisabled={number>=page.count} onPress={()=>setNumber(number+1)}>Next</Button><Button isDisabled={number===page.count} onPress={()=>setNumber(page.count)}>Last page</Button></div>
    <p className="file-note">{note??(data.has_cached_results?'Saved values from the original file. Calculate to produce fresh outputs.':'Native template equations. Calculate to produce outputs.')} Browser reconstruction; Prime rendering remains unverified.</p>
    <div className="sheet-shell" style={{width:page.width*scale,height:page.height*scale}}><div className="paper" style={{width:page.width,height:page.height,transform:`scale(${scale})`}}>
      {data.header.map(r=>region(r,Number(r.top)+12))}
      {data.regions.filter(r=>Math.floor(Number(r.top)/page.content_height)+1===number).map(r=>region(r,Number(r.top)-(number-1)*page.content_height+page.margins[1]))}
      {data.footer.map(r=>region(r,Number(r.top)+page.height-page.margins[3]))}
      <span className="sheet-page">{number} / {page.count}</span>
    </div></div>
    {selected&&<div className="region-inspection"><strong>{selected.variable||'Region '+selected.id}</strong><Button onPress={()=>setSelected(null)}>Close details</Button><pre>{selected.expression||selected.text}</pre>{selected.cached_result&&<p>Includes saved results; not a new calculation.</p>}</div>}
  </div>;
}

export function PDFPage(props:{url:string;initialPage:number}) {
  // A different citation owns a different renderer; never relabel the old canvas.
  return <PDFCanvas key={props.url+'#'+props.initialPage} {...props}/>;
}
function PDFCanvas({url,initialPage}:{url:string;initialPage:number}) {
  const canvas=useRef<HTMLCanvasElement>(null),container=useRef<HTMLDivElement>(null);
  const [doc,setDoc]=useState<PDFDocument|null>(null),[page,setPage]=useState(initialPage),[error,setError]=useState(''),[loading,setLoading]=useState(true),[width,setWidth]=useState(450),[rendered,setRendered]=useState('');
  const identity=url+'#'+page+'@'+width;
  useEffect(()=>{setPage(initialPage);},[url,initialPage]);
  useEffect(()=>{const observer=new ResizeObserver(()=>setWidth(container.current?.clientWidth||450));if(container.current)observer.observe(container.current);return ()=>observer.disconnect();},[]);
  useEffect(()=>{
    let alive=true; let destroy:(()=>void)|undefined; setDoc(null); setError(''); setLoading(true);
    // Teardown is best-effort: a failed release must never surface as an unhandled rejection.
    const release=(file:PDFDocument)=>{try{Promise.resolve(file.destroy()).catch(()=>{});}catch{/* already released */}};
    loadPDF(url).then(file=>{if(!alive){release(file);return;} destroy=()=>release(file);setDoc(file);setPage(p=>Math.min(file.numPages,Math.max(1,p)));})
      .catch(e=>{if(alive){setError(String(e?.message??e));setLoading(false);}});
    return ()=>{alive=false;destroy?.();destroy=undefined;};
  },[url]);
  useEffect(()=>{if(!doc)return;let alive=true;let cancel:(()=>void)|undefined;setLoading(true);setRendered('');setError('');
    (async()=>{const pdfPage=await doc.getPage(page);if(!alive||!canvas.current)return;const original=pdfPage.getViewport({scale:1});const viewport=pdfPage.getViewport({scale:Math.max(.25,(width-24)/original.width)});const ratio=Math.min(window.devicePixelRatio||1,2);
      canvas.current.width=viewport.width*ratio;canvas.current.height=viewport.height*ratio;canvas.current.style.width=viewport.width+'px';canvas.current.style.height=viewport.height+'px';
      const task=pdfPage.render({canvasContext:canvas.current.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0]});cancel=()=>task.cancel();await task.promise;if(alive){setRendered(identity);setLoading(false);}
    })().catch(e=>{if(alive&&e.name!=='RenderingCancelledException'){setError(e.message);setLoading(false);}});
    return ()=>{alive=false;cancel?.();};
  },[doc,page,width,identity]);
  return <div ref={container} className="pdf-document"><div className="page-toolbar"><Button isDisabled={page<=1||!doc} onPress={()=>setPage(page-1)}>Previous</Button><label>PDF page <input aria-label="PDF page" type="number" min="1" max={doc?.numPages||1} value={page} onChange={e=>{const n=Number(e.target.value);if(doc&&n>=1&&n<=doc.numPages)setPage(n);}}/></label><span>of {doc?.numPages||'…'}</span><Button isDisabled={!doc||page>=doc.numPages} onPress={()=>setPage(page+1)}>Next</Button></div>
    {error&&<p role="alert">{error}</p>}{loading&&<p role="status">Opening original page…</p>}<canvas ref={canvas} style={{visibility:rendered===identity&&!error&&!loading?'visible':'hidden'}} aria-label={'Original PDF page '+page}/>
  </div>;
}

export function Calculated({html}:{html:string}) {
  const policy=`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'">`;
  return <iframe className="calculated-frame" sandbox="" title="Calculated worksheet — CalcpadCE" srcDoc={policy+html}/>;
}
