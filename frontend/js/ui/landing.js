/* ═══════════════════════════════════════════
   LANDING — Taiji ink-wash canvas + click
   ═══════════════════════════════════════════ */
(function(){
  var canvas=document.getElementById('taijiCanvas');
  var ctx=canvas.getContext('2d');
  var W,H,cx,cy,R,rotation=0,animId,fc=0;

  function noise(a,b,c){return Math.sin(a*1.7+b*3.1+c*2.3)*.5+Math.sin(a*3.7-b*1.9+c*4.1)*.3+Math.cos(a*2.1+b*4.3-c*1.7)*.2}
  function isDark(){return document.documentElement.getAttribute('data-theme')!=='light'}

  // Particles
  var sParts=[],eParts=[];

  function initP(){
    sParts=[];eParts=[];
    for(var i=0;i<100;i++){
      sParts.push({t:Math.random(),ph:Math.random()*6.28,r:.5+Math.random()*3.5,sp:.0002+Math.random()*.0012,wb:.02+Math.random()*.08,al:.1+Math.random()*.6});
    }
    for(var i=0;i<25;i++){
      eParts.push({a:Math.random()*6.28,d:R*(.15+Math.random()*.85),r:.3+Math.random()*1.8,sp:.0008+Math.random()*.003,rd:.05+Math.random()*.25,al:.08+Math.random()*.25,tr:[]});
    }
  }

  function resize(){
    W=canvas.width=window.innerWidth;H=canvas.height=window.innerHeight;
    cx=W/2;cy=H/2;R=Math.min(W,H)*.42;
    initP();
  }

  // ═══ THE KEY: brush strokes, not circles ═══
  // Each stroke is a thick arc segment with noise and blur.
  // Combined, they form the Taiji — like brush painting, not geometry.

  function strokeArc(ox,oy,startA,endA,baseR,nS,noiseAmp,seed,blur,color,lw){
    ctx.save();
    ctx.shadowBlur=blur;
    ctx.shadowColor=color.replace(/[\d.]+\)$/,function(m){return parseFloat(m)*.5+')'});
    ctx.strokeStyle=color;
    ctx.lineWidth=lw;
    ctx.lineCap='round';
    ctx.beginPath();
    var segs=Math.max(12,Math.abs(endA-startA)/.04|0);
    for(var i=0;i<=segs;i++){
      var t=i/segs;
      var a=startA+(endA-startA)*t;
      var w=baseR+noise(a*nS,seed,t*3)*noiseAmp;
      var x=ox+Math.cos(a)*w,y=oy+Math.sin(a)*w;
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawTaiji(rot,t){
    ctx.save();ctx.translate(cx,cy);
    var r=R,d=isDark();

    // Ink palette
    var darkInk=d?'rgba(190,185,175,':'rgba(20,15,8,';
    var lightInk=d?'rgba(230,226,218,':'rgba(240,235,225,';
    var midDark=d?'rgba(160,155,145,':'rgba(40,32,22,';
    var midLight=d?'rgba(210,206,198,':'rgba(200,195,185,';
    var accentC=d?'rgba(196,122,90,':'rgba(181,102,64,';

    // ─ DARK HALF strokes (left side: PI/2 to 3PI/2) ─
    // These build the dark mass with layered brush strokes
    for(var i=0;i<22;i++){
      var frac=i/22;
      var startA=Math.PI*.5+frac*Math.PI;
      var endA=startA+.15+frac*.25;
      var rad=r*(.3+.65*frac);
      var nS=2+frac*3;
      var nAmp=r*(.015+frac*.03);
      var blur=r*(.06+frac*.08);
      var alpha=.06+frac*.08;
      var lw=r*(.08+.12*(1-frac));
      // Shift center for S-curve shape
      var sx=Math.cos(startA)*r*.15;
      var sy=Math.sin(startA)*r*.15;
      strokeArc(sx,sy,startA,endA,rad,nS,nAmp,t*.00005+i*.7,blur,darkInk+alpha+')',lw);
    }
    // Inner dark strokes for density
    for(var i=0;i<8;i++){
      var frac=i/8;
      var startA=Math.PI*.7+frac*Math.PI*.6;
      var endA=startA+.1+frac*.15;
      var rad=r*(.15+.35*frac);
      var blur=r*.04;
      var alpha=.08+frac*.06;
      var lw=r*(.06+.08*(1-frac));
      strokeArc(0,0,startA,endA,rad,3,r*.01,t*.00008+i*1.3,blur,midDark+alpha+')',lw);
    }

    // ─ LIGHT HALF strokes (right side: -PI/2 to PI/2) ─
    for(var i=0;i<22;i++){
      var frac=i/22;
      var startA=-Math.PI*.5+frac*Math.PI;
      var endA=startA+.15+frac*.25;
      var rad=r*(.3+.65*frac);
      var nS=2+frac*3;
      var nAmp=r*(.015+frac*.03);
      var blur=r*(.06+frac*.08);
      var alpha=.06+frac*.08;
      var lw=r*(.08+.12*(1-frac));
      var sx=-Math.cos(startA)*r*.15;
      var sy=-Math.sin(startA)*r*.15;
      strokeArc(sx,sy,startA,endA,rad,nS,nAmp,t*.00005+i*.7+11,blur,lightInk+alpha+')',lw);
    }
    for(var i=0;i<8;i++){
      var frac=i/8;
      var startA=-Math.PI*.3+frac*Math.PI*.6;
      var endA=startA+.1+frac*.15;
      var rad=r*(.15+.35*frac);
      var blur=r*.04;
      var alpha=.08+frac*.06;
      var lw=r*(.06+.08*(1-frac));
      strokeArc(0,0,startA,endA,rad,3,r*.01,t*.00008+i*1.3+5,blur,midLight+alpha+')',lw);
    }

    // ─ S-CURVE divider strokes ─
    // Strokes that trace the wavy S boundary
    for(var i=0;i<6;i++){
      var frac=i/6;
      // Upper S
      var sA=-Math.PI*.5+frac*Math.PI;
      var eA=sA+.2;
      var rad=r*(.48+noise(frac*5,t*.0001,i)*.04);
      strokeArc(0,0,sA,eA,rad,4,r*.02,t*.0001+i*2,r*.03,midDark+'.06)',r*.02);
      // Lower S
      var sA2=Math.PI*.5+frac*Math.PI;
      var eA2=sA2+.2;
      strokeArc(0,0,sA2,eA2,rad,4,r*.02,t*.0001+i*2+7,r*.03,midLight+'.06)',r*.02);
    }

    // ─ FISH EYES — ink vortices ─
    // Yang eye (dark in light half)
    ctx.save();
    ctx.shadowBlur=r*.08;
    ctx.shadowColor=d?'rgba(60,56,48,.2)':'rgba(0,0,0,.18)';
    for(var i=0;i<5;i++){
      var a=i*Math.PI*2/5+t*.001;
      var wr=r*.08+noise(a,2,t*.0003)*r*.015;
      ctx.beginPath();
      ctx.arc(Math.cos(a)*r*.01,-r*.45+Math.sin(a)*r*.01,wr,0,6.28);
      ctx.fillStyle=d?'rgba(150,145,135,.2)':'rgba(10,5,0,.18)';ctx.fill();
    }
    ctx.restore();
    // Yin eye (light in dark half)
    ctx.save();
    ctx.shadowBlur=r*.08;
    ctx.shadowColor=d?'rgba(200,196,188,.15)':'rgba(60,50,35,.1)';
    for(var i=0;i<5;i++){
      var a=i*Math.PI*2/5+t*.001+3;
      var wr=r*.08+noise(a,5,t*.0003)*r*.015;
      ctx.beginPath();
      ctx.arc(Math.cos(a)*r*.01,r*.45+Math.sin(a)*r*.01,wr,0,6.28);
      ctx.fillStyle=d?'rgba(220,216,208,.2)':'rgba(240,235,225,.18)';ctx.fill();
    }
    ctx.restore();

    // ─ Outer glow (very subtle) ─
    ctx.save();
    ctx.shadowBlur=r*.3;
    ctx.shadowColor=d?'rgba(180,175,165,.06)':'rgba(20,15,8,.04)';
    ctx.beginPath();ctx.arc(0,0,r*.02,0,6.28);
    ctx.fillStyle='rgba(0,0,0,0.001)';ctx.fill();
    ctx.restore();

    ctx.restore();
  }

  // ── Spiral flow particles ──
  function drawSpiral(t){
    var d=isDark();
    var ink=d?'#b8b4ac':'#1a1410';
    var acc=d?'#da9474':'#c47a5a';

    for(var i=0;i<sParts.length;i++){
      var p=sParts[i];
      p.t=(p.t+p.sp)%1;
      p.ph+=.01;
      // Spiral path around Taiji
      var a=p.t*12.56+rotation; // 2 full rotations
      var spiralR=R*(.05+.85*p.t);
      var wb=Math.sin(p.ph)*p.wb*R;
      var px=cx+Math.cos(a)*(spiralR+wb);
      var py=cy+Math.sin(a)*(spiralR+wb);
      // Check if inside screen
      if(px<-20||px>W+20||py<-20||py>H+20)continue;
      ctx.beginPath();ctx.arc(px,py,p.r,0,6.28);
      if(Math.random()>.95){ctx.fillStyle=acc;ctx.globalAlpha=p.al*.3;}
      else{ctx.fillStyle=ink;ctx.globalAlpha=p.al*.35;}
      ctx.fill();
    }
    ctx.globalAlpha=1;
  }

  // ── Energy glow ──
  function drawEnergy(t){
    var d=isDark();
    var acc=d?'rgba(218,148,116,':'rgba(196,122,90,';
    for(var i=0;i<eParts.length;i++){
      var p=eParts[i];
      p.a+=p.sp;
      p.d+=Math.sin(t*.0008+i*2)*p.rd;
      if(p.d>R)p.d=R*.15;if(p.d<R*.1)p.d=R*.5;
      var px=cx+Math.cos(p.a+rotation*.3)*p.d;
      var py=cy+Math.sin(p.a+rotation*.3)*p.d;
      p.tr.push({x:px,y:py,a:p.al});
      if(p.tr.length>6)p.tr.shift();
      for(var j=0;j<p.tr.length;j++){
        var tr=p.tr[j];
        ctx.beginPath();ctx.arc(tr.x,tr.y,p.r*(j+1)/p.tr.length,0,6.28);
        ctx.fillStyle=acc+(tr.a*j/p.tr.length*.25)+')';ctx.fill();
      }
    }
  }

  // ── Main loop ──
  function tick(){
    fc++;
    var d=isDark();
    // Motion trail: semi-transparent overlay
    ctx.fillStyle=d?'rgba(8,10,16,.04)':'rgba(240,236,228,.04)';
    ctx.fillRect(0,0,W,H);

    rotation+=.0002; // ~52s per revolution

    drawTaiji(rotation,fc);
    drawSpiral(fc);
    drawEnergy(fc);

    animId=requestAnimationFrame(tick);
  }

  function clearAll(){
    var d=isDark();
    ctx.fillStyle=d?'#080a10':'#f0ece4';
    ctx.fillRect(0,0,W,H);
  }

  window._taijiStart=function(){resize();clearAll();tick()};
  window._taijiStop=function(){cancelAnimationFrame(animId)};
  window.addEventListener('resize',function(){if(animId){cancelAnimationFrame(animId);resize();clearAll();tick()}});
})();

/* ═══ LANDING CLICK — bamboo scroll transition ═══ */
(function(){
  document.getElementById('landing').addEventListener('click',function(e){
    if(e.target.closest('.icon-btn'))return;
    this.classList.add('scroll-open');
    document.getElementById('app').classList.add('visible');
    if(window._taijiStop)window._taijiStop();
    setTimeout(()=>{this.style.display='none'},800);
  });
  if(window._taijiStart)window._taijiStart();
})();

/* ═══ THEME ═══ */
