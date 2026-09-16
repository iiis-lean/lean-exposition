"""Focused real-browser reader checks. Start the demo server first.

PYTHONPATH=src python tests/browser/test_reader.py --url http://127.0.0.1:8766
Requires Playwright and a locally installed Chromium browser.
"""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def run(url, output):
 output.mkdir(parents=True,exist_ok=True)
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':1440,'height':1000})
  errors=[];page.on('pageerror',lambda error:errors.append(error.stack))
  # Force genuine server pagination across text lines and visible graph nodes.
  async def paginate(route):
   data=route.request.post_data_json
   data['limit']=2
   await route.continue_(post_data=json.dumps(data))
  await page.route('**/api/read_text',paginate)
  await page.route('**/api/get_overview',paginate)
  await page.goto(url)
  await page.wait_for_function("document.querySelector('#document').getAttribute('aria-busy') === 'false'")
  initial=await page.locator('#document .prose').all_text_contents()
  await page.evaluate("window.originalShell=document.getElementById('root:section'); window.originalLead=document.getElementById('root:lead_in')")
  await page.get_by_role('button',name='Expand A simple successor argument',exact=True).click()
  await page.wait_for_selector('[data-target="setup"]')
  await page.wait_for_function('!state.busy')
  assert await page.evaluate("window.originalShell===document.getElementById('root:section') && window.originalLead===document.getElementById('root:lead_in')")
  view=await page.evaluate('state.view')
  await page.locator('.graph-node[data-node="setup"]').hover()
  assert await page.evaluate('state.view')==view
  assert await page.locator('[id="setup:section"]').evaluate("e=>e.classList.contains('hovered')")
  await page.locator('.graph-node[data-node="setup"]').click()
  await page.wait_for_function("document.querySelector('#detail-title').textContent==='The successor and its bound'")
  await page.get_by_role('button',name='Expand The successor and its bound',exact=True).focus()
  await page.keyboard.press('Enter')
  await page.wait_for_selector('[id="definition:section"]')
  await page.wait_for_function('!state.busy')
  assert await page.locator('.katex').count()>0
  assert await page.locator('[id="bound:statement"]').count()==1
  assert await page.locator('[id="bound:proof"]').count()==1
  await page.locator('.graph-node[data-node="bound"]').click()
  await page.locator('[data-detail="lean"]').click()
  await page.wait_for_function("document.querySelector('#detail-content').textContent.includes('theorem bound')")
  await page.locator('[data-detail="sources"]').click()
  await page.wait_for_function("document.querySelector('#detail-content').textContent.includes('handwritten_fixture')")
  await page.locator('[data-detail="interfaces"]').click()
  await page.wait_for_function("document.querySelector('#detail-content').textContent.includes('definition → bound')")
  await page.locator('[data-detail="members"]').click()
  await page.wait_for_function("document.querySelector('#detail-content').textContent.includes('demo: bound')")
  await page.locator('.dependency-link').first.click()
  await page.wait_for_function("document.querySelector('#detail-title').textContent==='Dependency evidence'")
  assert await page.locator('.edge-endpoint').count()>=2
  assert 'PROVIDER' in await page.locator('#detail-content').inner_text()
  await page.locator('.edge-source summary').filter(has_text='Lean source').first.click()
  await page.wait_for_function("document.querySelector('.edge-source').dataset.loaded==='true'")
  await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
  await page.wait_for_timeout(1500)
  await page.screenshot(path=str(output/'desktop.png'),full_page=True)
  # Budget changes create a view without automatically collapsing visible content.
  await page.locator('#budget').fill('1')
  await page.locator('#budget-save').click()
  await page.wait_for_function("!state.busy && document.querySelector('#length').textContent.includes('over budget')")
  assert await page.locator('[id="definition:section"]').count()==1
  await page.get_by_role('button',name='Expand Strict growth',exact=True).click()
  await page.wait_for_function("document.querySelector('#notice').textContent.includes('budget')")
  assert await page.locator('[id="result:section"]').count()==0
  await page.locator('#budget').fill('')
  await page.locator('#budget-save').click()
  await page.wait_for_function("!state.busy && !document.querySelector('#length').textContent.includes('over budget')")
  # Recommendation and manual buttons invoke the same server action.
  await page.locator('.suggestion').first.click()
  await page.wait_for_selector('[id="result:section"]')
  await page.wait_for_function('!state.busy')
  await page.locator('#back').click()
  await page.wait_for_function("!state.busy && document.querySelector('#view-status').textContent.includes('Previous')")
  assert await page.locator('[data-target="conclusion"]').is_disabled()
  await page.locator('#latest').click()
  await page.wait_for_function('!state.busy && state.view===state.latest')
  # A second client changes the same reader; a stale UI action must recover visibly.
  await page.evaluate("api('apply_action',{reader_id:state.reader,expected_view:state.view,action:'reset',target:state.root})")
  await page.get_by_role('button',name='Collapse A simple successor argument',exact=True).click()
  await page.wait_for_function("document.querySelector('#notice').textContent.includes('old view')")
  await page.locator('#notice button').click()
  await page.wait_for_function('!state.busy && state.view===state.latest')
  assert await page.locator('#document .prose').all_text_contents()==initial
  # Markdown is sanitized; TeX delimiters survive Markdown parsing.
  await page.evaluate("""()=>{let e=document.createElement('div');window.pwned=false;markdown('<img src=x onerror="window.pwned=true"><script>window.pwned=true</script> \\\\(n+1\\\\)',e);document.body.append(e);window.testMath=e.querySelectorAll('.katex').length;window.testDanger=e.querySelectorAll('script,img').length;e.remove();}""")
  assert await page.evaluate('window.pwned===false && window.testDanger===0 && window.testMath===1')
  # Explicit UI transport failure preserves the current view.
  await page.route('**/api/apply_action',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'ok':False,'view_id':view,'error':{'code':'generation_failed','message':'Controlled generation failure'}})))
  await page.get_by_role('button',name='Expand A simple successor argument',exact=True).click()
  await page.wait_for_function("document.querySelector('#notice').textContent.includes('Controlled generation failure')")
  assert await page.locator('#document .prose').all_text_contents()==initial
  await page.unroute('**/api/apply_action')
  # Exercise the pending-job UI with a controlled transport response.
  await page.route('**/api/apply_action',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'ok':True,'view_id':view,'job':{'job_id':'controlled-job','status':'pending','applied':False}})))
  async def job(route):
   if route.request.post_data_json.get('detail')=='job':
    await route.fulfill(status=200,content_type='application/json',body=json.dumps({'ok':True,'view_id':view,'job':{'job_id':'controlled-job','status':'failed','applied':False,'latest_view':view,'error':{'message':'Controlled job failure'}}}))
   else:await route.continue_()
  await page.route('**/api/inspect',job)
  await page.get_by_role('button',name='Expand A simple successor argument',exact=True).click()
  await page.wait_for_function("document.querySelector('#notice').textContent.includes('Controlled job failure')")
  assert await page.locator('#document .prose').all_text_contents()==initial
  await page.set_viewport_size({'width':390,'height':844})
  await page.screenshot(path=str(output/'mobile.png'),full_page=True)
  assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
  assert not errors, errors
  result={'real_checks':['server pagination','stable shells and lead-in','hover does not expand','map location','keyboard expansion','math rendering','statement/proof','edge inspection','structured detail tabs','budget','recommendation expansion','old view','stale recovery','collapse restoration','sanitization','mobile overflow'], 'controlled_transport_checks':['generation failure','pending job polling and failure'],'console_errors':errors}
  (output/'browser-report.json').write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps(result,indent=2))
  await browser.close()

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8766');parser.add_argument('--output',type=Path,default=Path('data/research/reader_delivery/web'))
 args=parser.parse_args();asyncio.run(run(args.url,args.output))
