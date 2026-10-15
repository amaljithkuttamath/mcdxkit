import {render, screen} from '@testing-library/react';
import {expect, test} from 'vitest';
import {RequirementRow} from './components';

test('a literal number-word requirement also exposes its normalized engineering unit', () => {
  render(<RequirementRow fact={{id:'r',subject:'New walls',property:'Settlement',value_text:'two (2)',unit:'in',
    review:null,evidence:[{page:5,quote:'Maximum two (2) inches.'}],conditions:[]}}
    source={{id:'s',name:'Specifications.pdf'}} selected={false} onSelect={()=>{}} onSource={()=>{}} onReview={()=>{}} />);
  expect(screen.getByRole('article')).toHaveTextContent('two (2)');
  expect(screen.getByText('in', {exact:true})).toBeVisible();
  expect(screen.getByText('Needs review')).toBeVisible();
});
