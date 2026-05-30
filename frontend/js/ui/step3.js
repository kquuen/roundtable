/* ═══════════════════════════════════════════
   STEP 3 — Team Recommendation
   ═══════════════════════════════════════════ */
function renderTeams(data){
  document.getElementById('sessionTypeText').textContent=data.session_type;
  const g=document.getElementById('teamGrid');g.innerHTML='';
  data.recommended_teams.forEach((team,i)=>{
    const c=document.createElement('div');c.className='team-card'+(i===0?' recommended selected':'');
    if(i===0)state.selectedTeam=team.team_id;
    const rid='radar-'+team.team_id;
    c.innerHTML='<span class="cloud-corner cloud-tl">'+CLOUD_SVG+'</span><span class="cloud-corner cloud-br">'+CLOUD_SVG+'</span><div class="team-name">'+team.name+'</div><div class="team-id">'+team.team_id+'</div><div class="radar-wrap"><canvas id="'+rid+'"></canvas></div><div class="score-list">'+Object.entries(team.capability_scores).map(function(e){return'<div class="score-row"><span class="score-label">'+e[0]+'</span><div class="score-bar"><div class="score-fill" style="width:'+e[1]+'%"></div></div><span class="score-val">'+e[1]+'</span></div>'}).join('')+'</div>';
    c.onclick=function(){document.querySelectorAll('.team-card').forEach(function(x){x.classList.remove('selected')});c.classList.add('selected');state.selectedTeam=team.team_id};
    g.appendChild(c);requestAnimationFrame(function(){drawRadar(rid,team.capability_scores)});
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
