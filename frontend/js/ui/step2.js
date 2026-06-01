/* ═══════════════════════════════════════════
   STEP 2 — Evidence Input
   ═══════════════════════════════════════════ */
function parsePlainTextSegments(text){
  return (text||'').split(/\n+/).map(function(line){
    const trimmed=line.trim();
    if(!trimmed)return null;
    const match=trimmed.match(/^([^:：]{1,24})[:：]\s*(.+)$/);
    return match?{speaker:match[1].trim(),text:match[2].trim()}:{speaker:'Speaker',text:trimmed};
  }).filter(function(seg){return seg&&seg.text});
}
function getPlainTextEvidence(){
  return document.getElementById('plainTextEditor')?.value.trim()||'';
}
function setPlainTextEvidence(text){
  const editor=document.getElementById('plainTextEditor');
  if(!editor)return;
  editor.value=text;
  updatePlainSegmentCount();
}
function appendPlainTextEvidence(line){
  const editor=document.getElementById('plainTextEditor');
  if(!editor||!line)return;
  const current=editor.value.trim();
  editor.value=current?current+'\n'+line:line;
  updatePlainSegmentCount();
}
function updatePlainSegmentCount(){
  const count=parsePlainTextSegments(getPlainTextEvidence()).length;
  const el=document.getElementById('plainSegmentCount');
  if(el)el.textContent=count+' 段';
}
document.getElementById('plainTextEditor')?.addEventListener('input',updatePlainSegmentCount);

