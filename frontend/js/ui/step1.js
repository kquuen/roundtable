/* ═══════════════════════════════════════════
   STEP 1 — Create Session
   ═══════════════════════════════════════════ */
function selectMode(m,el){state.mode=m;document.querySelectorAll('.mode-card').forEach(c=>c.classList.remove('selected'));el.classList.add('selected')}
async function createSession(){
  const t=document.getElementById('sessionTitle').value.trim();
  if(!t){document.getElementById('sessionTitle').style.borderColor='var(--danger)';return}
  showLoading('Creating session...');
  try{
    const r=await fetch(API+'/session/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:t,mode:state.mode})});
    const d=await r.json();state.sessionId=d.session_id;
    document.getElementById('sessionId').textContent=d.session_id;document.getElementById('sessionStatus').textContent=d.status;
    hideLoading();goStep(2);
  }catch(e){hideLoading();showToast('Backend connection failed','error')}
}

/* ═══ STEP 2 ═══ */
