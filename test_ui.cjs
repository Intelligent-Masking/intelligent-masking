const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function element(){const classes=new Set();return {disabled:false,hidden:false,value:'',textContent:'',innerHTML:'',style:{},dataset:{},attributes:{},listeners:{},classList:{add(...xs){xs.forEach(x=>classes.add(x));},remove(...xs){xs.forEach(x=>classes.delete(x));},toggle(x,on){if(on===undefined)on=!classes.has(x);on?classes.add(x):classes.delete(x);},contains:x=>classes.has(x)},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];if(k==='data-state')delete this.dataset.state;},addEventListener(name,fn){this.listeners[name]=fn;},replaceChildren(){this.innerHTML='';},focus(){},scrollIntoView(){},querySelectorAll(){return[]},querySelector(){return null}};}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
const document={getElementById:get,querySelectorAll:()=>[]};
for(const id of ['cats','items'])get(id).parentElement=element();
const context=vm.createContext({document,window:{IM_MAX_UPLOAD:4200000},URL,Blob,File,Uint8Array,TextDecoder,Response,ReadableStream,FormData,atob,btoa,console,setTimeout:fn=>{fn();return 0;},matchMedia:()=>({matches:true})});
const html=fs.readFileSync(__dirname+'/index.html','utf8'),script=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
vm.runInContext(script,context);vm.runInContext('function selectFiles(files){clearSelection();addFiles(files);}function selectFile(file){selectFiles(file?[file]:[]);}',context);const run=code=>vm.runInContext(code,context),sample='1. 판정\n필수값 누락\n\n2. 누락 항목\n- 2페이지 신분증사본첨부란의 전용 첨부 영역이 비어 있습니다.\n\n3. 추가 확인\n- 없습니다.\n\n4. 보완 안내\n- 2페이지에 제공된 신분증 사본 첨부란에 신분증 사본을 첨부해 보완해 주세요.\n- 그 외에는 앞 단계 검증 결과상 추가 확인 사항이 없습니다.';
context.sample=sample;
for(let step=0;step<4;step++){
 run(`showSteps(${step})`);assert.equal(Number.parseFloat(get('progress-fill').style.width),step/3*100);assert.equal(Number(get('progress-meter').attributes['aria-valuenow']),step/3*100);
}
let parsed=run('parseReview(sample)');assert.equal(parsed.decision,'필수값 누락');assert.equal(parsed.missing.length,1);assert.equal(parsed.checks.length,0);assert.equal(parsed.actions.length,1);
context.inline=sample.replaceAll('\n',' ');parsed=run('parseReview(inline)');assert.equal(parsed.missing.length,1);assert.equal(parsed.actions.length,1);
context.raw='# 검토 안내\n본문 형식을 읽지 못해도 안내를 보존합니다. <img src=x onerror=alert(1)>';
assert.ok(run('reviewDetails(raw)').includes('&lt;img'));assert.ok(!run('reviewDetails(raw)').includes('<img'));
context.review={문서종:'신용카드 개인회원 가입신청서',검토필요:true,사유:sample,원본url:'/runs/'+'a'.repeat(32)+'/input.pdf',소요초:20.3};
run('render(review)');assert.equal(get('grade-value').textContent,'검토 필요');assert.equal(get('grade-label').textContent,'분석 상태');assert.ok(get('notes').innerHTML.includes('보완이 필요한 항목'));assert.ok(get('notes').innerHTML.includes('page-tag'));assert.ok(!get('hint').textContent.includes('마스킹본'));assert.equal(get('progress').dataset.state,'review');assert.ok(get('steps').innerHTML.includes('보류'));assert.equal(get('progress-meter').hidden,true);
context.unsupported={미지원:true,문서종:'others',문서유형:'장보기 목록',사유:'지원하는 서식이 아닙니다.',원본url:reviewUrl()};function reviewUrl(){return '/runs/'+'b'.repeat(32)+'/input.pdf';}
run('render(unsupported)');assert.equal(get('grade-value').textContent,'미지원');assert.equal(get('progress').dataset.state,'unsupported');assert.ok(!get('hint').textContent.includes('마스킹본'));
run("selectFile({name:'wrong.pdf',size:28});showSteps(0);fail('PDF를 다시 저장해 주세요.',true)");assert.ok(get('job-error').textContent.includes('PDF를 다시 저장'));assert.equal(get('progress').dataset.state,'error');assert.equal(get('progress-meter').hidden,true);
run("selectFile({name:'valid.pdf',size:100})");assert.ok(get('progress').classList.contains('hide'));assert.ok(get('out').classList.contains('hide'));assert.ok(get('err').classList.contains('hide'));assert.equal(get('go').disabled,false);
context.partial={등급:'상',문서종:'금융 서식',마스킹본:[{등급:'중',url:'/runs/'+'a'.repeat(32)+'/mid.pdf'}],비고:['하 등급본 생성 실패'],원본url:reviewUrl()};run('render(partial)');assert.equal(get('progress').dataset.state,'partial');assert.ok(get('notes').innerHTML.includes('일부 마스킹본'));assert.equal(get('grade-value').textContent,'상');
context.low={등급:'하',문서종:'금융 서식',마스킹본:[],비고:[],원본url:reviewUrl()};run('render(low)');assert.equal(get('progress').dataset.state,'success');assert.ok(get('hint').textContent.includes('추가 마스킹이 필요하지'));assert.equal(get('grade-label').textContent,'위험 등급');
context.combined={결합:true,등급:'상',문서들:[{...context.partial,쪽:'1'}, {...context.review,쪽:'2'}]};run('render(combined)');assert.ok(get('grid').innerHTML.includes('보완이 필요한 항목'));assert.equal(get('progress').dataset.state,'partial');
(async()=>{
 context.fetch=async()=>new Response(JSON.stringify({detail:'파일 내용이 PDF 형식이 아닙니다.'}),{status:400,headers:{'content-type':'application/json'}});
 run("selectFile({name:'invalid.pdf',size:100})");await run('go.onclick()');assert.equal(get('go').disabled,true);assert.equal(run('selectedFiles.length'),1);assert.equal(get('progress').dataset.state,'error');
 context.fetch=async()=>new Response(JSON.stringify({detail:'문서 분석 서비스를 준비하고 있습니다. 잠시 후 다시 시도해 주세요.'}),{status:503,headers:{'content-type':'application/json'}});
 run("selectFile({name:'valid.pdf',size:100})");await run('go.onclick()');assert.equal(get('go').disabled,false);assert.equal(get('go-label').textContent,'실패한 1개 다시 분석');assert.ok(!get('job-error').textContent.includes('AGENT_'));
 const requests=[];context.fetch=async(url)=>{requests.push(url);return url==='/api/process'?new Response(JSON.stringify({ticket:'opaque-test-ticket',step:1}),{headers:{'content-type':'application/json'}}):new Response(JSON.stringify({detail:'분석 대기 시간이 20분을 넘었습니다. 잠시 후 다시 시도해 주세요.'}),{status:408,headers:{'content-type':'application/json'}});};
 run("selectFile({name:'valid.pdf',size:100})");await run('go.onclick()');assert.deepEqual(requests,['/api/process','/api/continue']);assert.ok(get('job-error').textContent.includes('20분'));assert.equal(get('progress-meter').hidden,true);assert.equal(get('go').disabled,false);
 context.fetch=run('(async()=>{throw new TypeError("Failed to fetch")})');run("selectFile({name:'valid.pdf',size:100})");await run('go.onclick()');assert.ok(get('job-error').textContent.includes('서버에 연결하지 못했습니다'));assert.equal(get('go').disabled,false);
 context.fetch=async()=>new Response('{"step":1}\n',{headers:{'content-type':'application/x-ndjson'}});run("selectFile({name:'valid.pdf',size:100})");await run('go.onclick()');assert.ok(get('job-error').textContent.includes('연결이 종료'));assert.equal(get('out').classList.contains('hide'),true);
 // Mixed inputs retain valid PDFs; failed files do not prevent other workers finishing.
 run("selectFiles([{name:'a.pdf',size:100},{name:'bad.txt',size:20},{name:'huge.pdf',size:4200001},{name:'b.pdf',size:200}])");
 assert.equal(run('selectedFiles.length'),2);assert.ok(get('err').textContent.includes('bad.txt'));assert.ok(get('err').textContent.includes('huge.pdf'));
 run("selectFiles(Array.from({length:5},(_,i)=>({name:`file${i}.pdf`,size:100+i})))");
 const releases=[],calls=[];let active=0,maxActive=0;
 context.fetch=(url,options)=>new Promise(resolve=>{const id=calls.length;calls.push({url,file:options.body.get('file')});active++;maxActive=Math.max(active,maxActive);releases.push(()=>{active--;resolve(id===1?new Response(JSON.stringify({detail:'잘못된 PDF'}),{status:422}):new Response(JSON.stringify({done:true,result:{...context.low,원본url:'/runs/'+String(id+1).repeat(32)+'/input.pdf'}})+'\n',{headers:{'content-type':'application/x-ndjson'}}));});});
 const processing=run('go.onclick()');assert.equal(calls.length,3);assert.equal(run('jobs.filter(j=>j.state==="running").length'),3);assert.equal(run('jobs.filter(j=>j.state==="queued").length'),2);
 releases.shift()();await new Promise(setImmediate);assert.equal(calls.length,4);assert.equal(run('jobs[0].state'),'done');
 // Navigating to an in-progress file shows that file's progress, not a previous result.
 run('showJob(2)');assert.equal(get('active-filename').textContent,'file2.pdf');assert.ok(get('out').classList.contains('hide'));
 while(releases.length){releases.shift()();await new Promise(setImmediate);}await processing;
 assert.equal(maxActive,3);assert.equal(calls.length,5);assert.equal(run('jobs.filter(j=>j.state==="done").length'),4);assert.equal(run('jobs[1].state'),'error');assert.equal(run('selectedFiles.length'),5);assert.equal(get('go').disabled,true);
 run('showJob(1)');assert.equal(get('job-error').textContent,'잘못된 PDF');assert.equal(get('progress').dataset.state,'error');
 run('showJob(0)');assert.ok(get('job-error').classList.contains('hide'));assert.equal(get('grade-value').textContent,'하');assert.equal(get('job-position').textContent,'1 / 5');
 // Mobile swipes and keyboard navigation select one result without sharing errors.
 get('job-view').listeners.pointerdown({pointerType:'touch',clientX:260,clientY:100,target:{closest:()=>null}});get('job-view').listeners.pointerup({clientX:80,clientY:110});assert.equal(run('activeJob'),1);
 get('file-tabs').listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(run('activeJob'),2);
 get('job-view').listeners.pointerdown({pointerType:'touch',clientX:260,clientY:100,target:{closest:()=>null}});get('job-view').listeners.pointerup({clientX:240,clientY:400});assert.equal(run('activeJob'),2);
 // Each resumable ticket must receive its own original PDF when requested.
 const tickets=new Map();context.fetch=async(url,options)=>{if(url==='/api/process'){const pdf=options.body.get('file');tickets.set(pdf.name,pdf.name);return new Response(JSON.stringify({ticket:pdf.name,step:1,requiresFile:true}),{headers:{'content-type':'application/json'}});}const ticket=options.body.get('ticket');assert.equal(options.body.get('file').name,tickets.get(ticket));return new Response(JSON.stringify({done:true,result:context.low})+'\n',{headers:{'content-type':'application/x-ndjson'}});};
 run("selectFiles(['alpha','beta','gamma'].map(name=>new File(['%PDF'],name+'.pdf',{type:'application/pdf'})))");await run('go.onclick()');assert.equal(tickets.size,3);assert.equal(run('jobs.every(j=>j.state==="done")'),true);
 // Streaming decoder keeps Korean text intact across fragmented byte boundaries.
 const encoded=new TextEncoder().encode(JSON.stringify({done:true,result:context.review})+'\n');context.fragmented=new Response(new ReadableStream({start(controller){for(let i=0;i<encoded.length;i+=7)controller.enqueue(encoded.slice(i,i+7));controller.close();}}));
 const streamed=await run('readAnalysisStream(fragmented,()=>{})');assert.equal(streamed.사유,sample);
 // Retry only transient failures, preserving completed results and their Blob URLs.
 let attempts=0;context.fetch=async()=>{attempts++;return attempts===1?new Response(JSON.stringify({detail:'일시적 오류'}),{status:503}):new Response(JSON.stringify({done:true,result:context.low})+'\n',{headers:{'content-type':'application/x-ndjson'}});};
 run("selectFiles([{name:'retry.pdf',size:100},{name:'done.pdf',size:200}])");await run('go.onclick()');assert.equal(attempts,2);assert.equal(run('jobs[0].state'),'error');
 run("receiveFile({path:'/runs/'+ 'c'.repeat(32)+'/input.pdf',pdf:btoa('test')})");const saved=run('documentFiles.size');
 await run('go.onclick()');assert.equal(attempts,3);assert.equal(run('documentFiles.size'),saved);assert.equal(run('jobs.every(j=>j.state==="done")'),true);
 run("selectFile({name:'fresh.pdf',size:100})");assert.equal(run('jobs.length'),0);assert.equal(run('documentFiles.size'),0);assert.ok(get('batch').classList.contains('hide'));
 // Additive upload retains completed results and only analyzes newly added files.
 let newCalls=0;context.fetch=async()=>{newCalls++;return new Response(JSON.stringify({done:true,result:context.low})+'\n',{headers:{'content-type':'application/x-ndjson'}});};
 run("selectFile({name:'first.pdf',size:100,lastModified:1})");await run('go.onclick()');
 run("receiveFile({path:'/runs/'+ 'd'.repeat(32)+'/input.pdf',pdf:btoa('saved')})");const firstResult=run('jobs[0].result'),blobCount=run('documentFiles.size');
 run("addFiles([{name:'second.pdf',size:200,lastModified:2}])");assert.equal(run('selectedFiles.length'),2);assert.equal(run('jobs[0].result'),firstResult);assert.equal(run('documentFiles.size'),blobCount);assert.ok(!get('out').classList.contains('hide'));
 run("addFiles([{name:'second.pdf',size:200,lastModified:2}])");assert.equal(run('selectedFiles.length'),2);
 await run('go.onclick()');assert.equal(newCalls,2);assert.equal(run('jobs.length'),2);assert.equal(run('jobs[0].result'),firstResult);assert.equal(run('documentFiles.size'),blobCount);
 run('removeFile(1)');assert.equal(run('jobs.length'),1);assert.equal(run('selectedFiles.length'),1);assert.equal(run('jobs[0].result'),firstResult);
 // Additional PDFs can join a running batch immediately, without exceeding 3 workers.
 const queuedReleases=[];let concurrent=0,peak=0,totalCalls=0;
 context.fetch=()=>new Promise(resolve=>{totalCalls++;concurrent++;peak=Math.max(peak,concurrent);queuedReleases.push(()=>{concurrent--;resolve(new Response(JSON.stringify({done:true,result:context.low})+'\n',{headers:{'content-type':'application/x-ndjson'}}));});});
 run("selectFile({name:'live0.pdf',size:100})");const live=run('go.onclick()');assert.equal(totalCalls,1);assert.equal(get('drop').disabled,false);
 run("addFiles([1,2,3,4].map(i=>({name:`live${i}.pdf`,size:100+i})))");assert.equal(totalCalls,3);assert.equal(run('jobs.length'),5);assert.equal(run('selectedFiles.length'),5);assert.equal(get('progress-meter').hidden,false);assert.ok(get('steps').innerHTML.includes('업로드'));
 while(queuedReleases.length){queuedReleases.shift()();await new Promise(setImmediate);}await live;assert.equal(totalCalls,5);assert.equal(peak,3);assert.equal(run('jobs.every(j=>j.state==="done")'),true);
 assert.ok(html.includes('--bg:#f3f6f8'));assert.ok(!html.includes('--bg:#edf3f0'));assert.ok(html.indexOf('id="progress"')<html.indexOf('id="batch"'));
 assert.ok(html.includes('multiple hidden'));assert.ok(!html.includes('data-reselect'));assert.ok(!html.includes('id="retry-file"'));
 console.log('UI: 검토 결과 구조화·강조, 실패 처리, 파일 누적·결과 유지·분석 중 추가, 단계 바 복원, 최대 3개 동시 실행, 개별 실패 격리, 문서별 이동, 실패만 재시도 OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