async function uploadEvidence(){
  const text=getPlainTextEvidence();
  const segments=parsePlainTextSegments(text);
  if(!segments.length){showToast('请先实时说话、粘贴文本或导入 TXT','error');return}
  if(segments.length>500){showToast('Too many segments (max 500)','error');return}
  showLoading('Uploading evidence...');
  try{
    const r=await fetch(API+'/evidence/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:state.sessionId,text:text})});
    if(!r.ok){
      let msg='Upload failed';
      try{const e=await r.json();msg=e.detail||e.error||msg}catch{}
      throw new Error(msg);
    }
    const evidence=await r.json();
    const storedSegments=evidence.segments||segments;
    showLoading('Analyzing content...');
    const t=await fetch(API+'/team/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:state.sessionId,segments:storedSegments})});
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
    showToast('Upload failed: '+e.message,'error');
  }
}

function handleDragOver(e){e.preventDefault();e.currentTarget.classList.add('drag-over')}
function handleDragLeave(e){e.currentTarget.classList.remove('drag-over')}
function handleTextDrop(e){
  e.preventDefault();e.currentTarget.classList.remove('drag-over');
  const files=e.dataTransfer&&e.dataTransfer.files;
  if(files&&files[0]){
    const input=document.getElementById('fileInput');
    input.files=files;
    handleTextFileUpload({target:input});
  }
}
function handleTextFileUpload(e){
  const file=e.target.files&&e.target.files[0];
  if(!file)return;
  if(file.size>2*1024*1024){showToast('TXT 文件不能超过 2MB','error');return}
  const reader=new FileReader();
  reader.onload=function(ev){
    setPlainTextEvidence(String(ev.target.result||''));
    showToast('TXT 已导入','success');
  };
  reader.onerror=function(){showToast('TXT 读取失败','error')};
  reader.readAsText(file,'utf-8');
}

function buildVoiceSocketUrl(){
  if(API.startsWith('http://'))return API.replace('http://','ws://')+'/ws/voice';
  if(API.startsWith('https://'))return API.replace('https://','wss://')+'/ws/voice';
  const protocol=window.location.protocol==='https:'?'wss:':'ws:';
  return protocol+'//'+window.location.host+'/ws/voice';
}
function setRecordStatus(text){
  const el=document.getElementById('recordStatus');
  if(el)el.textContent=text;
}
function setRecordButtons(active){
  const start=document.getElementById('recordStartBtn');
  const stop=document.getElementById('recordStopBtn');
  if(start)start.disabled=active;
  if(stop)stop.disabled=!active;
}
function updateLiveTranscript(text){
  const el=document.getElementById('liveTranscript');
  if(!el)return;
  if(!state.liveSegments.length&&!text){
    el.textContent='实时转写内容会显示在这里';
    return;
  }
  el.textContent=(state.liveSegments.concat(text?['Speaker：'+text]:[])).join('\n');
}
function floatTo16BitPcm(float32){
  const output=new Int16Array(float32.length);
  for(let i=0;i<float32.length;i++){
    const s=Math.max(-1,Math.min(1,float32[i]));
    output[i]=s<0?s*0x8000:s*0x7fff;
  }
  return output;
}
function downsampleBuffer(buffer,inputRate,outputRate){
  if(outputRate===inputRate)return buffer;
  const ratio=inputRate/outputRate;
  const newLength=Math.round(buffer.length/ratio);
  const result=new Float32Array(newLength);
  let offsetResult=0;
  let offsetBuffer=0;
  while(offsetResult<result.length){
    const nextOffsetBuffer=Math.round((offsetResult+1)*ratio);
    let accum=0,count=0;
    for(let i=offsetBuffer;i<nextOffsetBuffer&&i<buffer.length;i++){accum+=buffer[i];count++}
    result[offsetResult]=count?accum/count:0;
    offsetResult++;
    offsetBuffer=nextOffsetBuffer;
  }
  return result;
}
function arrayBufferToBase64(buffer){
  let binary='';
  const bytes=new Uint8Array(buffer);
  const chunkSize=0x8000;
  for(let i=0;i<bytes.length;i+=chunkSize){
    binary+=String.fromCharCode.apply(null,bytes.subarray(i,i+chunkSize));
  }
  return btoa(binary);
}
function cleanupVoiceInput(){
  if(state.voiceProcessor){
    state.voiceProcessor.disconnect();
    state.voiceProcessor.onaudioprocess=null;
    state.voiceProcessor=null;
  }
  if(state.voiceSource){
    state.voiceSource.disconnect();
    state.voiceSource=null;
  }
  if(state.voiceAudioContext){
    state.voiceAudioContext.close();
    state.voiceAudioContext=null;
  }
  if(state.recordStream){
    state.recordStream.getTracks().forEach(function(track){track.stop()});
    state.recordStream=null;
  }
}
async function startRecordEvidence(){
  if(!state.sessionId){showToast('请先创建会话','error');return}
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
    showToast('当前浏览器不支持实时语音输入','error');
    return;
  }
  try{
    state.liveSegments=[];
    updateLiveTranscript('');
    setRecordStatus('连接中...');
    setRecordButtons(true);

    const socket=new WebSocket(buildVoiceSocketUrl());
    state.voiceSocket=socket;
    socket.onopen=function(){
      socket.send(JSON.stringify({type:'init',mode:'evidence',template:'general',session_id:state.sessionId}));
    };
    socket.onmessage=function(event){
      let msg;
      try{msg=JSON.parse(event.data)}catch{return}
      if(msg.type==='ready')setRecordStatus('已连接，准备收音');
      if(msg.type==='status'&&msg.state==='listening')setRecordStatus('实时收音中');
      if(msg.type==='transcript_final'&&msg.text){
        const line='Speaker：'+msg.text.trim();
        state.liveSegments.push(line);
        appendPlainTextEvidence(line);
        updateLiveTranscript('');
      }
      if(msg.type==='error'){
        setRecordStatus('失败');
        showToast(msg.message||'实时语音失败','error');
      }
    };
    socket.onerror=function(){
      setRecordStatus('连接失败');
      showToast('实时语音连接失败','error');
      stopRecordEvidence();
    };
    socket.onclose=function(){
      state.voiceSocket=null;
      cleanupVoiceInput();
      setRecordButtons(false);
      if(document.getElementById('recordStatus')?.textContent!=='失败')setRecordStatus('已停止');
    };

    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    state.recordStream=stream;
    const AudioCtx=window.AudioContext||window.webkitAudioContext;
    const audioContext=new AudioCtx();
    state.voiceAudioContext=audioContext;
    const source=audioContext.createMediaStreamSource(stream);
    const processor=audioContext.createScriptProcessor(4096,1,1);
    state.voiceSource=source;
    state.voiceProcessor=processor;
    state.voiceSeq=0;
    processor.onaudioprocess=function(e){
      if(!state.voiceSocket||state.voiceSocket.readyState!==WebSocket.OPEN)return;
      const input=e.inputBuffer.getChannelData(0);
      const downsampled=downsampleBuffer(input,audioContext.sampleRate,16000);
      const pcm=floatTo16BitPcm(downsampled);
      state.voiceSocket.send(JSON.stringify({type:'audio',data:arrayBufferToBase64(pcm.buffer),seq:state.voiceSeq++}));
    };
    source.connect(processor);
    processor.connect(audioContext.destination);
  }catch(e){
    if(state.voiceSocket){
      state.voiceSocket.close();
      state.voiceSocket=null;
    }
    cleanupVoiceInput();
    setRecordButtons(false);
    setRecordStatus('失败');
    showToast('无法开始实时语音: '+e.message,'error');
  }
}
function stopRecordEvidence(){
  const socket=state.voiceSocket;
  if(socket&&socket.readyState===WebSocket.OPEN){
    socket.send(JSON.stringify({type:'commit'}));
    socket.send(JSON.stringify({type:'close',reason:'user_done'}));
    socket.close();
  }else if(socket){
    socket.close();
  }
  state.voiceSocket=null;
  cleanupVoiceInput();
  setRecordButtons(false);
  setRecordStatus('已停止');
}

/* Compatibility hooks for older markup/tests. These are no longer visible in the default UI. */
function handleFileUpload(e){handleTextFileUpload(e)}
function handleJsonDrop(e){handleTextDrop(e)}
function updateSegmentCount(){updatePlainSegmentCount()}
async function uploadAudioEvidence(){showToast('请使用实时语音输入','error')}
async function submitRecordedAudio(){showToast('请使用实时语音输入','error')}

/* ═══ STEP 3 ═══ */
