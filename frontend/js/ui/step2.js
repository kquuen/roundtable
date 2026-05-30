/* ═══════════════════════════════════════════
   STEP 2 — Evidence Upload
   ═══════════════════════════════════════════ */
function handleFileUpload(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{try{const j=JSON.parse(ev.target.result);if(j.segments){document.getElementById('transcriptEditor').value=JSON.stringify(j.segments,null,2);updateSegmentCount()}}catch{showToast('JSON parse failed','error')}};r.readAsText(f)}
function updateSegmentCount(){try{const s=JSON.parse(document.getElementById('transcriptEditor').value);document.getElementById('segmentCount').textContent=s.length+' 段'}catch{document.getElementById('segmentCount').textContent='—'}}
document.getElementById('transcriptEditor')?.addEventListener('input',updateSegmentCount);
async function uploadEvidence(){
  let s;try{s=JSON.parse(document.getElementById('transcriptEditor').value);if(!Array.isArray(s))throw 0}catch{showToast('Invalid JSON array','error');return}
  showLoading('Uploading evidence...');
  try{
    await fetch(API+'/evidence/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:state.sessionId,segments:s})});
    showLoading('Analyzing content...');
    const r=await fetch(API+'/team/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:state.sessionId,segments:s})});
    renderTeams(await r.json());hideLoading();fetchInterview();
  }catch(e){hideLoading();alert('Upload failed: '+e.message)}
}
function handleDragOver(e){e.preventDefault();e.currentTarget.classList.add('drag-over')}
function handleDragLeave(e){e.currentTarget.classList.remove('drag-over')}
function handleAudioDrop(e){
  e.preventDefault();e.currentTarget.classList.remove('drag-over');
  const files=e.dataTransfer&&e.dataTransfer.files;
  if(files&&files[0]){
    const input=document.getElementById('audioInput');
    input.files=files;
    handleAudioSelect({target:input});
  }
}
function handleJsonDrop(e){
  e.preventDefault();e.currentTarget.classList.remove('drag-over');
  const files=e.dataTransfer&&e.dataTransfer.files;
  if(files&&files[0]){
    const input=document.getElementById('fileInput');
    input.files=files;
    handleFileUpload({target:input});
  }
}
function handleAudioSelect(e){
  const file=e.target.files&&e.target.files[0];
  const name=file?file.name:'未选择音频文件';
  document.getElementById('audioFileName').textContent=name;
  document.getElementById('voiceStatus').textContent=file?'已选择文件':'等待上传';
}
function normalizeAudioSegments(segments){
  return (segments||[]).map(function(seg){
    return {
      speaker:seg.speaker||'Speaker',
      text:seg.text||''
    };
  }).filter(function(seg){return seg.text&&seg.text.trim()});
}
async function uploadAudioEvidence(){
  const input=document.getElementById('audioInput');
  const file=input&&input.files&&input.files[0];
  if(!file){showToast('请先选择音频文件','error');return}
  document.getElementById('voiceStatus').textContent='上传中';
  showLoading('Uploading audio...');
  try{
    const form=new FormData();
    form.append('audio',file);
    const r=await fetch(API+'/speak',{method:'POST',body:form});
    if(!r.ok){
      let msg='Audio upload failed';
      try{const e=await r.json();msg=e.detail||e.error||msg}catch{}
      throw new Error(msg);
    }
    const d=await r.json();
    if(d.error)throw new Error(d.detail||d.error);
    const segs=normalizeAudioSegments(d.segments);
    if(!segs.length)throw new Error('转写结果为空');
    if(d.session_id&&d.session_id!==state.sessionId){
      state.sessionId=d.session_id;
      document.getElementById('sessionId').textContent=d.session_id;
    }
    document.getElementById('transcriptEditor').value=JSON.stringify(segs,null,2);
    updateSegmentCount();
    document.getElementById('voiceStatus').textContent='转写完成';
    hideLoading();
    showLoading('Analyzing content...');
    const t=await fetch(API+'/team/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:state.sessionId,segments:segs})});
    if(!t.ok){
      let msg='Team recommend failed';
      try{const e=await t.json();msg=e.detail||e.error||msg}catch{}
      throw new Error(msg);
    }
    renderTeams(await t.json());
    hideLoading();
    fetchInterview();
  }catch(e){
    hideLoading();
    document.getElementById('voiceStatus').textContent='失败';
    showToast('语音处理失败: '+e.message,'error');
  }
}

/* ═══ STEP 3 ═══ */
