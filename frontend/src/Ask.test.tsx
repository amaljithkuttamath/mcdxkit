import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {test,expect,vi} from 'vitest';
import {Ask} from './Ask';
import type {Project} from './types';

test('human clarification resumes the saved conversation instead of changing inputs',async()=>{
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[{
    id:'a',conversation_id:'a',request:'Help with this file',kind:'workflow',model:'test',review:null,
    content:{sections:[],suggestions:[],gaps:[]},evidence:[],
    clarification:{question:'What do you want to do with the document?',choices:['Find requirements','Calculate with a template']},
  }]};
  const send=vi.fn().mockResolvedValue(undefined),workflow=vi.fn();
  render(<Ask project={project} jobs={[]} onSend={send} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={workflow}/>);
  expect(screen.getByText('What do you want to do with the document?')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Find requirements'}));
  expect(send).toHaveBeenCalledWith('Find requirements','a');
  expect(workflow).not.toHaveBeenCalled();
});

test('live activity shows real progress without exposing a reasoning transcript',async()=>{
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[]};
  render(<Ask project={project} jobs={[{id:'j',action:'ask',state:'running',stage:'Checking citations',uncertain:false,
    request:'What next?',activity:[{time:1,message:'Checking uploaded files'},{time:2,message:'Checking citations'}]}]}
    onSend={vi.fn()} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()}/>);
  expect(screen.getByText('Checking uploaded files')).not.toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Show steps (2)'}));
  expect(screen.getByText('Checking uploaded files')).toBeVisible();
  expect(screen.getByRole('button',{name:'Stop'})).toBeEnabled();
});

test('inline evidence and all cited pages open the original PDF with its quote',async()=>{
  const source=vi.fn();
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[{
    id:'a',request:'Find a limit',model:'test',review:null,
    content:{sections:[{heading:'Wall settlement',body:'The limit is **2 inches** [E1].',evidence_ids:['E1']}],suggestions:[],gaps:[]},
    evidence:[{id:'E1',source_id:'original',source_name:'Requirements.pdf',positions:[[131,0,1,0,1],[132,0,1,0,1]],text:'Original quotation'}],
  }]};
  render(<Ask project={project} jobs={[]} onSend={vi.fn()} onStop={vi.fn()} onSource={source} onSources={vi.fn()} onWorkflow={vi.fn()}/>);
  await userEvent.click(screen.getByRole('button',{name:'Source 1: Requirements.pdf, PDF pages 131, 132'}));
  await userEvent.click(screen.getByRole('button',{name:'Page 131'}));
  expect(source).toHaveBeenLastCalledWith('original',131,'Original quotation');
  await userEvent.click(screen.getByRole('button',{name:'Source 1: Requirements.pdf, PDF pages 131, 132'}));
  await userEvent.click(screen.getByRole('button',{name:'Page 132'}));
  expect(source).toHaveBeenLastCalledWith('original',132,'Original quotation');
});

test('a proposed extraction stays unsaved until the user clicks Save for review',async()=>{
  const save=vi.fn();
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[{
    id:'a',request:'What is the limit?',model:'test',review:null,
    content:{sections:[],suggestions:[],gaps:[]},evidence:[],
    extraction_offer:{source_id:'source',source_name:'Requirements.pdf',pages:[8],question:'Save limits'},
  }]};
  render(<Ask project={project} jobs={[]} onSend={vi.fn()} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()} onSaveOffer={save}/>);
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByText('Proposal, not saved')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Save for review'}));
  expect(save).toHaveBeenCalledWith('a');
});

test('New chat keeps the previous conversation and submits without its parent',async()=>{
  const send=vi.fn().mockResolvedValue(undefined);
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[{
    id:'old',conversation_id:'old',request:'Old question',model:'test',review:null,
    content:{sections:[{heading:'Old answer',body:'Previous result',evidence_ids:[]}],suggestions:[],gaps:[]},evidence:[],
  }]};
  render(<Ask project={project} jobs={[{id:'old-job',action:'ask',state:'cancelled',stage:'Stopped',uncertain:false,request:'Old question'}]}
    onSend={send} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()}/>);
  await userEvent.click(screen.getByRole('button',{name:'New chat'}));
  expect(screen.queryByText(/Response stopped/)).not.toBeInTheDocument();
  expect(screen.queryByText('Previous result')).not.toBeInTheDocument();
  await userEvent.type(screen.getByRole('textbox',{name:'Ask a question'}),'Fresh question');
  await userEvent.click(screen.getByRole('button',{name:'Send question'}));
  expect(send).toHaveBeenCalledWith('Fresh question',null);
  expect(project.answers).toHaveLength(1);
});

test('New chat during a first answer opens an empty draft and keeps the running answer reachable',async()=>{
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[]};
  render(<Ask project={project} jobs={[{id:'running',action:'ask',state:'running',stage:'Searching',uncertain:false,request:'In-flight question',parent:null}]}
    onSend={vi.fn()} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()}/>);
  await userEvent.click(screen.getByRole('button',{name:'New chat'}));
  expect(screen.queryByText('In-flight question')).not.toBeInTheDocument();
  expect(screen.getByText('An answer is running in another conversation.')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Show it'}));
  expect(screen.getByText('In-flight question')).toBeVisible();
});

test('legacy answers with a literal mismatch do not present a supported badge',()=>{
  const project:Project={id:'p',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[{
    id:'legacy',request:'Wall limit?',model:'test',review:null,
    content:{sections:[{heading:'Limit',body:'7 inches [E1].',evidence_ids:['E1']}],suggestions:[],gaps:[]},
    evidence:[{id:'E1',source_id:'s',source_name:'Requirements.pdf',text:'2 inches',positions:[[1,0,0,0,0]]}],
    assessment:{status:'supported',explanation:'Supported',conflicts:[]},
    audit:{status:'warnings',cited:['E1'],unsupported_numbers:[{heading:'Limit',values:['7']}],meaning:'Literal check only'},
  }]};
  render(<Ask project={project} jobs={[]} onSend={vi.fn()} onStop={vi.fn()} onSource={vi.fn()} onSources={vi.fn()} onWorkflow={vi.fn()}/>);
  expect(screen.queryByText('Evidence found')).not.toBeInTheDocument();
  expect(screen.getByText(/was not found word-for-word in the cited text/)).toBeVisible();
});
