import {useEffect, useMemo, useRef, useState} from 'react';
import {Button, Dialog, DialogTrigger, Popover} from 'react-aria-components';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkParse from 'remark-parse';
import {unified} from 'unified';
import type {Evidence} from './types';

export type SourceHandler=(id:string,page:number,quote:string)=>void;
export type Cited={evidence:Evidence;n:number};
// Numbers follow the answer's evidence order, so the same number appears in "Sources and checks".
export type CitationIndex=Map<string,Cited>;
export const citationIndex=(evidence:Evidence[]):CitationIndex=>new Map(evidence.map((e,i)=>[e.id,{evidence:e,n:i+1}]));
export const pagesOf=(e:Evidence)=>[...new Set(e.positions.map(p=>p[0]).filter(p=>Number.isInteger(p)&&p>0))].sort((a,b)=>a-b);
// Whole-page context opens the page itself; a page of text is not a useful highlighted quote.
export const quoteOf=(e:Evidence)=>e.kind==='page_context'?'':e.text;

type MdNode={type:string;value?:string;url?:string;children?:MdNode[]};
// [E1], [E1, E2], [E1; E2]. Adjacent [E1][E2] is plain text in CommonMark, so each bracket matches alone.
const GROUP=/\[\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]/g;
// Link text that is only citation IDs, e.g. [E1](…) or [[E1, E2]](…); its URL is discarded.
const LINK_ONLY=/^\[?\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]?$/;
const SKIP=new Set(['code','inlineCode','html','image','imageReference','definition','footnoteDefinition']);
const CONTAINERS=new Set(['list','listItem','blockquote','table','tableRow']);

// skipHtml means model text can never produce a span, so data-cite spans only come from this transform.
const citeNode=(id:string,scope:'inline'|'section')=>({type:'citation',children:[{type:'text',value:id}],
  data:{hName:'span',hProperties:{dataCite:id,dataScope:scope}}}) as MdNode;
const plain=(node:MdNode):string=>node.value??(node.children||[]).map(plain).join('');

function citeNodes(list:string,known:CitationIndex,used:Set<string>):MdNode[] {
  return list.split(/[,;]/).map(s=>s.trim()).map(id=>{
    if(!known.has(id))return {type:'text',value:`[${id}]`};
    used.add(id);return citeNode(id,'inline');
  });
}
function split(value:string,known:CitationIndex,used:Set<string>):MdNode[] {
  const out:MdNode[]=[];let last=0;
  for(const match of value.matchAll(GROUP)){
    const at=match.index??0;
    if(at>last)out.push({type:'text',value:value.slice(last,at)});
    out.push(...citeNodes(match[1],known,used));
    last=at+match[0].length;
  }
  if(last<value.length)out.push({type:'text',value:value.slice(last)});
  return out.length?out:[{type:'text',value}];
}
function lastBlock(tree:MdNode):MdNode|null {
  let node=tree.children?.at(-1);
  while(node){
    if(node.type==='paragraph'||node.type==='tableCell')return node;
    if(!CONTAINERS.has(node.type))return null;
    node=node.children?.at(-1);
  }
  return null;
}
// Returns the IDs turned into inline pills; section-level pills are appended for the remaining section IDs.
function transform(tree:MdNode,known:CitationIndex,sectionIds:string[]):Set<string> {
  const used=new Set<string>();
  const walk=(node:MdNode)=>{
    if(!node.children)return;
    node.children=node.children.flatMap(child=>{
      if(SKIP.has(child.type))return [child];
      if(child.type==='text')return split(child.value||'',known,used);
      if(child.type==='link'||child.type==='linkReference'){
        const only=LINK_ONLY.exec(plain(child).trim());
        return only?citeNodes(only[1],known,used):[child];
      }
      walk(child);return [child];
    });
  };
  walk(tree);
  // Section evidence not cited inline is attached to the section's last block, labelled as section-level.
  const rest=[...new Set(sectionIds)].filter(id=>known.has(id)&&!used.has(id));
  if(!rest.length)return used;
  const group={type:'citationGroup',children:rest.map(id=>citeNode(id,'section')),data:{hName:'span',hProperties:{dataSection:'true'}}} as MdNode;
  const target=lastBlock(tree);
  if(target)target.children=[...(target.children||[]),{type:'text',value:' '},group];
  else tree.children=[...(tree.children||[]),{type:'paragraph',children:[group]}];
  return used;
}

