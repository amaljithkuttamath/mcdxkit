import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {test,expect,vi} from 'vitest';
import {PDFPage} from './viewers';
import {loadPDF} from './pdf';
vi.mock('./pdf',()=>({loadPDF:vi.fn()}));

test('switching source or a failed page never displays stale source pixels',async()=>{
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
  const page={getViewport:()=>({width:450,height:600}),render:()=>({promise:Promise.resolve(),cancel:vi.fn()})};
  const file={numPages:2,getPage:vi.fn().mockResolvedValueOnce(page).mockRejectedValueOnce(new Error('Page unavailable')),destroy:vi.fn()};
  vi.mocked(loadPDF).mockResolvedValueOnce(file).mockRejectedValueOnce(new Error('PDF unavailable'));
  const view=render(<PDFPage url="/source/a" initialPage={1}/>);
  await waitFor(()=>expect(screen.getByLabelText('Original PDF page 1')).toBeVisible());
  await userEvent.click(screen.getByRole('button',{name:'Next'}));
  await screen.findByText('Page unavailable');
  expect(screen.getByLabelText('Original PDF page 2')).not.toBeVisible();
  view.rerender(<PDFPage url="/source/b" initialPage={7}/>);
  expect(screen.getByLabelText('Original PDF page 7')).not.toBeVisible();
  await screen.findByText('PDF unavailable');
  expect(screen.getByLabelText('Original PDF page 7')).not.toBeVisible();
  expect(file.destroy).toHaveBeenCalled();
});
