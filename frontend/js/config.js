/* ═══════════════════════════════════════════
   CONFIG
   ═══════════════════════════════════════════ */
const CLOUD_SVG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"><path d="M4 16C4 10 8 6 14 6C10 6 6 10 6 16"/><path d="M6 13C6 8.5 9 5 14 5"/></svg>';
const API = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://localhost:8000'
  : '';  // 同源部署时留空，所有请求走相对路径
const PIPE_IDS=['pipe-evidence','pipe-agents','pipe-review','pipe-memory','pipe-report'];
const REVIEW_PIPELINE=['构建证据','分派','审查','记忆','报告'];
const DEBATE_PIPELINE=['构建证据','Round 1','Round 2','共识','报告'];
let state={