// Same parser and plugins as react-markdown in Prose, so counts match the pills actually rendered:
// code, escapes, ordinary links and citation-only links are treated identically.
const parser=unified().use(remarkParse).use(remarkGfm);
export function inlineCitationIds(markdown:string,known:CitationIndex):string[] {
  const tree=parser.runSync(parser.parse(markdown)) as unknown as MdNode;
  return [...transform(tree,known,[])];
}

export function Prose({children,index,sectionIds,onSource}:{children:string;index:CitationIndex;sectionIds?:string[];onSource:SourceHandler}) {
  const ids=(sectionIds||[]).join(' ');
  const plugin=useMemo(()=>()=>(tree:MdNode)=>{transform(tree,index,ids?ids.split(' '):[]);},[index,ids]);
  return <div className="cx-prose"><Markdown skipHtml remarkPlugins={[remarkGfm,plugin]} components={{
    span:props=>{
      const {children}=props,data=props as unknown as Record<string,unknown>,id=data['data-cite'];
      if(typeof id==='string'){const cited=index.get(id);return cited?<CitationPill cited={cited} scope={data['data-scope']==='section'?'section':'inline'} onSource={onSource}/>:<>[{id}]</>;}
      if(data['data-section'])return <span className="cx-section-refs" role="group" aria-label="Section sources">{children}</span>;
      return <span>{children}</span>;
    },
    // Model-supplied links are shown as text; nothing in an answer navigates away.
    a:({children})=><span className="cx-model-link">{children}</span>,
    img:({alt})=><span>{alt}</span>,
  }}>{children}</Markdown></div>;
}

export function CitationPill({cited:{evidence:e,n},scope,onSource}:{cited:Cited;scope:'inline'|'section';onSource:SourceHandler}) {
  const pages=pagesOf(e),quote=quoteOf(e),ref=useRef<HTMLButtonElement>(null);
  const [open,setOpen]=useState(false),[choice,setChoice]=useState<number|null>(null);
  const what=scope==='section'?'Section source':'Source';
  // Open the drawer after the popover closes so focus (and the drawer's return target) is this pill.
  useEffect(()=>{if(!open&&choice!==null){ref.current?.focus();onSource(e.source_id,choice,quote);setChoice(null);}},[open,choice,e.source_id,quote,onSource]);
  const className='cx-pill '+scope;
  if(pages.length<=1){
    const where=pages.length?`PDF page ${pages[0]}`:'page not recorded, opens page 1';
    return <Button ref={ref} className={className} aria-label={`${what} ${n}: ${e.source_name}, ${where}`} onPress={()=>onSource(e.source_id,pages[0]||1,quote)}><span title={`${e.source_name}, ${where}`}>{n}</span></Button>;
  }
  return <DialogTrigger isOpen={open} onOpenChange={setOpen}>
    <Button ref={ref} className={className+' multi'} aria-label={`${what} ${n}: ${e.source_name}, PDF pages ${pages.join(', ')}`}><span title={`${e.source_name}, pages ${pages.join(', ')}`}>{n}</span></Button>
    <Popover className="cx-popover cx-cite-pop" placement="bottom start" offset={6}>
      <Dialog className="cx-dialog" aria-label={`${e.source_name}: choose a page`}>
        <p className="cx-cite-source">{e.source_name}</p>
        {quote&&<blockquote>{quote}</blockquote>}
        <div className="cx-cite-pages" role="group" aria-label="Open PDF page">{pages.map(page=><Button key={page} className="cx-page" onPress={()=>{setChoice(page);setOpen(false);}}>Page {page}</Button>)}</div>
      </Dialog>
    </Popover>
  </DialogTrigger>;
}
