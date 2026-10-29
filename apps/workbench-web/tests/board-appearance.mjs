import { expect } from '@playwright/test';

export async function sharedBoardAppearance(page,label){
 const surface=page.getByLabel(label,{exact:true}).locator('.tempo-board-surface');
 await expect(surface).toHaveClass(/tempo-theme-blue/);await expect(surface).toHaveClass(/tempo-pieces-merida/);
 await expect.poll(()=>surface.evaluate(node=>{
  const css=getComputedStyle(node);const pieces=[...node.querySelectorAll('cg-board piece')];
  return {light:css.getPropertyValue('--tempo-light').trim(),dark:css.getPropertyValue('--tempo-dark').trim(),
   images:pieces.length>0&&pieces.every(piece=>getComputedStyle(piece).backgroundImage!=='none')};
 })).toEqual({light:'#dee3e6',dark:'#6f8fa8',images:true});
}
