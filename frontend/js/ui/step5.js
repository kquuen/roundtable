/* ═══════════════════════════════════════════
   STEP 5 — Report & Debate Theater
   ═══════════════════════════════════════════ */
function renderReport(md){
  const body=document.getElementById('reportBody');let h=escHtml(md);
  const icons={'摘要':'S','会议事实':'F','推断与判断':'J','专家建议':'R','创意延展':'E','已降级':'W','已驳回':'X','待用户确认':'?','待解决问题':'Q','下一步行动':'A','审查统计':'N'};
  h=h.replace(/^# (.+)$/gm,'<h1>$1</h1>');
  h=h.replace(/^> \*\*(.+)\*\*$/gm,'<blockquote><strong>$1</strong></blockquote>');
  h=h.replace(/^## (.+)$/gm,function(m,t){var ic=Object.entries(icons).find(function(e){return t.includes(e[0])});return'<h2>'+(ic?'<span class="section-icon">'+ic[1]+'</span>':'')+t+'</h2>'});
  h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  h=h.replace(/^- \[(\w+)\] /gm,'<li><span class="agent-tag">[$1]</span> ');
  h=h.replace(/~(\w+)~/g,'<span style="text-decoration:line-through;color:var(--danger);font-size:.63rem">$1</span>');
  h=h.replace(/((?:<li>.*<\/li>\n?)+)/g,'<ul>$1</ul>');
  h=h.replace(/共 (\d+) 条 claim，通过 (\d+)，驳回 (\d+)/,function(m,t,a,r){return'<div class="report-stats">Review Stats<div class="stats-grid"><div class="stat-card"><div class="stat-num total">'+t+'</div><div class="stat-label">Total</div></div><div class="stat-card"><div class="stat-num approved">'+a+'</div><div class="stat-label">Approved</div></div><div class="stat-card"><div class="stat-num rejected">'+r+'</div><div class="stat-label">Rejected</div></div></div></div>'});
  h=h.replace(/---/g,'');body.innerHTML=h;
}
function showReportPanel(panel,btn){
  if(panel==='debate'&&state.reportType!=='debate')return;
  document.querySelectorAll('.report-tab').forEach(function(t){t.classList.remove('active')});
  document.querySelectorAll('.report-panel').forEach(function(p){p.classList.remove('active')});
  if(btn)btn.classList.add('active');
  document.getElementById('panel-'+panel)?.classList.add('active');
}
function escHtml(str){
  return String(str||'').replace(/[&<>"']/g,function(ch){
    return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
  });
}
function parseDebateReport(report){
  const fallback={round1:[],round2:[],consensus:[],conflicts:[]};
  if(!report)return fallback;
  const lines=report.split('\n');
  let section='';
  for(const raw of lines){
    const line=raw.trim();
    if(/^### Round 1/i.test(line)||/^### Round 1：/.test(line)){section='r1';continue}
    if(/^### Round 2/i.test(line)||/^### Round 2：/.test(line)){section='r2';continue}
    if(/^## 共识分层/.test(line)||/^## Consensus/.test(line)){section='cons';continue}
    if(/^## ⚠️/.test(line)||/^##\s*Conflicts/.test(line)){section='conf';continue}
    if(section==='r1'||section==='r2'){
      if(!line.startsWith('|')||line.includes('---')||line.includes('Position')||line.includes('立场'))continue;
      const cols=line.split('|').map(function(x){return x.trim()}).filter(Boolean);
      if(cols.length>=4){
        const pos=cols[0].replace(/[✅❌➕]/g,'').trim().toLowerCase();
        const agent=cols[1].replace(/`/g,'');
        const content=cols[2];
        const target=cols[3].replace(/^→\s*/,'').replace(/`/g,'');
        (section==='r1'?fallback.round1:fallback.round2).push({
          position:pos||'extend',
          agent:agent||'agent',
          content:content||'',
          target:target&&target!=='—'?target:''
        });
      }
      continue;
    }
    if(section==='cons'&&line.startsWith('- `')){
      const m=line.match(/^- `([^`]+)`\s*(.*)$/);
      if(m)fallback.consensus.push({id:m[1],text:m[2]||''});
      continue;
    }
    if(section==='conf'&&line.startsWith('**')){
      fallback.conflicts.push({text:line.replace(/\*\*/g,'')});
    }
  }
  return fallback;
}
function buildDebateViewModel(payload){
  const parsed=parseDebateReport(payload.report||'');
  const count=Math.max(1,parsed.round1.length+parsed.round2.length);
  const consensusCount=typeof payload.consensus_items==='number'?payload.consensus_items:parsed.consensus.length;
  const conflictCount=typeof payload.conflicts==='number'?payload.conflicts:parsed.conflicts.length;
  return {
    round1:parsed.round1,
    round2:parsed.round2,
    consensus:parsed.consensus,
    conflicts:parsed.conflicts,
    stats:{
      rounds:payload.rounds||2,
      arguments:payload.arguments||count,
      consensus_items:consensusCount||0,
      conflicts:conflictCount||0
    }
  };
}
function getPosMeta(position){
  const p=(position||'').toLowerCase();
  if(p.includes('agree')||p.includes('同意'))return{label:'同意',cls:'debate-pos-agree'};
  if(p.includes('disagree')||p.includes('反对'))return{label:'反对',cls:'debate-pos-disagree'};
  return{label:'延伸',cls:'debate-pos-extend'};
}
function renderDebateCards(id,list){
  const wrap=document.getElementById(id);
  if(!wrap)return;
  if(!list||!list.length){
    wrap.innerHTML='<div class="empty-state">暂无可展示辩论卡片</div>';
    return;
  }
  wrap.innerHTML=list.map(function(item,idx){
    const pos=getPosMeta(item.position);
    const target=item.target?'<div class="debate-target">target: '+escHtml(item.target)+'</div>':'';
    return '<div class="debate-card">'
      +'<div class="debate-card-head"><span class="debate-pos '+pos.cls+'">'+pos.label+'</span><span>'+escHtml(item.agent||'agent')+'</span><span>#'+(idx+1)+'</span></div>'
      +'<div class="debate-content">'+escHtml(item.content||'')+'</div>'
      +target
      +'</div>';
  }).join('');
}
function renderDebateStageBar(stats){
  const wrap=document.getElementById('debateStageBar');
  if(!wrap)return;
  const items=[
    {name:'Round 1',val:(stats&&stats.rounds)||2,hint:'首轮观点'},
    {name:'Round 2',val:(stats&&stats.arguments)||0,hint:'交叉辩论'},
    {name:'Consensus',val:(stats&&stats.consensus_items)||0,hint:'共识项'},
    {name:'Conflicts',val:(stats&&stats.conflicts)||0,hint:'冲突项'}
  ];
  wrap.innerHTML=items.map(function(item,idx){
    const cls=idx<3?'done':'active';
    return '<div class="debate-stage '+cls+'"><div>'+item.name+'</div><div style="font-family:var(--font-mono);font-size:.72rem;margin-top:2px">'+escHtml(item.val)+'</div><div style="font-size:.58rem;opacity:.72">'+item.hint+'</div></div>';
  }).join('');
}
function renderDebateSideLists(vm){
  const cons=document.getElementById('consensusList');
  const conf=document.getElementById('conflictList');
  if(cons){
    cons.innerHTML=(vm.consensus&&vm.consensus.length)
      ?vm.consensus.map(function(x){
        return '<div class="consensus-item"><div class="consensus-level">'+escHtml(x.id)+'</div><div class="consensus-text">'+escHtml(x.text||'')+'</div></div>';
      }).join('')
      :'<div class="empty-state">暂无共识分层数据</div>';
  }
  if(conf){
    conf.innerHTML=(vm.conflicts&&vm.conflicts.length)
      ?vm.conflicts.map(function(x){
        return '<div class="conflict-item"><div class="conflict-text">'+escHtml(x.text||'')+'</div></div>';
      }).join('')
      :'<div class="empty-state">暂无冲突记录</div>';
  }
}
function renderDebatePanel(){
  const vm=state.debateData||{round1:[],round2:[],consensus:[],conflicts:[],stats:{rounds:0,arguments:0,consensus_items:0,conflicts:0}};
  renderDebateStageBar(vm.stats||{});
  renderDebateCards('round1Cards',vm.round1||[]);
  renderDebateCards('round2Cards',vm.round2||[]);
  renderDebateSideLists(vm);
}

function copyReport(){navigator.clipboard.writeText(state.reportMarkdown).then(function(){showToast('已复制到剪贴板','success')})}
function downloadReport(){var b=new Blob([state.reportMarkdown],{type:'text/markdown'});var u=URL.createObjectURL(b);var a=document.createElement('a');a.href=u;a.download='roundtable-'+state.sessionId+'.md';a.click();URL.revokeObjectURL(u)}

/* ═══ LOADING ═══ */


/* ═══════════════════════════════════════════
   DEBATE THEATER V2
   ═══════════════════════════════════════════ */

let _currentInterruptType='question';

function setInterruptType(type){
  _currentInterruptType=type;
  document.querySelectorAll('#interruptBar .btn-ghost').forEach(function(b){
    b.style.borderColor='';
    b.style.background='';
  });
  const activeBtn=Array.from(document.querySelectorAll('#interruptBar .btn-ghost')).find(function(b){
    return b.getAttribute('onclick')&&b.getAttribute('onclick').includes(type);
  });
  if(activeBtn){
    activeBtn.style.borderColor='var(--accent)';
    activeBtn.style.background='var(--accent-soft)';
  }
}

async function sendInterrupt(){
  const input=document.getElementById('interruptInput');
  if(!input||!input.value.trim()){showToast('请输入插话内容','warning');return}
  if(!state.sessionId){showToast('会话不存在','error');return}
  try{
    const r=await apiFetch(API+'/session/'+encodeURIComponent(state.sessionId)+'/interrupt',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        session_id:state.sessionId,
        interrupt_type:_currentInterruptType,
        content:input.value.trim(),
        target_agent_id:''
      })
    });
    if(!r.ok){const e=await r.json();throw new Error(e.detail||e.error||'发送失败')}
    showToast('插话已发送','success');
    input.value='';
    // Refresh debate events
    setTimeout(function(){loadDebateEventsV2()},800);
  }catch(err){showToast('插话失败: '+err.message,'error')}
}

async function loadDebateEventsV2(){
  if(!state.sessionId)return;
  try{
    const r=await apiFetch(API+'/session/'+encodeURIComponent(state.sessionId)+'/events?limit=500');
    if(!r.ok)return;
    const d=await r.json();
    const events=d.events||[];
    if(!events.length)return;
    renderDebateV2(events);
  }catch(e){console.log('load debate events failed',e)}
}

function renderDebateV2(events){
  // Show V2 UI, hide V1 columns
  const timeline=document.getElementById('debateTimeline');
  const v2Steps=document.getElementById('debateV2Steps');
  const v1Cols=document.querySelector('.debate-columns');
  const interruptBar=document.getElementById('interruptBar');
  const scoreBlock=document.getElementById('consensusScoreBlock');
  if(timeline)timeline.style.display='block';
  if(v2Steps)v2Steps.style.display='flex';
  if(v1Cols)v1Cols.style.display='none';
  if(interruptBar)interruptBar.style.display='block';
  if(scoreBlock)scoreBlock.style.display='block';

  // Group events by step_type
  const byStep={statement:[],challenge:[],new_perspective:[],consensus:[],response:[]};
  let scores={};
  events.forEach(function(ev){
    const meta=ev.metadata||{};
    const type=ev.event_type||meta.step_type||'';
    if(byStep[type])byStep[type].push(ev);
    if(meta.dimension_scores){scores=meta.dimension_scores;}
  });

  // Update timeline dots
  document.querySelectorAll('.timeline-step').forEach(function(s){
    const step=s.dataset.step;
    const has=byStep[step]&&byStep[step].length>0;
    s.classList.toggle('done',has);
    s.classList.toggle('active',has);
  });

  // Render step cards
  renderV2StepCards('v2StatementCards',byStep.statement,'statement');
  renderV2StepCards('v2ChallengeCards',byStep.challenge,'challenge');
  renderV2StepCards('v2PerspectiveCards',byStep.new_perspective,'new_perspective');
  renderV2StepCards('v2ConsensusCards',byStep.consensus,'consensus');

  // Render consensus scores
  renderConsensusScores(scores);
}

function renderV2StepCards(containerId,list,stepType){
  const container=document.getElementById(containerId);
  if(!container)return;
  if(!list||!list.length){container.innerHTML='<div class="empty-state">暂无数据</div>';return}
  container.innerHTML=list.map(function(ev,idx){
    const meta=ev.metadata||{};
    const agent=ev.agent_id||meta.agent_id||'agent';
    const content=ev.content||meta.content||'';
    const cls=stepType==='challenge'?'agent-challenge':stepType==='new_perspective'?'agent-perspective':stepType==='consensus'?'agent-consensus':'';
    return'<div class="debate-v2-card '+cls+'">'+
      '<div class="debate-v2-head"><span>'+escHtml(agent)+'</span><span>#'+(idx+1)+'</span></div>'+
      '<div class="debate-v2-content">'+escHtml(content)+'</div>'+
      '</div>';
  }).join('');
}

function renderConsensusScores(scores){
  const container=document.getElementById('consensusScores');
  if(!container)return;
  const entries=Object.entries(scores||{});
  if(!entries.length){container.innerHTML='<div class="empty-state">暂无评分数据</div>';return}
  container.innerHTML=entries.map(function(e){
    const label=e[0];
    const val=Math.max(0,Math.min(100,Math.round((e[1]||0)*100)));
    return'<div class="consensus-score-row">'+
      '<div class="consensus-score-label">'+escHtml(label)+'</div>'+
      '<div class="consensus-score-bar"><div class="consensus-score-fill" style="width:'+val+'%"></div></div>'+
      '<div class="consensus-score-val">'+val+'%</div>'+
      '</div>';
  }).join('');
}

function scrollToDebateStep(stepType){
  const id='v2-'+stepType;
  const el=document.getElementById(id);
  if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
}

// Override renderDebatePanel to support V2
const _origRenderDebatePanel=renderDebatePanel;
renderDebatePanel=function(){
  _origRenderDebatePanel();
  loadDebateEventsV2();
};
