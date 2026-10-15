import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {test,expect,vi,beforeEach} from 'vitest';
import {Ask} from './Ask';
import {download} from './api';
import type {Project,Draft} from './types';

vi.mock('./api',()=>({download:vi.fn()}));
vi.mock('./ModelPicker',()=>({ModelPicker:()=>null,ModelUsed:({id}:{id:string})=><span>{id}</span>,Glyph:()=>null}));
beforeEach(()=>{vi.mocked(download).mockReset();});
function record():Draft{return {
  id:'answer',request:'Wall limit?',model:'vendor/model:free',created:100,review:null,kind:'research',
  content:{sections:[{heading:'Limit',body:'2 inches [E1].',evidence_ids:['E1']}],suggestions:[],gaps:[]},
  evidence:[{id:'E1',source_id:'source',source_name:'Requirements.pdf',positions:[[7,0,0,0,0]],text:'For new walls, settlement shall not exceed 2 inches.'}],
  assessment:{status:'supported',explanation:'Checked within scope',conflicts:[]},
  grounding:{version:'grounding/2',status:'grounded',statements:[{id:'S1',kind:'section',section:0,
    verdict:'supported',reasons:[],text:'2 inches [E1].',evidence_ids:['E1'],
    quotes:[{evidence_id:'E1',text:'settlement shall not exceed 2 inches'}]}]},
};}
function setup(answer=record()){
  const source=vi.fn(),project:Project={id:'project',title:'Project',revision:1,sources:[],drafts:[],runs:[],calculations:[],answers:[answer]};
  render(<Ask project={project} jobs={[]} onSend={vi.fn()} onStop={vi.fn()} onSource={source} onSources={vi.fn()} onWorkflow={vi.fn()}/>);
  return source;
}

test('statement evidence opens the original page at the exact quoted passage',async()=>{
  const source=setup();
  expect(screen.getByText('Model-checked')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:/Sources and checks/}));
  await userEvent.click(screen.getByRole('button',{name:'Open Requirements.pdf, page 7, at the quote for S1'}));
  expect(source).toHaveBeenLastCalledWith('source',7,'settlement shall not exceed 2 inches');
});

test('audit download uses the authenticated helper and selected answer only',async()=>{
  vi.mocked(download).mockResolvedValue(undefined);
  setup();
  await userEvent.click(screen.getByRole('button',{name:'Download audit record'}));
  await waitFor(()=>expect(download).toHaveBeenCalledWith('/api/documents/project/answers/answer/audit','mcdxkit-answer-answer-audit.json'));
  expect(await screen.findByRole('status')).toHaveTextContent('Downloaded');
});

test('audit download failure is visible and permits a deliberate retry',async()=>{
  vi.mocked(download).mockRejectedValue(new Error('Unavailable'));
  setup();
  await userEvent.click(screen.getByRole('button',{name:'Download audit record'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Audit record not downloaded');
  expect(screen.getByRole('button',{name:'Download audit record'})).toBeEnabled();
  expect(download).toHaveBeenCalledTimes(1);
});

test('withheld answer keeps evidence collapsed without inventing draft text',async()=>{
  const answer=record();
  answer.content.sections=[];
  answer.grounding={version:'grounding/2',status:'withheld',draft_withheld:true,statements:[{id:'S1',kind:'section',section:0,verdict:'unsupported',reasons:['Wrong unit']}]};
  answer.assessment={status:'insufficient',explanation:'The draft failed a source check.',conflicts:[]};
  setup(answer);
  expect(screen.getByText('Draft withheld')).toBeVisible();
  expect(screen.getAllByText('The draft failed a source check.').some(node=>!node.closest('[hidden]'))).toBe(true);
  expect(screen.getByRole('button',{name:'Requirements.pdf · p. 7'})).not.toBeVisible();
  await userEvent.click(screen.getByText('Evidence',{selector:'summary span'}));
  expect(screen.getByRole('button',{name:'Requirements.pdf · p. 7'})).toBeVisible();
});

test('identical passage text is grouped while both original files stay reachable',async()=>{
  const answer=record();
  answer.evidence.push({...answer.evidence[0],id:'E2',source_id:'copy',source_name:'Requirements copy.pdf',text:'For new walls,  settlement shall not exceed 2 inches.'});
  const source=setup(answer);
  await userEvent.click(screen.getByText('Evidence',{selector:'summary span'}));
  expect(screen.getByText(/Same passage in 2 files/)).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Requirements copy.pdf · p. 7 (E2)'}));
  expect(source).toHaveBeenLastCalledWith('copy',7,'For new walls,  settlement shall not exceed 2 inches.');
});

test('page context reports truncation and does not claim full document reading',async()=>{
  const answer=record();
  answer.evidence.push({id:'E2',source_id:'source',source_name:'Requirements.pdf',positions:[[8,0,0,0,0]],
    text:'An incomplete page prefix.',kind:'page_context',truncated:true,page_characters:12000});
  answer.context_reads=[{source_name:'Requirements.pdf',anchor_pages:[7],pages:[7,8,9],truncated_pages:[8],omitted_pages:[9]}];
  setup(answer);
  await userEvent.click(screen.getByText('Evidence',{selector:'summary span'}));
  expect(screen.getByText(/1 original page inspected/)).toHaveTextContent('1 only in part');
  expect(screen.queryByText('Read in full')).not.toBeInTheDocument();
});

test('inline citation opens its original page while the single evidence disclosure is collapsed',async()=>{
  const source=setup();
  const summary=screen.getByText('Evidence',{selector:'summary span'}).closest('summary')!;
  const disclosure=summary.closest('details')!;
  expect(disclosure.open).toBe(false);
  expect(summary).toHaveTextContent('1 record from 1 file');
  expect(screen.queryByRole('button',{name:'Requirements.pdf · p. 7 (E1)'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:/Source 1.*Requirements.pdf/}));
  expect(source).toHaveBeenLastCalledWith('source',7,expect.stringContaining('settlement'));
  expect(disclosure.open).toBe(false);
  await userEvent.click(summary);
  expect(disclosure.open).toBe(true);
  await userEvent.click(summary);
  expect(disclosure.open).toBe(false);
});
