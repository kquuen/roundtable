/* ═══════════════════════════════════════════
   NAVIGATION
   ═══════════════════════════════════════════ */
function goStep(n){
  const dir=n>prevStep?'forward':'back';
  prevStep=n;
  state.currentStep=n;
  document.querySelectorAll('.page').forEach(p=>{
    p.classList.remove('active','slide-forward','slide-back');
  });
  const pageId='page'+String(n).replace('.','_');
  const target=document.getElementById(pageId);
  if(target){
    target.classList.add('active','slide-'+dir);
    target.addEventListener('animationend',function handler(){
      target.classList.remove('slide-forward','slide-back');
      target.removeEventListener('animationend',handler);
    });
  }
  document.querySelectorAll('.step-item').forEach(s=>{
    const sn=+s.dataset.step;
    s.classList.toggle('active',sn===n);
    s.classList.toggle('completed',sn<n);
  });
}
function goAdmin(e){
  if(e)e.preventDefault();
  prevStep=state.currentStep;
  state.currentStep='admin';
  document.querySelectorAll('.page').forEach(p=>{p.classList.remove('active','slide-forward','slide-back');});
  const target=document.getElementById('page_admin');
  if(target){
    target.classList.add('active','slide-forward');
    target.addEventListener('animationend',function handler(){
      target.classList.remove('slide-forward','slide-back');
      target.removeEventListener('animationend',handler);
    });
  }
  document.querySelectorAll('.step-item').forEach(s=>s.classList.remove('active','completed'));
  showAdminTab('health',document.querySelector('.admin-tab'));
}
function setLang(l){state.lang=l;document.querySelectorAll('.header .lang-btn').forEach(b=>{b.classList.toggle('active',b.textContent.trim()===(l==='zh'?'中':'EN'))})}
function setRunLang(l,b){state.runLang=l;b.parentElement.querySelectorAll('.lang-btn').forEach(x=>x.classList.remove('active'));b.classList.add('active')}
