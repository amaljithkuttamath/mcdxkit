import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect, test, vi} from 'vitest';
import {JobStatus, RequirementRow} from './components';

test('a running job is cancellable and does not invent a percentage', async () => {
  let cancelled = false;
  render(<JobStatus job={{id:'one', action:'extract', state:'running', stage:'Reading selected pages', uncertain:false}} onCancel={() => {cancelled=true;}} />);
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
  expect(screen.getByText('Reading selected pages')).toBeVisible();
  await userEvent.click(screen.getByRole('button', {name:'Cancel'}));
  expect(cancelled).toBe(true);
});

test('uncertain interruptions explain the risk without a blind retry action', () => {
  render(<JobStatus job={{id:'one', action:'extract', state:'failed', stage:'Interrupted', error:'Worker stopped.', uncertain:true}} onCancel={() => {}} />);
  expect(screen.getByRole('alert')).toHaveTextContent('may have completed');
  expect(screen.queryByRole('button', {name:/retry/i})).toBeNull();
});

test('requirement keeps its qualifications and opens the exact original page', async () => {
  const open = vi.fn();
  const fact = {id:'r1', subject:'New walls', property:'Settlement', value_text:'2 inches', unit:'in', review:null,
    evidence:[{page:131,quote:'2 inches for new walls'}], conditions:[{page:132,quote:'Both short- and long-term.',text:'Calculate short-term and long-term settlement.'}]};
  render(<RequirementRow fact={fact} source={{id:'s1',name:'Specification.pdf'}} selected={false} onSelect={() => {}} onSource={open} onReview={() => {}} />);
  expect(screen.getByText('Calculate short-term and long-term settlement.')).toBeVisible();
  expect(screen.getByText('Needs review')).toBeVisible();
  await userEvent.click(screen.getByRole('button', {name:'Condition · p. 132'}));
  expect(open).toHaveBeenCalledWith('s1',132,'Both short- and long-term.');
});
