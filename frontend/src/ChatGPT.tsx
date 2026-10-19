import {useState} from 'react';
import {Button, Dialog, Heading, Modal, ModalOverlay} from 'react-aria-components';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';

export const USAGE_URL='https://chatgpt.com/settings/usage';
export type ChatGPTAccount={id:string;label:string;email?:string|null;signed_in:boolean;plan_usage:boolean;active:boolean;welcome_pending:boolean};
export type ChatGPTStatus={available:boolean;accounts:ChatGPTAccount[];active:string|null;usage_url?:string;last_result?:string|null};
export type ReturnTo={project?:string;view?:string};
type Model={slug:string;display_name:string};

// Tokens never reach the browser: connect returns a one-time server start link. The server
// returns to the validated project/view afterwards, never to a client-supplied URL.
async function connect(returnTo:ReturnTo,account?:string,enablePlan=false){
  const result=await api<{start:string}>('/api/chatgpt/connect',{...(account?{account}:{}),...(enablePlan?{enable_plan:true}:{}),...(returnTo.project?{project:returnTo.project,view:returnTo.view||'ask'}:{})});
  location.assign(result.start);
}

export function ManageUsage(){return <a href={USAGE_URL} target="_blank" rel="noopener noreferrer">Manage usage</a>;}

export function ChatGPTSettings({status,provider,model,pending,perform,returnTo}:{status?:ChatGPTStatus;provider:string;model:string;pending:boolean;perform:(fn:()=>Promise<unknown>)=>void;returnTo:ReturnTo}) {
  const [choice,setChoice]=useState(provider||'openrouter'),[selected,setSelected]=useState(model),[message,setMessage]=useState('');
  const active=status?.accounts.find(a=>a.active);
  // Models load as soon as an account with plan use is active; listing is not proof of inference.
  const models=useQuery({queryKey:['chatgpt-models',active?.id],queryFn:()=>api<{models:Model[]}>('/api/chatgpt/models',{account:active!.id}),enabled:!!active?.plan_usage&&!!status?.available,retry:false,staleTime:300000});
  if(!status)return null;
  if(!status.available)return <section className="chatgpt-card" aria-label="ChatGPT plan"><h3>Use your ChatGPT plan</h3><p>Available when MCDXKit runs on this computer (127.0.0.1). Shared network deployments use OpenRouter.</p></section>;
  const failure=status.last_result&&!['connected','plan_not_enabled'].includes(status.last_result)?status.last_result:'';
  function save(next:{provider:string;chatgpt_model?:string},note:string){perform(async()=>{await api('/api/documents/configure',next);setMessage(note);});}
  return <section className="chatgpt-card" aria-label="ChatGPT plan">
    <h3>Use your ChatGPT plan</h3>
    <p>Complete eligible AI requests in MCDXKit with usage included in your ChatGPT plan or credits balance. When selected, ChatGPT runs every step of an Ask conversation, including extraction it performs. Document search and embeddings stay on your configured RAGFlow service.</p>
    {status.accounts.length>0&&<ul className="chatgpt-accounts">{status.accounts.map(a=><li key={a.id}>
      <div><strong>{a.label}</strong><small>{a.active?'Active · ':''}{!a.signed_in?'Signed out':a.plan_usage?'ChatGPT plan use enabled':"ChatGPT plan use isn't enabled"}</small></div>
      <div className="row-actions">
        {!a.active&&<Button isDisabled={pending} onPress={()=>perform(()=>api('/api/chatgpt/select',{account:a.id}))}>Use this account</Button>}
        {!a.signed_in&&<Button className="chatgpt-button" isDisabled={pending} onPress={()=>perform(()=>connect(returnTo,a.id))}>Continue with ChatGPT</Button>}
        {a.signed_in&&!a.plan_usage&&<Button isDisabled={pending} onPress={()=>perform(()=>connect(returnTo,a.id,true))}>Enable ChatGPT plan use</Button>}
        {a.signed_in&&<Button isDisabled={pending} onPress={()=>perform(async()=>{const r=await api<{message?:string|null}>('/api/chatgpt/disconnect',{account:a.id});setMessage(r.message||'Signed out of ChatGPT on this computer.');})}>Sign out</Button>}
      </div></li>)}</ul>}
    <Button className="chatgpt-button" isDisabled={pending} onPress={()=>perform(()=>connect(returnTo))}>{status.accounts.length?'Add another ChatGPT account':'Continue with ChatGPT'}</Button>
    {failure&&<p role="alert">{failure}</p>}
    <fieldset className="provider-choice"><legend>Ask provider</legend>
      <label><input type="radio" name="ask-provider" checked={choice==='chatgpt'} disabled={!active?.plan_usage||pending} onChange={()=>{setChoice('chatgpt');if(selected)save({provider:'chatgpt',chatgpt_model:selected},'Ask uses your ChatGPT plan.');}}/> ChatGPT plan{active?` · ${active.label}`:''}</label>
      <label><input type="radio" name="ask-provider" checked={choice==='openrouter'} disabled={pending} onChange={()=>{setChoice('openrouter');save({provider:'openrouter'},'Ask uses OpenRouter.');}}/> OpenRouter free model</label>
    </fieldset>
    {choice==='chatgpt'&&active?.plan_usage&&<label>ChatGPT model
      <select value={selected} disabled={pending||models.isLoading} onChange={e=>{const slug=e.target.value;setSelected(slug);if(slug)save({provider:'chatgpt',chatgpt_model:slug},'Ask uses your ChatGPT plan with this model. It is confirmed when a request completes.');}}>
        <option value="">{models.isLoading?'Loading models…':'Choose a model'}</option>
        {model&&!models.data?.models.some(m=>m.slug===model)&&<option value={model}>{model}</option>}
        {models.data?.models.map(m=><option key={m.slug} value={m.slug}>{m.display_name}</option>)}
      </select></label>}
    {models.error&&<p role="alert">{(models.error as Error).message}</p>}
    <p className="muted">{message||<>If your ChatGPT plan can’t complete a request, Ask stops; MCDXKit never switches providers. <ManageUsage/></>}</p>
  </section>;
}

export function ChatGPTWelcome({onDone}:{onDone:()=>void}) {
  return <ModalOverlay isOpen isDismissable={false} isKeyboardDismissDisabled className="modal-backdrop">
    <Modal className="chatgpt-welcome"><Dialog>
      <Heading slot="title">You're using your ChatGPT plan</Heading>
      <p>Eligible AI requests in MCDXKit, including every step of an Ask conversation, use your ChatGPT plan. You can manage usage in ChatGPT settings. <ManageUsage/></p>
      <Button className="primary" autoFocus onPress={onDone}>Got it</Button>
    </Dialog></Modal>
  </ModalOverlay>;
}
