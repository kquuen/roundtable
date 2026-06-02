/* ═══════════════════════════════════════════
   STEP 3 — Agent Group Recommendation (V2)
   ═══════════════════════════════════════════ */

/* Legacy team radar chart renderer (kept for fallback) */
function renderTeams(data){
  document.getElementById('sessionTypeText').textContent=data.session_type||'综合分析';
  const g=document.getElementById('teamGrid');g.innerHTML='';
  const teams=data.recommended_teams||[];
  if(!teams.length){g.innerHTML='<div class="team-card" style="grid-column:1/-1;text-align:center;padding:2rem;">暂无推荐团队</div>';return}
  teams.forEach((team,i)=>{
    const c=document.createElement('div');c.className='team-card'+(i===0?' recommended selected':'');c.setAttribute('role','button');c.setAttribute('tabindex','0');c.setAttribute('aria-label','Select team: '+escHtml(team.name));
    if(i===0)state.selectedTeam=team.team_id;
    const rid='radar-'+team.team_id;
    c.innerHTML='<span class="cloud-corner cloud-tl">'+CLOUD_SVG+'</span><span class="cloud-corner cloud-br">'+CLOUD_SVG+'</span><div class="team-name">'+escHtml(team.name)+'</div><div class="team-id">'+escHtml(team.team_id)+'</div><div class="radar-wrap"><canvas id="'+rid+'"></canvas></div><div class="score-list">'+Object.entries(team.capability_scores||{}).map(function(e){return'<div class="score-row"><span class="score-label">'+escHtml(e[0])+'</span><div class="score-bar"><div class="score-fill" style="width:'+e[1]+'%"></div></div><span class="score-val">'+e[1]+'</span></div>'}).join('')+'</div>';
    c.onclick=function(){document.querySelectorAll('.team-card').forEach(function(x){x.classList.remove('selected')});c.classList.add('selected');state.selectedTeam=team.team_id};
    g.appendChild(c);requestAnimationFrame(function(){drawRadar(rid,team.capability_scores||{})});
  });
}

function drawRadar(id,scores){
  const canvas=document.getElementById(id);if(!canvas)return;
  const ctx=canvas.getContext('2d'),labels=Object.keys(scores),values=Object.values(scores),n=labels.length;
  const size=canvas.parentElement.clientWidth;canvas.width=size*2;canvas.height=size*2;canvas.style.width=size+'px';canvas.style.height=size+'px';ctx.scale(2,2);
  const cx=size/2,cy=size/2,r=size*.36;
  const accent=getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  const grid=getComputedStyle(document.documentElement).getPropertyValue('--border').trim();
  const dim=getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim();
  for(let ring=1;ring<=4;ring++){const rr=r*ring/4;ctx.beginPath();for(let i=0;i<=n;i++){const a=Math.PI*2*i/n-Math.PI/2;const x=cx+rr*Math.cos(a),y=cy+rr*Math.sin(a);i===0?ctx.moveTo(x,y):ctx.lineTo(x,y)}ctx.strokeStyle=grid;ctx.lineWidth=.6;ctx.stroke()}
  for(let i=0;i<n;i++){const a=Math.PI*2*i/n-Math.PI/2;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+r*Math.cos(a),cy+r*Math.sin(a));ctx.strokeStyle=grid;ctx.lineWidth=.4;ctx.stroke()}
  ctx.beginPath();for(let i=0;i<=n;i++){const idx=i%n;const a=Math.PI*2*idx/n-Math.PI/2;const v=values[idx]/100;const x=cx+r*v*Math.cos(a),y=cy+r*v*Math.sin(a);i===0?ctx.moveTo(x,y):ctx.lineTo(x,y)}ctx.fillStyle=accent+'18';ctx.fill();ctx.strokeStyle=accent+'b0';ctx.lineWidth=1.2;ctx.stroke();
  for(let i=0;i<n;i++){const a=Math.PI*2*i/n-Math.PI/2;const v=values[i]/100;ctx.beginPath();ctx.arc(cx+r*v*Math.cos(a),cy+r*v*Math.sin(a),2.5,0,Math.PI*2);ctx.fillStyle=accent;ctx.fill()}
  ctx.fillStyle=dim;ctx.font='7px "Noto Sans SC",sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
  for(let i=0;i<n;i++){const a=Math.PI*2*i/n-Math.PI/2;ctx.fillText(labels[i],cx+(r+14)*Math.cos(a),cy+(r+14)*Math.sin(a))}
}

/* ═══════════════════════════════════════════
   V2: Agent Match & Group Confirmation
   ═══════════════════════════════════════════ */

