"""Read-only browser check for a cached three-declaration smoke subset.

Start a real subset service, then pass its URL and an artifact label.
No model generation is configured or requested by this script.
"""
import argparse, asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
async def main(url,label,instance_title=None):
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':1500,'height':1000})
  errors=[];page.on('pageerror',lambda e:errors.append(e.stack))
  await page.goto(url);await page.wait_for_function("document.querySelector('#document').getAttribute('aria-busy')==='false'")
  if instance_title:
   await page.locator('#instances').select_option(label=instance_title)
   await page.wait_for_function("title=>!state.busy && document.querySelector('#title').textContent===title",arg=instance_title)
  assert 'Subset · 3 declarations' in await page.locator('#scope-label').inner_text()
  assert await page.evaluate("()=>{let box=document.createElement('div');renderSourceRows(box,[{ref:{local_id:'x'},part:'statement',text:''},{ref:{local_id:'x'},part:'statement',text:'a'}]);return box.querySelector('pre').textContent==='\\na';}")
  initial=await page.locator('#document .prose').all_text_contents()
  expansion_count=0
  while await page.locator('#document [data-action="expand"]').count():
   await page.locator('#document [data-action="expand"]').first.click();await page.wait_for_function('!state.busy');expansion_count+=1
   if expansion_count>15:raise AssertionError('Unexpectedly large subset')
  assert await page.locator('#document .segment.statement').count()>=1
  assert await page.locator('#document .segment.content').count()>=1
  assert not await page.locator('#notice.error:not([hidden])').count()
  nodes=await page.evaluate('state.nodes')
  assert await page.locator('.dependency-link').count()==await page.evaluate('state.edges.length')
  assert len([n for n in nodes if n['kind']=='unit'])==3
  assert all(n['parent'] is None for n in nodes if n['kind']=='external')
  if await page.locator('#external summary').count():
   await page.locator('#external summary').click();await page.locator('#external button').first.click()
   await page.wait_for_function("document.querySelector('#detail-title').textContent!==''")
   await page.locator('[data-detail="members"]').click();await page.wait_for_function("document.querySelector('#detail-content').children.length>0")
  await page.evaluate("window.scrollTo({top:0,behavior:'instant'});document.querySelector('.map-panel').scrollTop=0");await page.wait_for_timeout(1600)
  await page.screenshot(path=f'data/research/reader_delivery/web/{label}.png',full_page=True)
  await page.set_viewport_size({'width':390,'height':844})
  assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
  await page.locator('#reset').click();await page.wait_for_function('!state.busy')
  assert initial==await page.locator('#document .prose').all_text_contents()
  assert not errors,errors
  result={'label':label,'subset_declarations':3,'expanded_sections':expansion_count,'external_repo_groups':len([n for n in nodes if n['kind']=='external']),'exact_collapse_restoration':True,'page_errors':errors}
  Path(f'data/research/reader_delivery/web/{label}-report.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
  await browser.close()
parser=argparse.ArgumentParser();parser.add_argument('url');parser.add_argument('label');parser.add_argument('--instance-title');args=parser.parse_args();asyncio.run(main(args.url,args.label,args.instance_title))
