import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {test,expect,vi,beforeEach} from 'vitest';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {ModelPicker} from './ModelPicker';
import {Ask} from './Ask';
import {api} from './api';
import type {Project} from './types';
vi.mock('./api',()=>({api:vi.fn()}));
const config={provider:'openrouter',model:'vendor/first:free',router_key_saved:true};
beforeEach(()=>{vi.mocked(api).mockReset();});
function wrapper(){const c=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});c.setQueryData(['libraries'],{projects:[],configuration:config});return c;}

test('models load on opening the picker and selection changes the current provider model',async()=>{
  const c=wrapper();
  vi.mocked(api).mockImplementation(async(path)=>path==='/api/documents/models'?{models:[{id:'vendor/first:free',name:'First'},{id:'vendor/second:free',name:'Second'}]}:path==='/api/documents'?{projects:[],configuration:{...config,model:'vendor/second:free'}}:{});
  render(<QueryClientProvider client={c}><ModelPicker disabled={false}/></QueryClientProvider>);
  expect(api).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button',{name:/Answer model: OpenRouter, First/}));
  await userEvent.click(await screen.findByRole('option',{name:/Second/}));
  await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/documents/configure',{provider:'openrouter',model:'vendor/second:free'}));
});

test('model changes preserve the unsent question and current conversation',async()=>{
  const c=wrapper();
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[{id:'a',request:'Original question',model:'vendor/first:free',review:null,content:{sections:[{heading:'Result',body:'Original answer',evidence_ids:[]}],suggestions:[],gaps:[]},evidence:[]}]};
  vi.mocked(api).mockImplementation(async(path)=>path==='/api/documents/models'?{models:[{id:'vendor/second:free',name:'Second'}]}:path==='/api/documents'?{projects:[project],configuration:{...config,model:'vendor/second:free'}}:{});
  render(<QueryClientProvider client={c}><Ask project={project} jobs={[]} onSend={vi.fn()} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()}/></QueryClientProvider>);
  await userEvent.type(screen.getByRole('textbox',{name:'Ask a question'}),'My unfinished follow-up');
  await userEvent.click(screen.getByRole('button',{name:/Answer model:/}));
  await userEvent.click(await screen.findByRole('option',{name:/Second/}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('textbox',{name:'Ask a question'})).toHaveValue('My unfinished follow-up');
  expect(screen.getByText('Original answer')).toBeVisible();
});

test('a running conversation disables model switching',()=>{
  const c=wrapper();
  render(<QueryClientProvider client={c}><ModelPicker disabled/></QueryClientProvider>);
  expect(screen.getByRole('button',{name:/Answer model:/})).toBeDisabled();
  expect(api).not.toHaveBeenCalled();
});

test('starting an answer while the model menu is open blocks selection',async()=>{
  const c=wrapper();
  vi.mocked(api).mockImplementation(async(path)=>path==='/api/documents/models'?{models:[{id:'vendor/second:free',name:'Second'}]}:{});
  const {rerender}=render(<QueryClientProvider client={c}><ModelPicker disabled={false}/></QueryClientProvider>);
  await userEvent.click(screen.getByRole('button',{name:/Answer model:/}));
  await screen.findByRole('option',{name:/Second/});
  rerender(<QueryClientProvider client={c}><ModelPicker disabled/></QueryClientProvider>);
  const option=screen.queryByRole('option',{name:/Second/});
  if(option)await userEvent.click(option);
  expect(vi.mocked(api).mock.calls.filter(([path])=>path==='/api/documents/configure')).toEqual([]);
});

test('a pending model change prevents sending with the old model',async()=>{
  const c=wrapper(),send=vi.fn();
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[]};
  vi.mocked(api).mockImplementation(async(path)=>path==='/api/documents/models'?{models:[{id:'vendor/second:free',name:'Second'}]}:path==='/api/documents/configure'?new Promise(()=>{}):{projects:[],configuration:config});
  render(<QueryClientProvider client={c}><Ask project={project} jobs={[]} onSend={send} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()}/></QueryClientProvider>);
  await userEvent.type(screen.getByRole('textbox',{name:'Ask a question'}),'Keep my draft');
  await userEvent.click(screen.getByRole('button',{name:/Answer model:/}));
  await userEvent.click(await screen.findByRole('option',{name:/Second/}));
  expect(screen.getByRole('button',{name:'Send question',hidden:true})).toBeDisabled();
  expect(send).not.toHaveBeenCalled();
});
