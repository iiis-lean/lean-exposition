"""Focused browser acceptance for the reader UI.

The check starts its own loopback server on a free port with a temporary reader
state. It never attaches to or controls an existing reader process.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "reader_ui"
STATIC = ROOT / "src" / "lean_exposition" / "app" / "static"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_python():
    override = os.environ.get("READER_SERVER_PYTHON")
    if override:
        return override
    base = Path(sys.base_prefix) / "bin" / "python"
    return str(base if base.exists() else Path(sys.executable))


def browser_executable():
    override = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if override:
        return override
    cached = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
    return str(cached[-1]) if cached else None


async def wait_for_server(url):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            await asyncio.to_thread(lambda: urlopen(url + "/api/instances", timeout=.5).read())
            return
        except Exception:
            await asyncio.sleep(.05)
    raise RuntimeError("independent reader server did not start")


async def run():
    generation = json.loads((FIXTURES / "generation_states.json").read_text())
    dense = json.loads((FIXTURES / "dense_graph.json").read_text())
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="reader-ui-") as state_dir:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        process = subprocess.Popen(
            [server_python(), "-m", "lean_exposition.app", "--state-dir", state_dir,
             "--static-dir", str(STATIC), "--port", str(port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            await wait_for_server(url)
            async with async_playwright() as playwright:
                launch = {"headless": True, "args": ["--no-sandbox"]}
                executable = browser_executable()
                if executable:
                    launch["executable_path"] = executable
                browser = await playwright.chromium.launch(**launch)
                page = await browser.new_page(viewport={"width": 1600, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(url)
                await page.wait_for_function("!state.busy")

                # The contents panel covers the page instead of changing its geometry.
                main_before = await page.locator("#main").bounding_box()
                await page.locator("#map-handle").hover()
                await page.wait_for_function("document.querySelector('.map-panel').classList.contains('open')")
                main_after = await page.locator("#main").bounding_box()
                assert main_before == main_after
                await page.locator("#map-pin").click()
                assert await page.locator("#map-pin").get_attribute("aria-pressed") == "true"
                await page.locator("#map-close").click()

                # Frozen DTOs use controlled transport before the real workflow is wired.
                published_before = await page.locator("#document .prose").all_inner_texts()
                current_view = await page.evaluate("state.view")
                queued = {**generation[0], "job_id": "fixture-job", "applied": False}
                failed = {**generation[5], "job_id": "fixture-job", "applied": False,
                          "latest_view": current_view}
                await page.route("**/api/apply_action", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"ok": True, "view_id": current_view, "job": queued})))
                await page.route("**/api/inspect", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"ok": True, "view_id": current_view, "job": failed})))
                await page.get_by_role("button", name="Expand A simple successor argument", exact=True).click()
                await page.wait_for_function("document.querySelector('#job').dataset.status === 'failed'")
                assert await page.locator("#document .prose").all_inner_texts() == published_before
                await page.unroute("**/api/apply_action")
                await page.unroute("**/api/inspect")

                # Real cached actions exercise aligned nested text and frontier replacement.
                await page.get_by_role("button", name="Retry", exact=True).click()
                await page.wait_for_function("!state.busy && document.querySelector('[data-target=\"setup\"]')")
                await page.get_by_role("button", name="Expand The successor and its bound", exact=True).click()
                await page.wait_for_function("!state.busy && document.getElementById('definition:section')")
                xs = await page.locator("#document .segment > .prose").evaluate_all(
                    "els => els.map(e => Math.round(e.getBoundingClientRect().x))")
                assert max(xs) - min(xs) <= 1, xs
                assert await page.locator("#document .segment").evaluate_all(
                    "els => els.every(e => e.textContent.trim().length > 0)")
                label_geometry = await page.locator("#document .section-shell").evaluate_all("""shells => shells.map(shell => {
                    const label=shell.querySelector(':scope > .section-rail .rail-label');
                    const segment=shell.querySelector(':scope > .segment, :scope > .section-body > .section-shell > .segment');
                    if(!label||!segment) return {ok:true};
                    const a=label.getBoundingClientRect(), b=segment.getBoundingClientRect();
                    return {text:label.innerText,left:a.left,right:a.right,bottom:a.bottom,segmentTop:b.top,ok:a.left>=0&&a.right<=b.left+1};
                })""")
                assert all(item["ok"] for item in label_geometry), label_geometry
                rail_metrics = await page.locator("#document .rail-title").evaluate_all(
                    "els => els.map(e => ({text:e.innerText,scroll:e.scrollWidth,client:e.clientWidth,overflow:getComputedStyle(e).overflow}))")
                assert all(item["scroll"] <= item["client"] + 1 and item["overflow"] != "hidden" for item in rail_metrics), rail_metrics
                screenshot_dir = os.environ.get("READER_UI_SCREENSHOT_DIR")
                if screenshot_dir:
                    output = Path(screenshot_dir)
                    output.mkdir(parents=True, exist_ok=True)
                    await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
                    await page.wait_for_timeout(1300)
                    await page.screenshot(path=str(output / "desktop.png"), full_page=True)
                    await page.set_viewport_size({"width": 390, "height": 844})
                    await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
                    await page.wait_for_timeout(200)
                    await page.screenshot(path=str(output / "mobile.png"), full_page=True)
                    await page.set_viewport_size({"width": 1600, "height": 1000})

                # Every frozen state has a readable local status; no draft prose is invented.
                for dto in generation:
                    await page.evaluate("dto => { state.jobTarget='root'; renderJob({...dto,job_id:'fixture'}); }", dto)
                    assert await page.locator("#job").get_attribute("data-status") == dto["status"]
                    assert (await page.locator("#job").inner_text()).strip()
                    if dto["status"] == "drafting":
                        assert await page.locator("#job .generation-progress i").count() == dto["total_children"]

                # Dense fixture isolates deterministic layout, encodings and soft routing.
                await page.evaluate("fixture => {\n"
                    " state.nodes=fixture.nodes; state.edges=fixture.edges; state.lines=[]; state.anchors=[];\n"
                    " for (const node of fixture.nodes) { if (node.kind==='external') continue; const line=state.lines.length+1; state.lines.push(node.text); state.anchors.push({anchor_id:node.id+':statement',start_line:line,end_line:line}); }\n"
                    " graphCamera.positions.clear(); graphCamera.pinned.clear(); graphCamera.initialized=false; renderHDG();\n"
                    "}", dense)
                positions_one = await page.evaluate(
                    "Object.fromEntries([...graphCamera.positions].map(([id,p])=>[id,[+p.x.toFixed(3),+p.y.toFixed(3)]]))")
                await page.evaluate("graphCamera.positions.clear(); graphCamera.initialized=false; renderHDG()")
                positions_two = await page.evaluate(
                    "Object.fromEntries([...graphCamera.positions].map(([id,p])=>[id,[+p.x.toFixed(3),+p.y.toFixed(3)]]))")
                assert positions_one == positions_two
                assert await page.locator('.hdg-node title, .hdg-caption, .hdg-edge title').count()==0
                await page.evaluate("state.selected=null; highlight('a',true)")
                contrast=await page.locator('.hdg-edge').evaluate_all("els=>({active:els.filter(e=>e.classList.contains('incident')).map(e=>+getComputedStyle(e).opacity),background:els.filter(e=>!e.classList.contains('incident')).map(e=>+getComputedStyle(e).opacity)})")
                assert min(contrast['active'])>max(contrast['background']),contrast
                await page.evaluate("highlight('a',false)")

                assert await page.evaluate("contentRadius(800) < contentRadius(8000) && contentRadius(8000) < contentRadius(80000)")
                assert await page.evaluate("dependencyColor(12,false) !== dependencyColor(48,false)")
                assert await page.evaluate("edgeWidth(1) < edgeWidth(4) && edgeWidth(4) < edgeWidth(16)")
                assert await page.locator('.hdg-edge').evaluate_all("els=>els.every(e=>getComputedStyle(e).markerEnd !== 'none')")
                weighted = await page.locator('.hdg-edge').evaluate_all("els=>els.map(e=>parseFloat(getComputedStyle(e).strokeWidth))")
                assert max(weighted)>min(weighted),weighted

                assert not await page.evaluate("""() => {
                    const ps=[...graphCamera.positions.values()];
                    return ps.some((a,i)=>ps.slice(i+1).some(b=>Math.hypot(a.x-b.x,a.y-b.y)<a.r+b.r));
                }""")
                assert await page.evaluate("graphCamera.positions.get('e').r > graphCamera.positions.get('a').r")
                assert await page.locator('[data-node="d"] .visible-dot').get_attribute("fill") != await page.locator('[data-node="a"] .visible-dot').get_attribute("fill")
                assert await page.locator(".hdg-edge").evaluate_all("els => els.every(e => e.getAttribute('d').includes(' C'))")
                assert not await page.evaluate("""() => [...document.querySelectorAll('.hdg-edge')].some(path => {
                    const provider=path.dataset.provider, consumer=path.dataset.consumer;
                    const circles=[...document.querySelectorAll('.hdg-node')]
                      .filter(node=>node.dataset.node!==provider&&node.dataset.node!==consumer)
                      .map(node=>node.querySelector('.visible-dot'));
                    const length=path.getTotalLength();
                    for(let i=1;i<20;i++){
                      const point=path.getPointAtLength(length*i/20);
                      if(circles.some(circle=>Math.hypot(point.x-circle.cx.baseVal.value,point.y-circle.cy.baseVal.value)<circle.r.baseVal.value+3)) return true;
                    }
                    return false;
                })""")

                # Dragging a dot pins it without selecting; double click releases it.
                hit = page.locator('[data-node="a"] .hit-dot')
                box = await hit.bounding_box()
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await page.mouse.down()
                await page.mouse.move(box["x"] + box["width"] / 2 + 55, box["y"] + box["height"] / 2 + 35, steps=6)
                await page.mouse.up()
                assert await page.evaluate("graphCamera.pinned.has('a')")
                await page.locator('[data-node="a"] .hit-dot').dispatch_event("dblclick")
                assert not await page.evaluate("graphCamera.pinned.has('a')")
                await page.locator("#graph-reset").click()
                assert await page.evaluate("graphCamera.pinned.size === 0")

                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.locator("#document .rail-title").evaluate_all(
                    "els => els.every(e => getComputedStyle(e).display !== 'none')")
                assert await page.locator("#document .segment[data-boundary-label]").evaluate_all(
                    "els => els.every(e => getComputedStyle(e,'::before').display === 'none')")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert not errors, errors
                await browser.close()
                print(json.dumps({
                    "independent_port": port,
                    "checks": ["cover outline", "frozen generation DTO", "published text preservation",
                               "aligned nested prose", "frontier layout determinism", "node collision",
                               "size/tone encodings", "cubic soft-obstacle routes",
                               "manual pin/release/reset", "mobile overflow"],
                    "page_errors": errors,
                }, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.returncode not in (0, -15):
                print(process.stderr.read(), file=sys.stderr)


async def run_projection():
    """Bilingual, multi-layer math and read-only graph card acceptance."""
    port=free_port()
    with tempfile.TemporaryDirectory(prefix="reader-projection-check-") as directory:
        env={**os.environ,"PYTHONPATH":str(ROOT/"src")}
        subprocess.run([server_python(),str(FIXTURES/"projection_demo.py"),directory],env=env,check=True)
        process=subprocess.Popen([server_python(),"-m","lean_exposition.app","--state-dir",directory+"/state","--packages",directory+"/packages.json","--port",str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        try:
            url=f"http://127.0.0.1:{port}"
            await wait_for_server(url)
            async with async_playwright() as p:
                browser=await p.chromium.launch(headless=True,executable_path=browser_executable(),args=["--no-sandbox"])
                page=await browser.new_page(viewport={"width":1600,"height":1100})
                errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
                await page.goto(url);await page.wait_for_function("!state.busy")
                await page.locator("#locale").select_option("en")
                await page.wait_for_function("!state.busy && state.locale==='en'")
                for id in ["root","setup","geometry"]:
                    await page.locator(f'.rail-label [data-target="{id}"]').click()
                    await page.wait_for_function("!state.busy")
                assert await page.locator('#document .katex').count() > 5
                assert await page.locator('.hdg-node .visible-dot').evaluate_all("els=>{const frame=document.querySelector('#hdg').getBoundingClientRect();return els.every(e=>{const r=e.getBoundingClientRect();return r.left>=frame.left&&r.right<=frame.right&&r.top>=frame.top&&r.bottom<=frame.bottom;});}")
                assert not await page.locator('#budget-form').is_visible()
                assert await page.locator('.recommendation-metrics, .hdg-toggle, #document .part-label').count()==0
                geometry=await page.locator('.section-shell').evaluate_all("""els=>els.map(e=>{
                    const label=e.querySelector(':scope > .section-rail .rail-label').getBoundingClientRect();
                    const prose=e.querySelector('.prose').getBoundingClientRect();
                    return {id:e.dataset.node,left:label.left,right:label.right,text:prose.left};
                })""")
                assert all(g['right'] <= g['text']+1 for g in geometry),geometry
                positions={g['id']:g['left'] for g in geometry}
                assert positions['root'] < positions['setup'] < positions['projection']
                output=Path(os.environ.get("READER_UI_SCREENSHOT_DIR","/tmp/reader-refined"));output.mkdir(parents=True,exist_ok=True)
                await page.evaluate("window.scrollTo(0,0)");await page.mouse.move(900,75)
                await page.screenshot(path=str(output/'projection-en.png'),full_page=True)
                before=await page.evaluate("state.view")
                await page.locator('.hdg-node[data-node="split"]').click()
                await page.wait_for_function("document.querySelector('.graph-card .interface-map .incoming') && document.querySelector('.graph-card .interface-map .outgoing')")
                assert await page.evaluate("state.view")==before
                assert await page.locator('.graph-card .interface-map .internal').count()==2
                assert await page.locator('.graph-card-header h2').inner_text()=='Decomposition and energy'
                assert await page.locator('.graph-card .interface-map path').count()>3
                await page.screenshot(path=str(output/'projection-region-card.png'),full_page=True)
                await page.keyboard.press('Escape')
                await page.locator('.hdg-node[data-node="idempotent"]').click()
                await page.wait_for_function("document.querySelector('.graph-card pre')")
                assert 'project_twice' in await page.locator('.graph-card pre').inner_text()
                assert await page.locator('.graph-card .katex').count()>0
                await page.screenshot(path=str(output/'projection-declaration-card.png'),full_page=True)
                await page.keyboard.press('Escape')
                await page.locator('#locale').select_option('zh')
                await page.wait_for_function("!state.busy && state.locale==='zh'")
                await page.evaluate("window.scrollTo(0,0)");await page.mouse.move(900,75)
                await page.screenshot(path=str(output/'projection-zh.png'),full_page=True)
                await page.set_viewport_size({"width":390,"height":844})
                await page.screenshot(path=str(output/'projection-mobile.png'),full_page=True)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert await page.locator('.rail-title').evaluate_all("els=>els.every(e=>e.getBoundingClientRect().right<=document.querySelector('#document .prose').getBoundingClientRect().left)")
                assert not errors,errors
                await browser.close()
                print(json.dumps({"projection_port":port,"bilingual_math":True,"readonly_region_card":True,"complete_source_card":True,"errors":errors}))
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(run())
    asyncio.run(run_projection())
