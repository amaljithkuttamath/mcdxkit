import {test,expect,vi} from 'vitest';
import {api,sessionToken,streamConversation} from './api';

test('conversation events survive fragmented network frames and use session authentication',async()=>{
  const encoder=new TextEncoder(),events:unknown[]=[];
  const fetcher=vi.fn(async(_url:string,_options?:RequestInit)=>new Response(new ReadableStream({start(controller){
    for(const part of ['event: job\ndata: {"id":"one","sta','te":"running"}\n\n',': keep-alive\n\nevent: job\ndata: {"id":"one","state":"succeeded"}\n\n'])controller.enqueue(encoder.encode(part));
    controller.close();
  }}),{headers:{'Content-Type':'text/event-stream'}}));
  vi.stubGlobal('fetch',fetcher);sessionToken('synthetic');
  await streamConversation('one',j=>events.push(j),new AbortController().signal);
  expect(events).toEqual([{id:'one',state:'running'},{id:'one',state:'succeeded'}]);
  expect(fetcher.mock.calls[0]?.[1]).toMatchObject({headers:{'X-MCDXKit-Token':'synthetic'}});
  vi.unstubAllGlobals();
});

test('lock-guarded requests never overlap, survive a failure, and job polling bypasses the lane',async()=>{
  let active=0,peak=0;const order:string[]=[];const release:(()=>void)[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    order.push(url);
    if(url.startsWith('/api/jobs'))return new Response('[]');
    active++;peak=Math.max(peak,active);
    await new Promise<void>(resolve=>release.push(resolve));
    active--;
    return url.includes('fail')?new Response(JSON.stringify({error:'Synthetic refusal.'}),{status:400}):new Response('{}');
  }));
  const report=api('/api/upload?kind=report',new Blob(['r']),true);
  const failing=api('/api/upload?kind=fail',new Blob(['x']),true);
  const template=api('/api/upload?kind=template',new Blob(['t']),true);
  await vi.waitFor(()=>expect(release).toHaveLength(1));
  await api('/api/jobs?project=p');     // answered while the report upload is still in flight
  expect(order).toEqual(['/api/upload?kind=report','/api/jobs?project=p']);
  release.shift()!();await report;
  await vi.waitFor(()=>expect(release).toHaveLength(1));
  release.shift()!();await expect(failing).rejects.toThrow('Synthetic refusal.');
  await vi.waitFor(()=>expect(release).toHaveLength(1));
  release.shift()!();await template;
  expect(peak).toBe(1);
  expect(order.filter(u=>u.startsWith('/api/upload'))).toEqual(['/api/upload?kind=report','/api/upload?kind=fail','/api/upload?kind=template']);
  vi.unstubAllGlobals();
});
