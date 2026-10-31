// Browser interaction and layout checks. Pass a Playwright package path if it is not on NODE_PATH.
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.argv[2]||'playwright');
(async()=>{
  const root=path.resolve(__dirname,'../../..');
  const out=path.join(root,'build/system_animation');
  const browser=await chromium.launch({headless:true,channel:process.env.NPU_BROWSER_CHANNEL||'msedge'});
  const page=await browser.newPage({viewport:{width:1440,height:1050},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('file:///'+path.join(__dirname,'index.html').replaceAll('\\','/'));
  await page.waitForFunction(()=>!!window.NPU_ANIMATION);
  const chapters=await page.evaluate(()=>window.NPU_ANIMATION.chapters);
  let frames=0;
  for(let i=0;i<chapters.length;i++){
    await page.evaluate(i=>window.NPU_ANIMATION.selectChapter(i),i);
    const count=chapters[i].id==='replay'?1:chapters[i].steps;
    for(let j=0;j<count;j++){
      await page.evaluate(j=>window.NPU_ANIMATION.setStep(j),j);frames++;
      if(!await page.locator('#stepTitle').textContent())throw Error('Missing step title');
    }
    await page.evaluate(()=>window.NPU_ANIMATION.setStep(0));
    if(['overview','gather','array','replay'].includes(chapters[i].id)){
      await page.screenshot({path:path.join(out,`desktop-${chapters[i].id}.png`),fullPage:true});
    }
  }
  await page.evaluate(()=>window.NPU_ANIMATION.selectChapter(9));
  for(const variant of ['fused','plain']){
    await page.evaluate(v=>window.NPU_ANIMATION.setVariant(v),variant);
    const total=await page.evaluate(()=>window.NPU_ANIMATION.state.total);
    // Exercise all actual captured cycles for null/unknown/value-format handling.
    await page.evaluate(n=>{for(let i=0;i<n;i++)window.NPU_ANIMATION.setStep(i);},total);
    frames+=total;
    await page.locator('#restart').click();
    await page.locator('#nextIssue').click();
    const jump=await page.evaluate(()=>window.NPU_ANIMATION.state.step);
    if(jump<=0)throw Error('Next issue did not advance');
    await page.locator('#nextWrite').click();
    await page.locator('#nextPC').click();
  }
  await page.locator('#restart').click();await page.locator('#play').click();
  await page.waitForTimeout(1100);await page.locator('#play').click();
  if((await page.evaluate(()=>window.NPU_ANIMATION.state.step))<1)throw Error('Playback did not advance');
  await page.evaluate(()=>window.NPU_ANIMATION.selectChapter(4));
  await page.locator('[data-pos="9"]').click();await page.locator('#kgroup').selectOption('2');
  await page.evaluate(()=>window.NPU_ANIMATION.selectChapter(5));
  await page.locator('[data-pe="9"]').click();
  await page.evaluate(()=>window.NPU_ANIMATION.setStep(6));
  await page.screenshot({path:path.join(out,'desktop-array-active.png'),fullPage:true});
  await page.locator('#sourcesBtn').click();
  if(!await page.locator('#sourceDialog').isVisible())throw Error('Sources modal failed');
  await page.locator('#closeSources').click();
  await page.setViewportSize({width:390,height:844});
  for(let i=0;i<chapters.length;i++){
    await page.evaluate(i=>window.NPU_ANIMATION.selectChapter(i),i);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
    if(overflow)throw Error(`Page overflows mobile viewport: ${chapters[i].id}`);
  }
  await page.evaluate(()=>window.NPU_ANIMATION.selectChapter(4));
  await page.screenshot({path:path.join(out,'mobile-gather.png'),fullPage:true});
  await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
  await page.evaluate(()=>window.NPU_ANIMATION.selectChapter(9));
  await page.screenshot({path:path.join(out,'mobile-replay-dark.png'),fullPage:true});
  // The standalone file must work without requesting any sibling files or network resources.
  const standaloneRequests=[];
  const single=await browser.newPage({viewport:{width:1440,height:1050}});
  single.on('request',r=>standaloneRequests.push(r.url()));
  single.on('pageerror',e=>errors.push(e.message));
  await single.goto('file:///'+path.join(__dirname,'standalone.html').replaceAll('\\','/'));
  await single.waitForFunction(()=>!!window.NPU_ANIMATION);
  await single.evaluate(()=>{window.NPU_ANIMATION.selectChapter(9);window.NPU_ANIMATION.setStep(0);});
  if((await single.locator('#visual').textContent()).includes('null'))throw Error('Unknown value leaked as null');
  if(standaloneRequests.length!==1)throw Error('Standalone requested external resources');
  await single.locator('#nextIssue').click();
  await single.screenshot({path:path.join(out,'standalone-replay-active.png'),fullPage:true});
  if(errors.length)throw Error(errors.join('\n'));
  const result={passed:true,chapters:chapters.length,rendered_frames:frames,viewports:['1440×1050','390×844'],
    checks:['all lesson frames','all fused and plain RTL cycles','play/pause','event jumps','variant switch','gather position/group','PE selection','sources dialog','mobile overflow','dark/reduced-motion','standalone without external resources','unknown values displayed as X'],page_errors:errors};
  fs.writeFileSync(path.join(out,'browser-check.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result,null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
