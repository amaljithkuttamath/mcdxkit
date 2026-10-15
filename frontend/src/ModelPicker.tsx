import {useContext, useEffect, useRef, useState} from 'react';
import {Button, Dialog, DialogTrigger, Heading, ListBox, ListBoxItem, Popover} from 'react-aria-components';
import type {Selection} from 'react-aria-components';
import {QueryClientContext, useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {api} from './api';
import {ManageUsage} from './ChatGPT';
import type {ChatGPTStatus} from './ChatGPT';
import type {Project} from './types';

type Configuration={model?:string;provider?:string;chatgpt_model?:string;router_key_saved?:boolean};
type Option={id:string;label:string;note?:string};

const GLYPHS={chevron:'m7 10 5 5 5-5',check:'m5 12.5 4.5 4.5L19 7.5',plus:'M12 5v14 M5 12h14',up:'M12 19V5 M6 11l6-6 6 6',history:'M4 12a8 8 0 1 0 2.3-5.6 M4 4v4h4 M12 8v4l3 2',stop:'M8 8h8v8H8z'};
export function Glyph({name,size=16}:{name:keyof typeof GLYPHS;size?:number}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill={name==='stop'?'currentColor':'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={GLYPHS[name]}/></svg>;
}

// Readable label from a model ID; the full ID stays in the title and in "Sources and checks".
export function friendlyModel(id:string) {
  const raw=(id.startsWith('chatgpt:')?id.slice(8):id).split('/').pop()!.replace(/:free$/,'');
  if(!raw)return id||'Unknown model';
  return raw.split(/[-_]+/).filter(Boolean).map(w=>/^gpt$/i.test(w)?'GPT':/^[a-z]{2,}$/.test(w)?w[0].toUpperCase()+w.slice(1):w).join(' ');
}
export function ModelUsed({id}:{id:string}) {
  if(!id)return null;
  const provider=id.startsWith('chatgpt:')?'ChatGPT':id.includes('/')?'OpenRouter':'';
  return <span className="cx-model-used" title={id}><span className="cx-sr">Answered with </span>{provider&&<span className="cx-sr">{provider} </span>}{friendlyModel(id)}</span>;
}

type PickerProps={disabled:boolean;onServices?:()=>void;onPendingChange?:(pending:boolean)=>void};
// Needs the app's QueryClient. Without one (isolated component tests), the picker is omitted.
export function ModelPicker(props:PickerProps) {return useContext(QueryClientContext)?<Picker {...props}/>:null;}

function Picker({disabled,onServices,onPendingChange}:PickerProps) {
  const client=useQueryClient(),[open,setOpen]=useState(false);
  const pendingChange=useRef(onPendingChange);pendingChange.current=onPendingChange;
  // An answer starting while the menu is open closes it; selection is also guarded below.
  useEffect(()=>{if(disabled)setOpen(false);},[disabled]);
  // Same key and request as App, so this reads the existing configuration cache.
  const libraries=useQuery({queryKey:['libraries'],queryFn:()=>api<{projects:Project[];configuration:Configuration}>('/api/documents'),staleTime:Infinity});
  const config=libraries.data?.configuration;
  const provider=config?.provider==='chatgpt'?'chatgpt':'openrouter',providerName=provider==='chatgpt'?'ChatGPT':'OpenRouter';
  const chatgpt=useQuery({queryKey:['chatgpt'],queryFn:()=>api<ChatGPTStatus>('/api/chatgpt'),enabled:!!config&&provider==='chatgpt',staleTime:Infinity});
  const account=chatgpt.data?.accounts.find(a=>a.active);
  const current=(provider==='chatgpt'?config?.chatgpt_model:config?.model)||'';
  const blocker=!config?'':provider==='openrouter'?(config.router_key_saved?'':'Add an OpenRouter key in Services to list free models.')
    :!chatgpt.data?'':!chatgpt.data.available?'ChatGPT sign-in works only when MCDXKit runs on this computer.'
    :!account?.signed_in?'Sign in to ChatGPT in Services to list models for your account.'
    :!account.plan_usage?'Enable ChatGPT plan use in Services to list models.':'';
  const ready=!!config&&!blocker&&(provider==='openrouter'||!!account);
  // The catalog is requested only once the person opens the picker; no retries, no fallback provider.
  const catalog=useQuery({queryKey:['ask-models',provider,provider==='chatgpt'?account?.id||'':'saved-key'],enabled:open&&ready,retry:false,staleTime:5*60_000,
    queryFn:async():Promise<Option[]>=>provider==='chatgpt'
      ?(await api<{models:{slug:string;display_name:string}[]}>('/api/chatgpt/models',{account:account!.id})).models.map(m=>({id:m.slug,label:m.display_name||friendlyModel(m.slug)}))
      :(await api<{models:{id:string;name:string;recommendation?:string}[]}>('/api/documents/models',{router_key:''})).models.map(m=>({id:m.id,label:m.name.replace(/\s*\(free\)\s*$/i,'')||friendlyModel(m.id),note:m.recommendation}))});
  const choose=useMutation({
    mutationFn:(id:string)=>api('/api/documents/configure',provider==='chatgpt'?{provider:'chatgpt',chatgpt_model:id}:{provider:'openrouter',model:id}),
    onSuccess:async()=>{await client.invalidateQueries({queryKey:['libraries']});setOpen(false);}});
  // Sending waits until the saved model and the refreshed configuration agree; unmount releases the hold.
  useEffect(()=>{pendingChange.current?.(choose.isPending);},[choose.isPending]);
  useEffect(()=>()=>pendingChange.current?.(false),[]);
  const options=catalog.data||[];
  const listed=options.find(o=>o.id===current);
  const label=listed?.label||(current?friendlyModel(current):'Choose a model');
  const items=current&&catalog.data&&!listed?[{id:current,label,note:'Current model, not in this list'},...options]:options;
  function select(keys:Selection){
    if(keys==='all'||disabled||choose.isPending)return;
    const id=[...keys][0];
    if(typeof id==='string'&&id!==current)choose.mutate(id);
  }
  const services=onServices
    ?<Button className="cx-link" onPress={()=>{setOpen(false);onServices();}}>Open Services</Button>
    :<span>Open Services in the sidebar.</span>;
  const triggerLabel=libraries.isError?'Answer model unavailable':!config?'Loading answer model':`Answer model: ${providerName}, ${label}${disabled?'. Available after the current answer finishes':''}`;
  return <DialogTrigger isOpen={open&&!disabled} onOpenChange={next=>{if(next&&disabled)return;setOpen(next);if(next&&!choose.isPending)choose.reset();}}>
    <Button className="cx-model-trigger" isDisabled={disabled||!config} aria-label={triggerLabel}>
      <span className="cx-model-provider">{config?providerName:'Model'}</span>
      <span className="cx-model-name" title={current||undefined}>{choose.isPending?'Switching…':config?label:libraries.isError?'Unavailable':'Loading…'}</span>
      <Glyph name="chevron" size={14}/>
    </Button>
    <Popover className="cx-popover cx-model-pop" placement="top start" offset={8}>
      <Dialog className="cx-dialog">
        <Heading slot="title" className="cx-pop-heading">Answer model</Heading>
        <p className="cx-pop-sub">{provider==='chatgpt'?`Models listed for your ChatGPT account${account?.label?` (${account.label})`:''}. A model is confirmed only when an answer completes.`:'Free OpenRouter models available to the saved key, plus pinned paid models billed to it.'}</p>
        {blocker?<div className="cx-pop-state"><p>{blocker}</p>{onServices&&<Button className="cx-secondary" onPress={()=>{setOpen(false);onServices();}}>Open Services</Button>}</div>
          :provider==='chatgpt'&&chatgpt.isPending?<p className="cx-pop-state" role="status">Checking your ChatGPT account…</p>
          :chatgpt.isError?<div className="cx-pop-state" role="alert"><p>{chatgpt.error.message}</p><Button className="cx-secondary" onPress={()=>void chatgpt.refetch()}>Try again</Button></div>
          :catalog.isPending?<p className="cx-pop-state" role="status">Loading models…</p>
          :catalog.isError?<div className="cx-pop-state" role="alert"><p>Models couldn’t be listed. {catalog.error.message}</p><Button className="cx-secondary" onPress={()=>void catalog.refetch()}>Try again</Button></div>
          :!items.length?<div className="cx-pop-state"><p>No supported models are available{provider==='chatgpt'?' for this account':' for this key'}.</p></div>
          :<ListBox aria-label={`${providerName} models`} className="cx-option-list" items={items} selectionMode="single" disallowEmptySelection
              selectedKeys={current?[current]:[]} disabledKeys={choose.isPending||disabled?items.map(o=>o.id):[]} onSelectionChange={select}>
            {o=><ListBoxItem id={o.id} textValue={o.label} className="cx-option">{({isSelected})=><>
              <span className="cx-option-name">{o.label}</span>{isSelected&&<Glyph name="check"/>}
              <small>{o.id}</small>{o.note&&<small>{o.note}</small>}</>}</ListBoxItem>}
          </ListBox>}
        {choose.isError&&<p className="cx-pop-state" role="alert">The model wasn’t changed. {choose.error.message}</p>}
        <div className="cx-pop-foot"><span>Provider and accounts are set in Services.</span>{services}{provider==='chatgpt'&&<ManageUsage/>}</div>
      </Dialog>
    </Popover>
  </DialogTrigger>;
}
