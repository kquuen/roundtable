/* ═══════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════ */

/* ═══ INIT ═══ */
syncPipelineLabels();
document.getElementById('tab-debate')?.classList.add('disabled');
showReportPanel('report',document.getElementById('tab-report'));
(async function(){try{var r=await fetch(API+'/health');var d=await r.json();if(d.status==='ok'){document.getElementById('apiStatus').textContent='connected'}}catch(e){document.getElementById('apiStatus').textContent='offline';document.querySelector('.status-pill').style.background='rgba(212,107,107,.06)';document.querySelector('.status-pill').style.borderColor='rgba(212,107,107,.15)';document.querySelector('.status-pill').style.color='var(--danger)'}})();
</script>
