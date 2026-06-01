/* ═══════════════════════════════════════════
   INTERVIEW — Step 2.5 追问补充
   ═══════════════════════════════════════════ */
async function fetchInterview(){
  showLoading('生成追问...');
  try{
    const question=(typeof getPlainTextEvidence==='function'&&getPlainTextEvidence())||'请根据当前会议证据生成补充问题';
    const r=await fetch(API+'/roundtable/interview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:question,template:'general'})});
    if(!r.ok){
      let msg='Interview failed';
      try{const e=await r.json();msg=e.detail||e.error||msg}catch{}
      throw new Error(msg);
    }
    const d=await r.json();
    state.interviewQuestions=d.questions||[];
    renderInterviewQuestions();
    hideLoading();
    goStep(2.5);
  }catch(e){
    hideLoading();
    showToast('追问生成失败，跳过补充环节','error');
    goStep(3);
  }
}

function renderInterviewQuestions(){
  const wrap=document.getElementById('interviewQuestions');
  if(!wrap)return;
  if(!state.interviewQuestions||!state.interviewQuestions.length){
    wrap.innerHTML='<div class="empty-state">无需补充信息</div>';
    return;
  }
  wrap.innerHTML=state.interviewQuestions.map(function(q,i){
    return '<div class="interview-q">'
      +'<div class="interview-q-text">'+escHtml(q)+'</div>'
      +'<textarea class="interview-answer" data-idx="'+i+'" placeholder="请输入你的回答（可跳过）..."></textarea>'
      +'</div>';
  }).join('');
}

async function submitInterview(){
  const answers=Array.from(document.querySelectorAll('.interview-answer')).map(function(t){return t.value.trim()});
  const hasAnswers=answers.some(function(a){return a.length>0});
  if(!hasAnswers){
    goStep(3);
    return;
  }
  showLoading('提交补充信息...');
  try{
    const base=(typeof getPlainTextEvidence==='function'&&getPlainTextEvidence())||'';
    const additions=answers.map(function(text,i){return text?'User：追问回答 '+(i+1)+': '+text:''}).filter(Boolean);
    await fetch(API+'/evidence/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      session_id:state.sessionId,
      text:[base].concat(additions).filter(Boolean).join('\n')
    })});
    hideLoading();
    goStep(3);
  }catch(e){
    hideLoading();
    showToast('提交失败，继续下一步','error');
    goStep(3);
  }
}
