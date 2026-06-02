/* ═══════════════════════════════════════════
   STEP 4 — Run Analysis
   ═══════════════════════════════════════════ */
function setPipelineNodes(index){
  PIPE_IDS.forEach(function(id,i){
    const node=document.getElementById(id);
    if(!node)return;
    node.classList.remove('active','done');
    if(index>=0&&i<index)node.classList.add('done');
    if(index>=0&&index<PIPE_IDS.length&&i===index)node.classList.add('active');
  });
}
function syncPipelineLabels(){
  const labels=state.analysisMode==='debate'?DEBATE_PIPELINE:REVIEW_PIPELINE;
  PIPE_IDS.forEach(function(id,i){
    const node=document.getElementById(id);
    if(!node)return;
    const name=node.querySelector('.pipeline-name');
    if(name)name.textContent=labels[i];
  });
  const hint=document.getElementById('pipelineHint');
  const runBtn=document.getElementById('runBtn');
  if(state.analysisMode==='debate'){
    if(hint)hint.textContent='当前模式：多 Agent 辩论流程';
    if(runBtn)runBtn.textContent='启动辩论';
  }else{
    if(hint)hint.textContent='当前模式：标准审查流程';
    if(runBtn)runBtn.textContent='启动审查';
  }
  setPipelineNodes(-1);
}
function setAnalysisMode(mode,btn){
  state.analysisMode=mode;
  const wrap=btn?btn.parentElement:document;
  if(wrap&&wrap.querySelectorAll){
    wrap.querySelectorAll('.analysis-mode-btn').forEach(function(x){x.classList.remove('active')});
  }
  if(btn)btn.classList.add('active');
  document.getElementById('modeReviewBtn')?.classList.toggle('active',mode==='review');
  document.getElementById('modeDebateBtn')?.classList.toggle('active',mode==='debate');
  syncPipelineLabels();
}
function toggleTextAdvanced(){
  const box=document.getElementById('textAdvanced');
  if(!box)return;
  box.classList.toggle('collapsed');
}
const PIPELINE_STEPS={
  review:['构建证据中','专家分派中','审查执行中','记忆写入中','报告生成中'],
  debate:['构建证据中','Round 1 首轮观点生成中','Round 2 交叉辩论中','共识收敛中','辩论报告生成中']
};
function getPipelineStepName(mode,idx){
  const list=PIPELINE_STEPS[mode]||PIPELINE_STEPS.review;
  return list[idx]||'处理中';
}
function startPipelineTicker(){
  const steps=PIPELINE_STEPS[state.analysisMode]||PIPELINE_STEPS.review;
  let idx=0;
  setPipelineNodes(0);
  showLoading(steps[0]+'...');
  const timer=setInterval(function(){
    idx=Math.min(idx+1,steps.length-1);
    setPipelineNodes(idx);
    showLoading(steps[idx]+'...');
  },1100);
  return function stopTicker(){
    clearInterval(timer);
    setPipelineNodes(PIPE_IDS.length);
  };
}

/* ═══ STEP 4 ═══ */
async function runAnalysis(){
  const ac=+document.getElementById('agentCount').value,um=document.getElementById('useMock').checked;
  const stopTicker=startPipelineTicker();
  try{
    const endpoint=state.analysisMode==='debate'?'/roundtable/debate':'/roundtable/run';
    const payload={session_id:state.sessionId,agent_count:ac,use_mock:um,lang:state.runLang,stream:true};
    const r=await apiFetch(API+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!r.ok){
      let msg='Request failed';
      try{const e=await r.json();msg=e.detail||e.error||msg}catch{}
      throw new Error(msg);
    }
    const meta=await r.json();
    if(!meta.stream_url){
      // 后端不支持流式，回退到同步模式
      state.reportMarkdown=meta.report||'';
      state.reportType=state.analysisMode;
      state.debateData=state.analysisMode==='debate'?buildDebateViewModel(meta):null;
      finalizeReport(meta);
      stopTicker();
      hideLoading();
      goStep(5);
      return;
    }
    // SSE 流式模式
    await connectAnalysisStream(meta.stream_url,stopTicker);
  }catch(e){
    stopTicker();
    hideLoading();
    showToast('Analysis failed: '+e.message,'error');
  }
}

function finalizeReport(d){
  const tabDebate=document.getElementById('tab-debate');
  if(tabDebate)tabDebate.classList.toggle('disabled',state.analysisMode!=='debate');
  document.getElementById('reportType').textContent=state.analysisMode;
  document.getElementById('reportMode').textContent=d.mode||state.analysisMode;
  document.getElementById('reportMemories').textContent=typeof d.memories_written==='number'?d.memories_written:'—';
  document.getElementById('sessionStatus').textContent='completed';
  document.querySelectorAll('.step-item').forEach(function(s){s.classList.remove('active');s.classList.add('completed')});
  renderReport(d.report||'');
  renderDebatePanel();
  const defaultTab=state.analysisMode==='debate'?'debate':'report';
  const tabBtn=document.getElementById(defaultTab==='debate'?'tab-debate':'tab-report');
  showReportPanel(defaultTab,tabBtn);
}

function connectAnalysisStream(streamUrl,stopTicker){
  return new Promise(function(resolve,reject){
    let finalData=null;
    var url=streamUrl;
    if(url.startsWith('/'))url=API+url;
    const es=apiEventSource(url);
    const timeout=setTimeout(function(){
      es.close();
      reject(new Error('SSE connection timeout'));
    },600000); // 10min 总超时
    es.onmessage=function(ev){
      try{
        var msg=JSON.parse(ev.data);
      }catch(e){return;}
      if(msg.type==='heartbeat')return;
      if(msg.type==='stage'){
        var idx=typeof msg.idx==='number'?msg.idx:0;
        setPipelineNodes(idx);
        showLoading(getPipelineStepName(state.analysisMode,idx)+'...');
        return;
      }
      if(msg.type==='final_report'){
        finalData=msg.data||msg;
        state.reportMarkdown=(msg.data&&msg.data.report)||msg.report||'';
        state.reportType=state.analysisMode;
        state.debateData=state.analysisMode==='debate'?buildDebateViewModel(msg.data||msg):null;
        return;
      }
      if(msg.type==='error'){
        clearTimeout(timeout);
        es.close();
        reject(new Error(msg.content||'Stream error'));
        return;
      }
      if(msg.type==='done'){
        clearTimeout(timeout);
        es.close();
        if(finalData){
          finalizeReport(finalData);
        }else{
          showToast('No report received','warning');
        }
        stopTicker();
        hideLoading();
        goStep(5);
        resolve();
        return;
      }
    };
    es.onerror=function(){
      clearTimeout(timeout);
      es.close();
      reject(new Error('SSE connection error'));
    };
  });
}

/* ═══ STEP 5 ═══ */
