/* ═══════════════════════════════════════════
   THEME
   ═══════════════════════════════════════════ */
function toggleTheme(e){
  if(e)e.stopPropagation();
  const html=document.documentElement;
  const next=html.getAttribute('data-theme')==='dark'?'light':'dark';
  html.setAttribute('data-theme',next);
  document.querySelectorAll('.icon-btn').forEach(b=>{b.textContent=next==='dark'?'☀':'☾'});
  localStorage.setItem('rt-theme',next);
}
(function(){const s=localStorage.getItem('rt-theme');if(s){document.documentElement.setAttribute('data-theme',s);document.querySelectorAll('.icon-btn').forEach(b=>{b.textContent=s==='dark'?'☀':'☾'})}})();
