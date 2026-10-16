import {createParser} from 'eventsource-parser';
import type {Job} from './types';
let token = '';
export function sessionToken(value:string) {token=value;}
// The local server runs one file or conversion operation at a time and refuses overlap (409).
// Those requests share one lane so quick consecutive picks, such as a report then a template,
// never collide. Job polling and event streams do not take the server lock and stay outside it.
let lane:Promise<unknown>=Promise.resolve();
export function request(path:string, data?:unknown, raw=false,signal?:AbortSignal):Promise<Response> {
  if(path.startsWith('/api/jobs'))return send(path,data,raw,signal);
  const next=lane.then(()=>send(path,data,raw,signal));
  lane=next.catch(()=>undefined);
  return next;
}
async function send(path:string, data:unknown, raw:boolean,signal?:AbortSignal):Promise<Response> {
  const response = await fetch(path, {signal,headers:{'X-MCDXKit-Token':token, ...(data===undefined?{}:{'Content-Type':raw?'application/octet-stream':'application/json'})},
    ...(data===undefined?{}:{method:'POST',body:raw?data as Blob:JSON.stringify(data)})});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || (response.status===401?'Enter the server access token to continue.':'Could not complete this request.'));
  }
  return response;
}
export async function api<T>(path:string, data?:unknown, raw=false):Promise<T> {return (await request(path,data,raw)).json();}
export async function streamConversation(id:string,onEvent:(job:Job)=>void,signal:AbortSignal){
  const response=await request('/api/jobs/'+id+'/events',undefined,false,signal);
  if(!response.body)throw new Error('Live connection unavailable. Saved conversation status will still refresh.');
  const reader=response.body.getReader(),decoder=new TextDecoder();
  const parser=createParser({onEvent(event){if(event.event==='job')onEvent(JSON.parse(event.data) as Job);}});
  try{while(true){const {value,done}=await reader.read();if(done)break;parser.feed(decoder.decode(value,{stream:true}));}}
  finally{await reader.cancel();reader.releaseLock();}
}
export async function download(path:string, name:string) {
  const blob=await (await request(path)).blob(); const url=URL.createObjectURL(blob);
  const link=document.createElement('a'); link.href=url; link.download=name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),10000);
}
