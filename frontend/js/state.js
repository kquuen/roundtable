/* ═══════════════════════════════════════════
   STATE
   ═══════════════════════════════════════════ */
let state={
  sessionId:null,
  mode:'meeting',
  lang:'zh',
  runLang:'zh',
  currentStep:1,
  selectedTeam:null,
  reportMarkdown:'',
  analysisMode:'review',
  reportType:'review',
  debateData:null,
  recordStream:null,
  voiceSocket:null,
  voiceAudioContext:null,
  voiceSource:null,
  voiceProcessor:null,
  voiceSeq:0,
  liveSegments:[]
};

/* ═══ NAV ═══ */
let prevStep=1;