function renderAgentGroups(data){
  state.agentMatchData=data;
  const container=document.getElementById('teamGrid');
  if(!container)return;
  container.innerHTML='';

  // Keywords chip
  const keywords=data.extracted_keywords||[];
  const kwHtml=keywords.length?'<div class="match-keywords"><span class="match-label">提取关键词:</span> '+keywords.map(function(k){return'<span class="keyword-chip">'+escHtml(k)+'</span>'}).join('')+'</div>':'';

  // Matched agents list
  const matched=data.matched_agents||[];
  let agentsHtml='<div class="matched-agents">';
  agentsHtml+='<div class="match-section-title">匹配到的专家 <span class="match-count">('+matched.length+')</span></div>';
  agentsHtml+='<div class="agent-match-list">';
  matched.forEach(function(m){
    const a=m.agent||{};
    const scorePct=Math.round((m.final_score||0)*100);
    const scoreClass=scorePct>=50?'high':scorePct>=30?'medium':'low';
    agentsHtml+='<div class="agent-match-item" data-agent-id="'+escHtml(a.id||'')+'">';
    agentsHtml+='<div class="agent-match-header">';
    agentsHtml+='<span class="agent-emoji">'+(a.emoji||'🤖')+'</span>';
    agentsHtml+='<span class="agent-match-name">'+escHtml(a.name||'')+'</span>';
    agentsHtml+='<span class="agent-match-score '+scoreClass+'">'+scorePct+'%</span>';
    agentsHtml+='</div>';
    if(m.matched_keywords&&m.matched_keywords.length){
      agentsHtml+='<div class="agent-match-kws">命中: '+m.matched_keywords.map(function(k){return'<span class="match-kw">'+escHtml(k)+'</span>'}).join('')+'</div>';
    }
    if(m.reason){
      agentsHtml+='<div class="agent-match-reason">'+escHtml(m.reason)+'</div>';
    }
    agentsHtml+='</div>';
  });
  agentsHtml+='</div></div>';

  // Groups
  const groups=data.groups||[];
  let groupsHtml='<div class="group-recommendations">';
  groupsHtml+='<div class="match-section-title">建议分组 <span class="match-count">('+groups.length+')</span></div>';
  groupsHtml+='<div class="group-list">';
  groups.forEach(function(grp,idx){
    const grpAgents=grp.agents||[];
    groupsHtml+='<div class="group-card'+(idx===0?' selected':'')+'" data-group-id="'+escHtml(grp.group_id||'')+'" onclick="selectGroup(this)">';
    groupsHtml+='<div class="group-card-header">';
    groupsHtml+='<span class="group-name">'+escHtml(grp.group_name||'')+'</span>';
    groupsHtml+='<span class="group-topic">'+escHtml(grp.topic||'')+'</span>';
    groupsHtml+='</div>';
    groupsHtml+='<div class="group-agents">';
    grpAgents.forEach(function(a){
      groupsHtml+='<span class="group-agent-tag" title="'+escHtml(a.methodology||'')+'">'+(a.emoji||'🤖')+' '+escHtml(a.name||'')+'</span>';
    });
    groupsHtml+='</div>';
    if(grp.rationale){
      groupsHtml+='<div class="group-rationale">'+escHtml(grp.rationale)+'</div>';
    }
    groupsHtml+='</div>';
  });
  groupsHtml+='</div></div>';

  // Ungrouped notice
  let ungroupedHtml='';
  if(data.ungrouped_reason){
    ungroupedHtml='<div class="ungrouped-notice">'+escHtml(data.ungrouped_reason)+'</div>';
  }

  container.innerHTML=kwHtml+agentsHtml+groupsHtml+ungroupedHtml;

  // Auto-select first group
  if(groups.length){
    state.selectedGroup=groups[0].group_id;
  }
}

function selectGroup(el){
  document.querySelectorAll('.group-card').forEach(function(c){c.classList.remove('selected')});
  el.classList.add('selected');
  state.selectedGroup=el.dataset.groupId;
}

async function confirmTeam(){
  if(!state.sessionId){showToast('会话不存在','error');return}
  if(!state.selectedGroup){showToast('请先选择一个分组','error');return}

  showLoading('确认分组中...');
  try{
    const r=await apiFetch(API+'/agents/confirm-group',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        session_id:state.sessionId,
        group_selections:[state.selectedGroup],
        agent_adjustments:{}
      })
    });
    if(!r.ok){
      let msg='确认失败';
      try{const e=await r.json();msg=e.detail||e.error||msg}catch{}
      throw new Error(msg);
    }
    const result=await r.json();
    showToast('分组已确认','success');
    hideLoading();
    goStep(4);
  }catch(e){
    hideLoading();
    showToast('确认失败: '+e.message,'error');
  }
}

/* Compatibility: if old renderTeams is called with V1 shape, still works */
