import {render,screen,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {test,expect,vi} from 'vitest';
import {Prose,citationIndex,inlineCitationIds} from './citations';
import type {Evidence} from './types';

const evidence:Evidence[]=[
  {id:'E1',source_id:'original-one',source_name:'Requirements.pdf',positions:[[132,0,1,0,1]],text:'The limit is 2 inches.'},
  {id:'E2',source_id:'original-two',source_name:'Conditions.pdf',positions:[[7,0,1,0,1],[8,0,1,0,1]],text:'Applies only to new walls.'},
];

test('grouped inline citations open the matching original PDF, not model links',async()=>{
  const source=vi.fn();
  render(<Prose index={citationIndex(evidence)} onSource={source}>The limit is 2 inches [E1, E2]; see [E1](https://untrusted.example).</Prose>);
  const first=screen.getAllByRole('button',{name:'Source 1: Requirements.pdf, PDF page 132'});
  expect(first).toHaveLength(2);
  await userEvent.click(first[0]);
  expect(source).toHaveBeenLastCalledWith('original-one',132,'The limit is 2 inches.');
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

test('multi-page evidence makes every original page available',async()=>{
  const source=vi.fn();
  render(<Prose index={citationIndex(evidence)} onSource={source}>Conditions [E2].</Prose>);
  await userEvent.click(screen.getByRole('button',{name:'Source 2: Conditions.pdf, PDF pages 7, 8'}));
  const dialog=await screen.findByRole('dialog',{name:'Conditions.pdf: choose a page'});
  await userEvent.click(within(dialog).getByRole('button',{name:'Page 8'}));
  expect(source).toHaveBeenLastCalledWith('original-two',8,'Applies only to new walls.');
});

test('unknown ids, code and model-supplied HTML never create clickable evidence',()=>{
  render(<Prose index={citationIndex(evidence)} onSource={vi.fn()}>{'Unknown [E99]. `sample [E1]`. <span data-cite="E1">spoof</span>'}</Prose>);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.getByText(/Unknown/)).toHaveTextContent('[E99]');
});

test('legacy section citations are labeled as section evidence and remain inline',async()=>{
  const source=vi.fn();
  const {container}=render(<Prose index={citationIndex(evidence)} sectionIds={['E1']} onSource={source}>Legacy answer has a limit.</Prose>);
  const group=screen.getByRole('group',{name:'Section sources'});
  expect(group.closest('p')).toHaveTextContent('Legacy answer has a limit.');
  await userEvent.click(within(group).getByRole('button',{name:'Section source 1: Requirements.pdf, PDF page 132'}));
  expect(source).toHaveBeenLastCalledWith('original-one',132,evidence[0].text);
  expect(container.textContent).not.toContain('Requirements.pdf');
});

test('whole-page context does not pretend a full page is an exact highlighted quote',async()=>{
  const source=vi.fn();
  render(<Prose index={citationIndex([{...evidence[0],kind:'page_context'}])} onSource={source}>Value [E1].</Prose>);
  await userEvent.click(screen.getByRole('button',{name:'Source 1: Requirements.pdf, PDF page 132'}));
  expect(source).toHaveBeenLastCalledWith('original-one',132,'');
});

test.each([
  ['Ordinary [see [E1]](https://example.test)', []],
  ['    code [E1]', []],
  ['```text\n[E1]\n```', []],
  ['`[E1]` and [E2]', ['E2']],
  ['[E1](https://example.test)', ['E1']],
  ['[E1, E2]', ['E1','E2']],
  ['Escaped \\[E1\\]', ['E1']],
  ['Unknown [E99]', []],
])('citation counts agree with rendered Markdown: %s',(markdown,expected)=>{
  const index=citationIndex(evidence);
  expect(inlineCitationIds(markdown,index)).toEqual(expected);
  render(<Prose index={index} onSource={vi.fn()}>{markdown}</Prose>);
  expect(screen.queryAllByRole('button')).toHaveLength(expected.length);
});
