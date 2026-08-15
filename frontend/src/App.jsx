import { useState, useEffect, useRef, useCallback } from "react";
import "./App.css";
import NexusShell from "./NexusShell.jsx";

const API = import.meta.env.VITE_API_URL || "http://localhost:8765";
const WS_URL = import.meta.env.VITE_WS_URL || `${API.replace(/^http/i, "ws")}/ws`;

const PROVIDER_LABELS = {
  ollama_local:"🖥  Ollama Local", ollama_cloud:"☁️  Ollama Cloud",
  google:"🔷  Google Gemini",      openai:"🟢  ChatGPT / OpenAI",
};
const PROVIDER_COLORS = {
  ollama_local:"#6366f1", ollama_cloud:"#8b5cf6",
  google:"#3b82f6",       openai:"#22c55e",
};
const STATUS_ICON  = {pending:"○",in_progress:"◉",completed:"✓",skipped:"⊘"};
const STATUS_COLOR = {pending:"#374151",in_progress:"#f59e0b",completed:"#22c55e",skipped:"#6b7280"};
const ENV_LABELS = {internal:"Internal",external:"External",hybrid:"Hybrid"};
const ENV_COLORS = {internal:"#06b6d4",external:"#f97316",hybrid:"#22c55e"};

function Typewriter({text,speed=12}){
  const [d,setD]=useState("");
  useEffect(()=>{
    setD(""); if(!text) return;
    let i=0; const t=setInterval(()=>{setD(text.slice(0,++i));if(i>=text.length)clearInterval(t);},speed);
    return()=>clearInterval(t);
  },[text]);
  return <span>{d}</span>;
}

/* ── Settings Panel ─────────────────────────────────────────────────────── */
function SettingsPanel({onClose}){
  const [cfg,setCfg]=useState(null);
  const [keys,setKeys]=useState({ollama_cloud_key:"",google_api_key:"",openai_api_key:""});
  const [saved,setSaved]=useState(false);

  useEffect(()=>{ fetch(`${API}/config`).then(r=>r.json()).then(setCfg); },[]);

  const save=async()=>{
    const p={...cfg};
    for(const k of Object.keys(keys)){if(keys[k])p[k]=keys[k];}
    await fetch(`${API}/config`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});
    setSaved(true); setTimeout(()=>setSaved(false),2000);
  };

  if(!cfg) return<Modal onClose={onClose} title="Settings"><div style={{padding:24,color:"#64748b"}}>Loading…</div></Modal>;

  const tog=(val,onChange,label,sub)=>(
    <div style={{marginBottom:12}}>
      <div onClick={()=>onChange(!val)} style={{display:"flex",alignItems:"center",gap:8,cursor:"pointer"}}>
        <div style={{width:27,height:15,borderRadius:8,background:val?"#6366f1":"#1a1f2e",
          position:"relative",transition:"0.2s",flexShrink:0}}>
          <div style={{position:"absolute",top:1.5,left:val?13.5:1.5,width:12,height:12,
            borderRadius:"50%",background:"white",transition:"0.2s"}}/>
        </div>
        <span style={{fontSize:11,color:val?"#a5b4fc":"#4b5563"}}>{label}</span>
      </div>
      {sub&&val&&<div style={{marginLeft:35,marginTop:6}}>{sub}</div>}
    </div>
  );
  const inp=(val,onChange,ph="",type="text")=>(
    <input type={type} value={val} onChange={e=>onChange(e.target.value)} placeholder={ph}
      style={{width:"100%",background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:5,
        color:"#e2e8f0",padding:"5px 9px",fontSize:11.5,outline:"none",
        fontFamily:"inherit",boxSizing:"border-box"}}/>
  );
  const row=(label,ch)=>(
    <div style={{marginBottom:12}}>
      <label style={{fontSize:9,color:"#374151",textTransform:"uppercase",letterSpacing:1}}>{label}</label>
      <div style={{marginTop:4}}>{ch}</div>
    </div>
  );
  const sel=(val,onChange,options)=>(
    <select value={val} onChange={e=>onChange(e.target.value)}
      style={{width:"100%",background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:5,
        color:"#e2e8f0",padding:"6px 9px",fontSize:11.5,outline:"none",fontFamily:"inherit"}}>
      {options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
  const sldr=(val,onChange,min,max,step=1,labels)=>(
    <>
      <input type="range" min={min} max={max} step={step} value={val}
        onChange={e=>onChange(+e.target.value)} style={{width:"100%",accentColor:"#6366f1",marginTop:4}}/>
      {labels&&<div style={{display:"flex",justifyContent:"space-between",
        fontSize:9,color:"#374151",marginTop:1}}>{labels.map((l,i)=><span key={i}>{l}</span>)}</div>}
    </>
  );
  const sec=(label,color="#6366f1")=>(
    <div style={{fontSize:11,fontWeight:700,color,marginBottom:10,marginTop:18,
      textTransform:"uppercase",letterSpacing:1}}>{label}</div>
  );

  return(
    <Modal onClose={onClose} title="⚙️ Settings" width={640}>
      <div style={{padding:"14px 20px",overflowY:"auto",maxHeight:"75vh"}}>
        {sec("🔑 API Keys","#6366f1")}
        {row("Ollama Cloud Key",inp(keys.ollama_cloud_key,v=>setKeys(k=>({...k,ollama_cloud_key:v})),cfg.ollama_cloud_key||"Enter key…","password"))}
        {row("Ollama Cloud URL",inp(cfg.ollama_cloud_url||"",v=>setCfg(c=>({...c,ollama_cloud_url:v}))))}
        {row("Google Gemini Key",inp(keys.google_api_key,v=>setKeys(k=>({...k,google_api_key:v})),cfg.google_api_key||"Enter key…","password"))}
        {row("OpenAI Key",inp(keys.openai_api_key,v=>setKeys(k=>({...k,openai_api_key:v})),cfg.openai_api_key||"Enter key…","password"))}

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("🔄 Fallback","#f59e0b")}
        {tog(cfg.fallback_enabled||false,v=>setCfg(c=>({...c,fallback_enabled:v})),
          "Auto-switch provider on failure / rate limit",
          cfg.fallback_order&&<div style={{padding:"5px 9px",background:"#0a0c12",
            border:"1px solid #1a1f2e",borderRadius:5,fontSize:10,color:"#4b5563"}}>
            Order: {cfg.fallback_order.join(" → ")}
          </div>
        )}

        {sec("Ollama request queue","#8b5cf6")}
        {tog(cfg.ollama_queue_enabled!==false,v=>setCfg(c=>({...c,ollama_queue_enabled:v})),
          "Queue and pace Ollama requests",
          <>
            {row("Minimum delay between requests",
              <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>{cfg.ollama_queue_min_interval_seconds??6}s</div>
              {sldr(cfg.ollama_queue_min_interval_seconds??6,v=>setCfg(c=>({...c,ollama_queue_min_interval_seconds:v})),0,30,1,["0s","6s","15s","30s"])}</>)}
            {row("Rate-limit retries",
              <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>{cfg.ollama_queue_max_retries??3} retries</div>
              {sldr(cfg.ollama_queue_max_retries??3,v=>setCfg(c=>({...c,ollama_queue_max_retries:v})),0,6,1,["0","2","4","6"])}</>)}
          </>
        )}
        {tog(cfg.resume_incomplete_on_startup!==false,v=>setCfg(c=>({...c,resume_incomplete_on_startup:v})),
          "Resume interrupted bot tasks when the server starts")}

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("🔄 Adaptive Replanning","#06b6d4")}
        {row("Replan every N steps (0 = off)",
          <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>
            Every {cfg.replan_interval||8} steps the agent reflects and may restructure tasks
          </div>
          {sldr(cfg.replan_interval||8,v=>setCfg(c=>({...c,replan_interval:v})),0,20,1,
            ["Off","4","8","12","20"])}</>
        )}

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("⏱ Stop Conditions","#22c55e")}
        {tog(cfg.max_time_enabled||false,v=>setCfg(c=>({...c,max_time_enabled:v})),"Stop after time limit",
          <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>Max: {cfg.max_time_minutes||30} min</div>
            {sldr(cfg.max_time_minutes||30,v=>setCfg(c=>({...c,max_time_minutes:v})),1,120,1,["1m","30m","60m","120m"])}</>
        )}
        {tog(cfg.max_steps_enabled||false,v=>setCfg(c=>({...c,max_steps_enabled:v})),"Stop after N steps",
          <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>Max: {cfg.max_steps||100} steps</div>
            {sldr(cfg.max_steps||100,v=>setCfg(c=>({...c,max_steps:v})),10,200,5,["10","50","100","200"])}</>
        )}
        {tog(cfg.stuck_detection||false,v=>setCfg(c=>({...c,stuck_detection:v})),"Stuck detection",
          <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>Trigger after {cfg.stuck_threshold||3}× same action</div>
            {sldr(cfg.stuck_threshold||3,v=>setCfg(c=>({...c,stuck_threshold:v})),2,8,1,["2","4","6","8"])}</>
        )}

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("📄 Page Reading","#06b6d4")}
        <div style={{marginBottom:12}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
            <span style={{fontSize:11,color:"#64748b"}}>Max text/step:</span>
            <span style={{fontSize:12,color:"#06b6d4",fontWeight:700}}>{(cfg.max_text_chars||8000).toLocaleString()} chars</span>
          </div>
          {sldr(cfg.max_text_chars||8000,v=>setCfg(c=>({...c,max_text_chars:v})),2000,50000,1000,["2K","8K","20K","50K"])}
          <div style={{marginTop:5,padding:"4px 8px",background:"#0a0c12",border:"1px solid #1a1f2e",
            borderRadius:4,fontSize:9,color:"#374151"}}>
            💡 Local qwen3:4b → keep under 8K · Gemini / GPT-4o → up to 30K+
          </div>
        </div>
        {tog(cfg.deep_read||false,v=>setCfg(c=>({...c,deep_read:v})),"Deep read — scroll full page")}
        {tog(cfg.ocr_enabled||false,v=>setCfg(c=>({...c,ocr_enabled:v})),"OCR images (Tesseract required)",
          <div style={{padding:"5px 9px",background:"#0a0c12",border:"1px solid #f59e0b33",
            borderRadius:4,fontSize:9.5,color:"#f59e0b"}}>
            pip install pytesseract Pillow + Tesseract binary
          </div>
        )}
        {tog(cfg.pdf_enabled||false,v=>setCfg(c=>({...c,pdf_enabled:v})),"PDF extraction (pip install pymupdf)")}

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("💻 Local Computer Access","#22c55e")}
        {tog(cfg.local_file_access_enabled!==false,v=>setCfg(c=>({...c,local_file_access_enabled:v})),
          "Allow agent to read local files and folders")}
        {tog(cfg.local_file_write_enabled!==false,v=>setCfg(c=>({...c,local_file_write_enabled:v})),
          "Allow agent to create/edit/delete local files and folders")}
        {row("Filesystem Scope",sel(cfg.filesystem_scope||"workspace",v=>setCfg(c=>({...c,filesystem_scope:v})),[
          {value:"workspace",label:"Workspace only"},
          {value:"full_computer",label:"Full computer access"},
        ]))}
        {row("Filesystem Root",inp(cfg.filesystem_root||"",v=>setCfg(c=>({...c,filesystem_root:v})),
          "Blank = project root; used for relative paths"))}
        <div style={{padding:"6px 9px",background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:5,
          fontSize:10,color:"#64748b",marginBottom:12}}>
          Relative file paths are resolved from the filesystem root. In <strong style={{color:"#e2e8f0"}}>Workspace only</strong> mode,
          the agent cannot access paths outside that root.
        </div>

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("🖥️ Desktop Control","#ef4444")}
        {tog(cfg.desktop_automation_enabled||false,v=>setCfg(c=>({...c,desktop_automation_enabled:v})),
          "Allow the agent to inspect and control software outside the browser")}
        {row("Desktop Execution Mode",sel(cfg.desktop_execution_mode||"manual",v=>setCfg(c=>({...c,desktop_execution_mode:v})),[
          {value:"disabled",label:"Disabled"},
          {value:"manual",label:"Manual approval style"},
          {value:"auto",label:"Full autonomy"},
        ]))}
        {row("Autonomy Scope",sel(cfg.desktop_autonomy_scope||"browser_only",v=>setCfg(c=>({...c,desktop_autonomy_scope:v})),[
          {value:"browser_only",label:"Browser only"},
          {value:"browser_and_desktop",label:"Browser + desktop"},
        ]))}
        <div style={{padding:"6px 9px",background:"#0a0c12",border:"1px solid #ef444433",borderRadius:5,
          fontSize:10,color:"#64748b",marginBottom:12}}>
          <strong style={{color:"#e2e8f0"}}>Browser only</strong> prevents autonomous runs from leaving the browser even if desktop tools exist.
          <strong style={{color:"#e2e8f0"}}> Manual approval style</strong> blocks autonomous mouse, keyboard, window, and app-launch actions,
          but still lets you trigger them through manual actions. <strong style={{color:"#f59e0b"}}>Full autonomy</strong> allows real desktop control.
        </div>

        <div style={{height:1,background:"#1a1f2e",margin:"14px 0"}}/>
        {sec("⌨️ Command Execution","#f97316")}
        {row("Command Mode",sel(cfg.command_execution_mode||"manual",v=>setCfg(c=>({...c,command_execution_mode:v})),[
          {value:"disabled",label:"Disabled"},
          {value:"manual",label:"Manual only"},
          {value:"auto",label:"Full autonomy"},
        ]))}
        <div style={{padding:"6px 9px",background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:5,
          fontSize:10,color:"#64748b",marginBottom:12}}>
          <strong style={{color:"#e2e8f0"}}>Manual only</strong> means the agent can propose commands but will not auto-run them during autonomous work.
          You can still run a command yourself via the manual action API. <strong style={{color:"#f59e0b"}}>Full autonomy</strong> lets the agent execute commands on its own.
        </div>
        {row("Command Timeout (seconds)",
          <><div style={{fontSize:11,color:"#64748b",marginBottom:4}}>{cfg.command_timeout_seconds||120}s timeout</div>
          {sldr(cfg.command_timeout_seconds||120,v=>setCfg(c=>({...c,command_timeout_seconds:v})),10,600,10,["10","120","300","600"])}</>
        )}

        <div style={{marginTop:18,display:"flex",justifyContent:"flex-end",gap:8}}>
          <button onClick={onClose} style={{padding:"7px 14px",background:"#1a1f2e",
            border:"1px solid #2d3748",borderRadius:5,color:"#64748b",cursor:"pointer",
            fontSize:11,fontFamily:"inherit"}}>Cancel</button>
          <button onClick={save} style={{padding:"7px 14px",border:"none",borderRadius:5,
            background:saved?"#22c55e":"linear-gradient(135deg,#6366f1,#8b5cf6)",
            color:"white",cursor:"pointer",fontSize:11,fontWeight:600,fontFamily:"inherit"}}>
            {saved?"✓ Saved!":"Save Settings"}</button>
        </div>
      </div>
    </Modal>
  );
}




/* ── Inject Task Panel ──────────────────────────────────────────────────── */
function InjectPanel({isRunning,onInject}){
  const [val,setVal]=useState("");
  const [priority,setPriority]=useState("next");
  const [sent,setSent]=useState(false);
  const submit=()=>{
    if(!val.trim()||!isRunning)return;
    onInject(val.trim(),priority);
    setSent(true);setVal("");
    setTimeout(()=>setSent(false),2000);
  };
  if(!isRunning)return null;
  return(
    <div style={{padding:"8px 12px",borderBottom:"1px solid #1a1f2e",flexShrink:0,background:"#070b0e"}}>
      <label style={{fontSize:9,color:"#f97316",textTransform:"uppercase",letterSpacing:1}}>
        ➕ INJECT TASK (live)
      </label>
      <textarea value={val} onChange={e=>setVal(e.target.value)}
        placeholder="Add a new task for the agent right now…" rows={2}
        onKeyDown={e=>{if(e.key==="Enter"&&e.ctrlKey)submit();}}
        style={{marginTop:4,width:"100%",background:"#0a0c12",border:"1px solid #f9741640",
          borderRadius:5,color:"#e2e8f0",padding:"5px 8px",fontSize:10.5,resize:"none",
          outline:"none",boxSizing:"border-box",fontFamily:"inherit"}}/>
      <div style={{display:"flex",gap:5,marginTop:5,alignItems:"center"}}>
        <select value={priority} onChange={e=>setPriority(e.target.value)}
          style={{background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:4,
            color:"#64748b",padding:"3px 6px",fontSize:9.5,outline:"none"}}>
          <option value="next">Next (after current)</option>
          <option value="last">Last (at end)</option>
        </select>
        <button onClick={submit} disabled={!val.trim()}
          style={{flex:1,padding:"4px 0",borderRadius:4,border:"none",
            background:sent?"#22c55e":val.trim()?"#f97316":"#1a1f2e",
            color:"white",cursor:val.trim()?"pointer":"not-allowed",
            fontSize:10,fontWeight:600,fontFamily:"inherit",transition:"0.2s"}}>
          {sent?"✓ Added!":"Ctrl+Enter"}
        </button>
      </div>
    </div>
  );
}

/* ── Task Checklist ─────────────────────────────────────────────────────── */
function TaskChecklist({tasks,progress,isPlanning,isReplanning,replanMsg,taskEnvironment,currentTaskDescription}){
  if(isPlanning)return(
    <div style={{padding:"8px 12px",borderBottom:"1px solid #1a1f2e",flexShrink:0}}>
      <label style={{fontSize:9,color:"#f59e0b",textTransform:"uppercase",letterSpacing:1}}>🧠 PLANNING…</label>
      <div style={{display:"flex",alignItems:"center",gap:6,marginTop:4}}>
        <div style={{width:7,height:7,borderRadius:"50%",background:"#f59e0b",animation:"pulse 0.8s infinite"}}/>
        <span style={{fontSize:10,color:"#64748b"}}>Breaking down your goal…</span>
      </div>
    </div>
  );
  const done=(tasks||[]).filter(t=>t.status==="completed"||t.status==="skipped").length;
  const total=(tasks||[]).length;
  const pct=total>0?Math.round((done/total)*100):0;
  return(
    <div style={{flexShrink:0,borderBottom:"1px solid #1a1f2e",background:"#050a0d"}}>
      <div style={{padding:"7px 12px 3px"}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <label style={{fontSize:9,color:"#06b6d4",textTransform:"uppercase",letterSpacing:1}}>📋 TASK PLAN</label>
          <span style={{fontSize:9,color:"#374151"}}>{done}/{total} · {pct}%</span>
        </div>
        {taskEnvironment&&(
          <div style={{display:"flex",alignItems:"center",gap:6,marginTop:4}}>
            <span style={{fontSize:8.5,color:ENV_COLORS[taskEnvironment]||"#94a3b8",textTransform:"uppercase",letterSpacing:0.8}}>
              {ENV_LABELS[taskEnvironment]||taskEnvironment} environment
            </span>
            {currentTaskDescription&&(
              <span style={{fontSize:8.5,color:"#475569",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                {currentTaskDescription}
              </span>
            )}
          </div>
        )}
        <div style={{marginTop:4,height:3,background:"#1a1f2e",borderRadius:2,overflow:"hidden"}}>
          <div style={{height:"100%",width:`${pct}%`,background:"linear-gradient(90deg,#06b6d4,#22c55e)",
            borderRadius:2,transition:"width 0.5s"}}/>
        </div>
      </div>
      {(isReplanning||replanMsg)&&(
        <div style={{margin:"3px 12px 5px",padding:"4px 8px",background:"#f59e0b0a",
          border:"1px solid #f59e0b30",borderRadius:4,fontSize:9.5,color:"#f59e0b",
          display:"flex",alignItems:"center",gap:5}}>
          <span style={{animation:isReplanning?"pulse 1s infinite":"none"}}>🔄</span>
          <span>{isReplanning?"Reflecting on plan…":replanMsg}</span>
        </div>
      )}
      <div style={{maxHeight:220,overflowY:"auto",padding:"3px 12px 8px"}}>
        {(tasks||[]).map((t)=>{
          const isCur=t.status==="in_progress";
          const col=STATUS_COLOR[t.status]||"#374151";
          return(
            <div key={t.id} style={{marginBottom:3,padding:"4px 6px",borderRadius:4,
              background:isCur?"#f59e0b08":t.status==="completed"?"#22c55e06":"transparent",
              border:`1px solid ${isCur?"#f59e0b25":t.status==="completed"?"#22c55e18":"transparent"}`}}>
              <div style={{display:"flex",alignItems:"flex-start",gap:5}}>
                <span style={{color:col,fontSize:10.5,flexShrink:0,marginTop:1,
                  animation:isCur?"pulse 1.2s infinite":"none"}}>{STATUS_ICON[t.status]||"○"}</span>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{fontSize:9.5,color:t.status==="completed"?"#94a3b8":"#c9d1d9",
                    lineHeight:1.4,textDecoration:t.status==="skipped"?"line-through":"none"}}>
                    {t.description}
                  </div>
                  {t.finding&&(
                    <div style={{fontSize:9,color:"#22c55e",marginTop:1,lineHeight:1.4,fontStyle:"italic"}}>
                      → {t.finding}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Candidates Panel ────────────────────────────────────────────────────── */
function CandidatesPanel({candidates,task}){
  if(!candidates||candidates.length===0)return null;
  return(
    <div style={{flexShrink:0,borderBottom:"1px solid #1a1f2e",background:"#030809"}}>
      <div style={{padding:"5px 12px 2px",display:"flex",alignItems:"center",gap:5}}>
        <label style={{fontSize:9,color:"#f59e0b",textTransform:"uppercase",letterSpacing:1}}>🎯 CANDIDATES</label>
        {task&&<span style={{fontSize:8,color:"#374151",overflow:"hidden",textOverflow:"ellipsis",
          whiteSpace:"nowrap",flex:1}}>— {task}</span>}
      </div>
      <div style={{padding:"0 12px 6px",display:"flex",flexDirection:"column",gap:2,maxHeight:150,overflowY:"auto"}}>
        {candidates.map((c,i)=>{
          const tried=c.tried||(c.score<0);
          const isTop=c.rank===1&&!tried;
          return(
            <div key={i} style={{padding:"3px 6px",borderRadius:3,
              background:tried?"#1a0808":isTop?"#f59e0b06":"transparent",
              border:`1px solid ${tried?"#ef444415":isTop?"#f59e0b25":"#1a1f2e"}`,opacity:tried?0.5:1}}>
              <div style={{display:"flex",alignItems:"center",gap:4}}>
                <span style={{fontSize:8,fontWeight:700,
                  color:tried?"#ef4444":isTop?"#f59e0b":"#374151",flexShrink:0,width:12,textAlign:"center"}}>
                  #{c.rank}</span>
                <span style={{fontSize:8.5,color:"#374151",fontFamily:"monospace",flexShrink:0}}>&lt;{c.tag}&gt;</span>
                <span style={{fontSize:9.5,color:tried?"#4b1010":isTop?"#e2e8f0":"#64748b",
                  overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",flex:1,
                  textDecoration:tried?"line-through":"none"}}>{c.text||"(no text)"}</span>
                <span style={{fontSize:8,color:"#1f2937",flexShrink:0}}>({c.x},{c.y})</span>
                {tried&&<span style={{fontSize:8,color:"#ef4444",flexShrink:0}}>✗</span>}
              </div>
              {c.reasons&&c.reasons.length>0&&(
                <div style={{fontSize:7.5,color:"#374151",marginLeft:18,marginTop:1}}>
                  {c.reasons.slice(0,3).join(" · ")}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


/* ── Profiles Panel ─────────────────────────────────────────────────────── */
function ProfilesPanel({onClose}){
  const [profiles, setProfiles]   = useState([]);
  const [newName,  setNewName]    = useState("");
  const [msg,      setMsg]        = useState("");
  const [loading,  setLoading]    = useState(false);
  const [sources,  setSources]    = useState([]);
  const [sourceId, setSourceId]   = useState("");
  const [importName,setImportName]= useState("");
  const [overwrite,setOverwrite]  = useState(false);
  const [useAfter,setUseAfter]    = useState(true);
  const [importing,setImporting]  = useState(false);

  const load = async () => {
    const r = await fetch(`${API}/profiles`);
    const d = await r.json();
    setProfiles(d.profiles || []);
  };

  const loadSources = async () => {
    const r = await fetch(`${API}/profiles/sources`);
    const d = await r.json();
    const detected = d.sources || [];
    setSources(detected);
    const firstCompatible = detected.find(s=>s.compatible);
    if (!sourceId && firstCompatible) {
      setSourceId(firstCompatible.id);
      setImportName(firstCompatible.recommended_name || "");
    }
  };

  useEffect(()=>{ load(); loadSources(); }, []);

  const flash = (m) => { setMsg(m); setTimeout(()=>setMsg(""), 3500); };

  const selectedSource = sources.find(s=>s.id===sourceId);

  const chooseSource = (id) => {
    setSourceId(id);
    const src = sources.find(s=>s.id===id);
    if (src) setImportName(src.recommended_name || "");
  };

  const switchProfile = async (name) => {
    setLoading(true);
    const r = await fetch(`${API}/profiles/switch`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name})
    });
    const d = await r.json();
    setLoading(false);
    flash(`✅ Switched to "${name}" — restart backend to apply`);
    load();
  };

  const createProfile = async () => {
    if (!newName.trim()) return;
    await switchProfile(newName.trim());
    setNewName("");
  };

  const clearProfile = async (name) => {
    if (!window.confirm(`Clear all saved sessions for "${name}"? You will be logged out everywhere on this profile.`)) return;
    setLoading(true);
    const r = await fetch(`${API}/profiles/clear`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name})
    });
    const d = await r.json();
    setLoading(false);
    flash(`🗑 Cleared sessions for "${name}"`);
    load();
  };

  const importProfile = async () => {
    if (!selectedSource?.compatible || !importName.trim()) return;
    if (overwrite && !window.confirm(`Overwrite project profile "${importName.trim()}"?`)) return;
    setImporting(true);
    try {
      const r = await fetch(`${API}/profiles/import`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          source_id: sourceId,
          target_name: importName.trim(),
          overwrite,
          use_after_import: useAfter,
        })
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || "Import failed");
      flash(`Imported "${d.name}" (${d.copied_files} files). Restart backend to use it.`);
      load();
      loadSources();
    } catch (e) {
      flash(e.message || "Import failed");
    } finally {
      setImporting(false);
    }
  };

  return (
    <Modal onClose={onClose} title="🗂 Browser Profiles & Sessions" width={560}>
      <div style={{padding:"14px 20px", overflowY:"auto", maxHeight:"75vh"}}>

        <div style={{padding:"10px 14px", background:"#0a1628", border:"1px solid #1e3a5f",
          borderRadius:7, fontSize:11, color:"#93c5fd", lineHeight:1.7, marginBottom:18}}>
          <strong style={{color:"#60a5fa"}}>How it works:</strong><br/>
          Each profile saves its own cookies, sessions and login state to disk.
          If you log into Gmail on the <em>default</em> profile, it stays logged in
          across backend restarts — just like a normal browser.<br/><br/>
          Create separate profiles to keep different accounts isolated
          (e.g. "work", "personal", "client-a").
          <br/><br/>
          <strong style={{color:"#fbbf24"}}>⚠ After switching profiles, restart the backend.</strong>
        </div>

        {msg && (
          <div style={{padding:"8px 12px", background:"#0d2a0d", border:"1px solid #196119",
            borderRadius:6, color:"#86efac", fontSize:11, marginBottom:14}}>
            {msg}
          </div>
        )}

        {/* Import local browser profile */}
        <div style={{marginBottom:18,padding:"10px 12px",background:"#0a0c12",
          border:"1px solid #1a1f2e",borderRadius:7}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:8}}>
            <label style={{fontSize:9,color:"#f97316",textTransform:"uppercase",letterSpacing:1,flex:1}}>
              IMPORT FROM THIS COMPUTER
            </label>
            <button onClick={loadSources} disabled={importing}
              style={{padding:"4px 9px",borderRadius:4,border:"1px solid #2d3748",
                background:"#111827",color:"#94a3b8",cursor:"pointer",fontSize:10,fontFamily:"inherit"}}>
              Refresh
            </button>
          </div>
          <div style={{fontSize:10,color:"#64748b",lineHeight:1.6,marginBottom:8}}>
            Chrome, Edge, Brave, Chromium, Vivaldi and Opera profiles can be copied into a project profile.
            Close the source browser first for the best chance of bringing cookies and logged-in sessions across.
            Firefox and Safari are detected for visibility, but cannot be used directly by this Chromium-based agent.
          </div>
          <div style={{display:"grid",gridTemplateColumns:"1.4fr 1fr auto",gap:8,alignItems:"end"}}>
            <div>
              <label style={{fontSize:8,color:"#374151",textTransform:"uppercase",letterSpacing:1}}>Source</label>
              <select value={sourceId} onChange={e=>chooseSource(e.target.value)}
                style={{marginTop:4,width:"100%",background:"#05070b",border:"1px solid #1a1f2e",
                  borderRadius:5,color:"#e2e8f0",padding:"6px 8px",fontSize:10.5,outline:"none"}}>
                {sources.length===0&&<option value="">No local profiles detected</option>}
                {sources.map(s=>(
                  <option key={s.id} value={s.id}>
                    {s.browser} / {s.profile} {s.compatible ? "" : "(not compatible)"}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label style={{fontSize:8,color:"#374151",textTransform:"uppercase",letterSpacing:1}}>Project profile name</label>
              <input value={importName} onChange={e=>setImportName(e.target.value)}
                placeholder="e.g. chrome_work"
                style={{marginTop:4,width:"100%",background:"#05070b",border:"1px solid #1a1f2e",
                  borderRadius:5,color:"#e2e8f0",padding:"6px 8px",fontSize:10.5,outline:"none",
                  fontFamily:"inherit"}}/>
            </div>
            <button onClick={importProfile}
              disabled={!selectedSource?.compatible||!importName.trim()||importing}
              style={{padding:"7px 12px",borderRadius:5,border:"none",
                background:selectedSource?.compatible&&importName.trim()&&!importing?"#f97316":"#1a1f2e",
                color:"white",cursor:selectedSource?.compatible&&importName.trim()&&!importing?"pointer":"not-allowed",
                fontSize:10.5,fontWeight:700,fontFamily:"inherit"}}>
              {importing?"Importing...":"Import"}
            </button>
          </div>
          {selectedSource&&(
            <div style={{marginTop:7,fontSize:9.5,color:selectedSource.compatible?"#94a3b8":"#f59e0b",
              wordBreak:"break-all",lineHeight:1.5}}>
              {selectedSource.note}<br/>
              {selectedSource.path}
            </div>
          )}
          <div style={{display:"flex",gap:14,marginTop:8,flexWrap:"wrap"}}>
            <label style={{display:"flex",alignItems:"center",gap:6,fontSize:10,color:"#64748b",cursor:"pointer"}}>
              <input type="checkbox" checked={useAfter} onChange={e=>setUseAfter(e.target.checked)}/>
              Use after import
            </label>
            <label style={{display:"flex",alignItems:"center",gap:6,fontSize:10,color:"#64748b",cursor:"pointer"}}>
              <input type="checkbox" checked={overwrite} onChange={e=>setOverwrite(e.target.checked)}/>
              Overwrite existing target
            </label>
          </div>
        </div>

        {/* Profile list */}
        <div style={{marginBottom:18}}>
          <label style={{fontSize:9,color:"#374151",textTransform:"uppercase",letterSpacing:1}}>
            SAVED PROFILES
          </label>
          <div style={{marginTop:8, display:"flex", flexDirection:"column", gap:6}}>
            {profiles.length === 0 && (
              <div style={{color:"#374151", fontSize:11, padding:"8px 0"}}>No profiles yet</div>
            )}
            {profiles.map(p => (
              <div key={p.name} style={{
                padding:"9px 12px", borderRadius:7,
                background: p.active ? "#6366f118" : "#0a0c12",
                border: `1px solid ${p.active ? "#6366f160" : "#1a1f2e"}`,
                display:"flex", alignItems:"center", gap:10
              }}>
                <div style={{flex:1}}>
                  <div style={{display:"flex", alignItems:"center", gap:7}}>
                    <span style={{fontSize:12, color: p.active ? "#a5b4fc" : "#e2e8f0",
                      fontWeight: p.active ? 700 : 400}}>
                      {p.active ? "▶ " : ""}{p.name}
                    </span>
                    {p.active && (
                      <span style={{fontSize:9, background:"#6366f130", color:"#a5b4fc",
                        padding:"1px 6px", borderRadius:8, border:"1px solid #6366f140"}}>
                        ACTIVE
                      </span>
                    )}
                    {p.has_sessions && (
                      <span style={{fontSize:9, background:"#22c55e18", color:"#86efac",
                        padding:"1px 6px", borderRadius:8, border:"1px solid #22c55e30"}}>
                        🔐 sessions saved
                      </span>
                    )}
                  </div>
                  <div style={{fontSize:9, color:"#374151", marginTop:2}}>{p.path}</div>
                </div>
                <div style={{display:"flex", gap:5}}>
                  {!p.active && (
                    <button onClick={()=>switchProfile(p.name)} disabled={loading}
                      style={{padding:"4px 10px", borderRadius:4, border:"1px solid #6366f140",
                        background:"#6366f118", color:"#a5b4fc", cursor:"pointer",
                        fontSize:10, fontFamily:"inherit"}}>
                      Use
                    </button>
                  )}
                  <button onClick={()=>clearProfile(p.name)} disabled={loading}
                    style={{padding:"4px 10px", borderRadius:4, border:"1px solid #ef444430",
                      background:"#ef444410", color:"#f87171", cursor:"pointer",
                      fontSize:10, fontFamily:"inherit"}}>
                    Clear
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Create new profile */}
        <div>
          <label style={{fontSize:9,color:"#374151",textTransform:"uppercase",letterSpacing:1}}>
            CREATE NEW PROFILE
          </label>
          <div style={{display:"flex", gap:8, marginTop:6}}>
            <input value={newName} onChange={e=>setNewName(e.target.value)}
              onKeyDown={e=>e.key==="Enter"&&createProfile()}
              placeholder="e.g. work, personal, client-a"
              style={{flex:1, background:"#0a0c12", border:"1px solid #1a1f2e",
                borderRadius:5, color:"#e2e8f0", padding:"6px 9px",
                fontSize:11, outline:"none", fontFamily:"inherit"}}/>
            <button onClick={createProfile} disabled={!newName.trim()||loading}
              style={{padding:"6px 14px", borderRadius:5, border:"none",
                background: newName.trim() ? "linear-gradient(135deg,#6366f1,#8b5cf6)" : "#1a1f2e",
                color:"white", cursor: newName.trim() ? "pointer" : "not-allowed",
                fontSize:11, fontWeight:600, fontFamily:"inherit"}}>
              Create
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function Modal({onClose,title,width=720,children}){
  return(
    <div style={{position:"fixed",inset:0,background:"#000000cc",display:"flex",
      alignItems:"center",justifyContent:"center",zIndex:1000,padding:24,backdropFilter:"blur(4px)"}}>
      <div style={{background:"#0d1117",border:"1px solid #30363d",borderRadius:12,
        width:"100%",maxWidth:width,maxHeight:"88vh",display:"flex",flexDirection:"column",
        boxShadow:"0 24px 80px #0008"}}>
        <div style={{padding:"11px 18px",borderBottom:"1px solid #21262d",
          display:"flex",alignItems:"center",gap:10}}>
          <span style={{fontSize:13,fontWeight:700,color:"#e6edf3"}}>{title}</span>
          <div style={{flex:1}}/>
          <button onClick={onClose} style={{background:"none",border:"none",
            color:"#8b949e",cursor:"pointer",fontSize:18,lineHeight:1}}>✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ── Results Modal ────────────────────────────────────────────────────────── */
function ResultsModal({result,onClose,onRestart,onRunAgain}){
  const [copied,setCopied]=useState(false);
  const copy=()=>{navigator.clipboard.writeText(result.report||"");setCopied(true);setTimeout(()=>setCopied(false),2000);};
  const done=(result.tasks||[]).filter(t=>t.status==="completed").length;
  const tot=(result.tasks||[]).length;
  return(
    <Modal onClose={onClose} title="✅ Session Complete">
      <div style={{padding:"5px 18px 4px",borderBottom:"1px solid #21262d"}}>
        <span style={{fontSize:10,color:"#8b949e"}}>
          {result.goal?.slice(0,60)} · {result.steps} steps · {result.elapsed} · Tasks {done}/{tot}
          {result.filename&&<span style={{color:"#6366f1"}}> · {result.filename}</span>}
        </span>
      </div>
      {result.tasks&&result.tasks.length>0&&(
        <div style={{padding:"8px 18px",borderBottom:"1px solid #21262d",display:"flex",flexWrap:"wrap",gap:5}}>
          {result.tasks.map(t=>(
            <div key={t.id} style={{padding:"2px 8px",borderRadius:10,fontSize:9,
              background:`${STATUS_COLOR[t.status]}15`,border:`1px solid ${STATUS_COLOR[t.status]}35`,
              color:STATUS_COLOR[t.status]}}>
              {STATUS_ICON[t.status]} {t.description.slice(0,40)}
            </div>
          ))}
        </div>
      )}
      <div style={{flex:1,overflow:"auto",padding:16}}>
        <div style={{background:"#161b22",border:"1px solid #21262d",borderRadius:7,
          padding:14,fontSize:12,color:"#c9d1d9",lineHeight:1.8,whiteSpace:"pre-wrap",fontFamily:"inherit"}}>
          {result.report||result.message}
        </div>
      </div>
      <div style={{padding:"10px 18px",borderTop:"1px solid #21262d",display:"flex",gap:8,flexWrap:"wrap"}}>
        <button onClick={copy} style={{padding:"5px 12px",borderRadius:5,background:"#21262d",
          border:"1px solid #30363d",color:"#c9d1d9",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>
          {copied?"✓ Copied":"📋 Copy"}</button>
        {result.filename&&<div style={{padding:"5px 12px",borderRadius:5,background:"#0d2a0d",
          border:"1px solid #196119",color:"#56d364",fontSize:11}}>💾 {result.filename}</div>}
        <div style={{flex:1}}/>
        <button onClick={()=>{onClose();onRestart();}} style={{padding:"5px 12px",borderRadius:5,
          background:"#21262d",border:"1px solid #30363d",color:"#c9d1d9",cursor:"pointer",
          fontSize:11,fontFamily:"inherit"}}>🔁 New Goal</button>
        <button onClick={()=>{onClose();onRunAgain();}} style={{padding:"5px 12px",borderRadius:5,
          background:"linear-gradient(135deg,#6366f1,#8b5cf6)",border:"none",color:"white",
          cursor:"pointer",fontSize:11,fontWeight:600,fontFamily:"inherit"}}>▶ Run Again</button>
      </div>
    </Modal>
  );
}

/* ── File Browser ─────────────────────────────────────────────────────────── */
function FileBrowser({title,listUrl,fetchBase,onClose}){
  const [files,setFiles]=useState([]);
  const [sel,setSel]=useState(null);
  const [content,setCon]=useState("");
  useEffect(()=>{fetch(listUrl).then(r=>r.json()).then(d=>setFiles(d.files||[]));});
  const load=async f=>{setSel(f);const r=await fetch(`${fetchBase}/${f}`);const d=await r.json();setCon(d.content||"");};
  return(
    <Modal onClose={onClose} title={title} width={900}>
      <div style={{display:"flex",flex:1,overflow:"hidden",height:"70vh"}}>
        <div style={{width:250,borderRight:"1px solid #21262d",overflow:"auto",padding:8}}>
          {files.length===0&&<div style={{color:"#374151",fontSize:11,padding:10}}>No files yet</div>}
          {files.map(f=>(
            <div key={f.filename} onClick={()=>load(f.filename)} style={{padding:"5px 8px",borderRadius:4,
              cursor:"pointer",marginBottom:3,background:sel===f.filename?"#161b22":"transparent",
              border:`1px solid ${sel===f.filename?"#30363d":"transparent"}`}}>
              <div style={{fontSize:9.5,color:"#8b949e",wordBreak:"break-all"}}>{f.filename}</div>
              <div style={{fontSize:8.5,color:"#374151",marginTop:1}}>{(f.size/1024).toFixed(1)} KB</div>
            </div>
          ))}
        </div>
        <div style={{flex:1,overflow:"auto",padding:14,background:"#050508"}}>
          {content
            ?<pre style={{fontSize:10.5,color:"#94a3b8",whiteSpace:"pre-wrap",lineHeight:1.7,margin:0,
                fontFamily:"'JetBrains Mono','Fira Code',monospace"}}>{content}</pre>
            :<div style={{color:"#374151",fontSize:12,marginTop:20,textAlign:"center"}}>Select a file</div>}
        </div>
      </div>
    </Modal>
  );
}

/* ── Live Log ─────────────────────────────────────────────────────────────── */
function LiveLog({isRunning,commandEvents=[]}){
  const [content,setCon]=useState("(no active session)");
  const [filename,setFn]=useState("");
  const [autoScroll,setAs]=useState(true);
  const [paused,setPaused]=useState(false);
  const ref=useRef(null); const pRef=useRef(false); pRef.current=paused;
  useEffect(()=>{
    const poll=async()=>{
      if(pRef.current)return;
      try{const r=await fetch(`${API}/logs/current/tail?lines=200`);const d=await r.json();
        setCon(d.content||(isRunning?"(waiting…)":"(no active session)"));setFn(d.filename||"");}
      catch{setCon("(backend offline)");}
    };
    poll(); const id=setInterval(poll,700); return()=>clearInterval(id);
  },[isRunning]);
  useEffect(()=>{if(autoScroll&&ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[content,commandEvents,autoScroll]);
  const col=l=>{
    if(l.includes("════"))return"#1e293b";
    if(l.includes("SESSION"))return"#6366f1";
    if(/STEP \d/.test(l))return"#f59e0b";
    if(l.includes("QUERY"))return"#3b82f6";
    if(l.includes("RAW REPLY"))return"#8b5cf6";
    if(l.includes("DECISION"))return"#22c55e";
    if(l.includes("ACTION:"))return"#f97316";
    if(l.includes("RESULT"))return"#34d399";
    if(l.includes("ERROR"))return"#ef4444";
    if(l.includes("COMPLETED"))return"#22c55e";
    if(l.includes("SKIPPED"))return"#6b7280";
    if(l.includes("REJECTED"))return"#ef4444";
    if(l.includes("REFLECT")||l.includes("REPLAN"))return"#06b6d4";
    if(l.includes("INJECT"))return"#f97316";
    if(l.includes("Auto-detect"))return"#22c55e";
    if(l.startsWith("    "))return"#475569";
    return"#2d3748";
  };
  return(
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:"#050508"}}>
      <div style={{padding:"4px 12px",borderBottom:"1px solid #1a1f2e",background:"#0d1017",
        display:"flex",alignItems:"center",gap:7,flexShrink:0}}>
        <div style={{width:6,height:6,borderRadius:"50%",
          background:isRunning?"#22c55e":"#374151",
          boxShadow:isRunning?"0 0 5px #22c55e":"none",
          animation:isRunning?"pulse 1s infinite":"none"}}/>
        <span style={{fontSize:9.5,color:"#2d3748",fontFamily:"monospace",
          overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",flex:1}}>
          {filename||"no active session"}</span>
        <span style={{fontSize:8,color:"#1f2937"}}>auto-scroll</span>
        <div onClick={()=>setAs(v=>!v)} style={{width:22,height:12,borderRadius:6,cursor:"pointer",
          background:autoScroll?"#6366f1":"#1a1f2e",position:"relative",transition:"0.2s",flexShrink:0}}>
          <div style={{position:"absolute",top:1,left:autoScroll?11:1,width:10,height:10,
            borderRadius:"50%",background:"white",transition:"0.2s"}}/>
        </div>
        <button onClick={()=>setPaused(v=>!v)} style={{padding:"1px 6px",background:"none",
          border:`1px solid ${paused?"#f59e0b33":"#1a1f2e"}`,borderRadius:3,
          color:paused?"#f59e0b":"#374151",cursor:"pointer",fontSize:8.5,fontFamily:"inherit"}}>
          {paused?"▶":"⏸"}</button>
      </div>
      <div ref={ref} style={{flex:1,overflow:"auto",padding:"6px 12px",
        fontFamily:"'JetBrains Mono','Fira Code',monospace",fontSize:10.5,lineHeight:1.7}}>
        {content.split("\n").map((l,i)=>(
          <div key={i} style={{color:col(l),whiteSpace:"pre"}}>{l||"\u00a0"}</div>
        ))}
        {commandEvents.length>0&&(
          <div style={{marginTop:10,border:"1px solid #1a1f2e",borderRadius:5,
            background:"#030712",overflow:"hidden"}}>
            <div style={{padding:"4px 8px",borderBottom:"1px solid #1a1f2e",
              color:"#f97316",fontSize:9,fontWeight:700,textTransform:"uppercase",letterSpacing:1}}>
              Terminal Output
            </div>
            <div style={{maxHeight:220,overflow:"auto",padding:"5px 8px"}}>
              {commandEvents.slice(-160).map((e,i)=>(
                <div key={i} style={{color:e.kind==="stderr"?"#f87171":e.kind==="meta"?"#f59e0b":"#94a3b8",
                  whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{e.text||"\u00a0"}</div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Log Entry ────────────────────────────────────────────────────────────── */
function LogEntry({entry}){
  const colors={start:"#6366f1",done:"#22c55e",error:"#ef4444",thinking:"#f59e0b",
    decision:"#3b82f6",info:"#374151",warning:"#f97316",
    task_done:"#22c55e",task_skip:"#6b7280",plan:"#06b6d4",
    planning:"#f59e0b",inject:"#f97316",replan:"#06b6d4",auto:"#22c55e",
    command:"#f97316"};
  const color=colors[entry.kind]||"#374151";
  if(entry.kind==="decision") return(
    <div style={{background:`${color}07`,border:`1px solid ${color}18`,borderRadius:4,padding:"3px 6px"}}>
      <div style={{fontSize:9,color,fontWeight:700}}>STEP {entry.step} · {entry.action?.toUpperCase()}</div>
      {entry.summary&&<div style={{fontSize:9.5,color:"#94a3b8",marginTop:1}}>{entry.summary}</div>}
    </div>
  );
  if(entry.kind==="task_done") return(
    <div style={{background:"#22c55e07",border:"1px solid #22c55e18",borderRadius:4,padding:"3px 6px"}}>
      <div style={{fontSize:9,color:"#22c55e",fontWeight:700}}>
        {entry.auto?"✅ AUTO":"✓"} [{entry.task_id}]
      </div>
      {entry.finding&&<div style={{fontSize:9.5,color:"#86efac",marginTop:1}}>{entry.finding}</div>}
    </div>
  );
  if(entry.kind==="replan") return(
    <div style={{background:"#06b6d408",border:"1px solid #06b6d418",borderRadius:4,padding:"3px 6px"}}>
      <div style={{fontSize:9,color:"#06b6d4",fontWeight:700}}>🔄 REPLAN</div>
      <div style={{fontSize:9.5,color:"#67e8f9",marginTop:1}}>{entry.text}</div>
    </div>
  );
  return<div style={{fontSize:9.5,color,lineHeight:1.5}}>{entry.text}</div>;
}

/* ══════════════════════════════════════════════════════════════════════════
   MAIN APP
══════════════════════════════════════════════════════════════════════════ */
export default function App(){
  const [screenshot,setScreenshot]=useState("");
  const [url,setUrl]=useState(""); const [title,setTitle]=useState("");
  const [goal,setGoal]=useState("");
  const [provider,setProvider]=useState("ollama_local");
  const [model,setModel]=useState("qwen3:4b");
  const [allModels,setAllModels]=useState({
    ollama_local:["qwen3:4b"],ollama_cloud:["gpt-oss:120b"],
    google:["gemini-2.0-flash"],openai:["gpt-4o-mini"]
  });
  const [activeProvider,setActiveProvider]=useState("ollama_local");
  const [activeModel,setActiveModel]=useState("");
  const [tasks,setTasks]=useState([]);
  const [progress,setProgress]=useState("");
  const [currentTaskEnvironment,setCurrentTaskEnvironment]=useState("");
  const [currentTaskDescription,setCurrentTaskDescription]=useState("");
  const [isPlanning,setIsPlanning]=useState(false);
  const [isReplanning,setIsReplanning]=useState(false);
  const [replanMsg,setReplanMsg]=useState("");
  const [candidates,setCandidates]=useState([]);
  const [candidateTask,setCandidateTask]=useState("");
  const [autoRestart,setAutoRestart]=useState(false);
  const [isRunning,setIsRunning]=useState(false);
  const [log,setLog]=useState([]);
  const [thought,setThought]=useState("");
  const [summary,setSummary]=useState("");
  const [step,setStep]=useState(0);
  const [elapsed,setElapsed]=useState("");
  const [status,setStatus]=useState("idle");
  const [manualUrl,setManualUrl]=useState("");
  const [connected,setConnected]=useState(false);
  const [doneResult,setDoneResult]=useState(null);
  const [rightTab,setRightTab]=useState("browser");
  const [showSettings,setShowSettings]=useState(false);
  const [showResults,setShowResults]=useState(false);
  const [showLogs,setShowLogs]=useState(false);
  const [showProfiles,setShowProfiles]=useState(false);
  const [attachments,setAttachments]=useState([]);
  const [configLoaded,setConfigLoaded]=useState(false);
  const [commandEvents,setCommandEvents]=useState([]);
  const [pageView,setPageView]=useState("home");
  const [controlOwner,setControlOwner]=useState("agent");
  const [computerBusy,setComputerBusy]=useState(false);
  const [computerMessage,setComputerMessage]=useState("");
  const [operatorText,setOperatorText]=useState("");
  const [activityFilter,setActivityFilter]=useState("all");
  const [mobileNav,setMobileNav]=useState(false);
  const [startError,setStartError]=useState("");
  const [bots,setBots]=useState([{id:"primary",name:"Primary Browser Agent",role:"Web research, local files, desktop tools, and execution"}]);
  const [selectedBotId,setSelectedBotId]=useState("primary");

  const wsRef=useRef(null); const logRef=useRef(null); const fileInputRef=useRef(null);
  const goalRef=useRef(goal); const provRef=useRef(provider); const modRef=useRef(model);
  const attachmentsRef=useRef(attachments);
  const selectedBotRef=useRef(selectedBotId);
  const wheelTimerRef=useRef(null); const wheelDeltaRef=useRef(0);
  useEffect(()=>{goalRef.current=goal;},[goal]);
  useEffect(()=>{provRef.current=provider;},[provider]);
  useEffect(()=>{modRef.current=model;},[model]);
  useEffect(()=>{attachmentsRef.current=attachments;},[attachments]);
  useEffect(()=>{selectedBotRef.current=selectedBotId;},[selectedBotId]);

  const addLog=useCallback(e=>setLog(prev=>[...prev.slice(-300),{...e,ts:Date.now()}]),[]);

  const applyWorkspace=useCallback((d)=>{
    if(!d)return;
    setGoal(d.goal||"");
    setTasks(d.tasks||[]);setProgress(d.progress||"0/0");
    setLog((d.events||[]).map(e=>({...e,kind:e.kind||"info",text:e.text||e.kind})));
    setIsRunning(!!d.is_running);setControlOwner(d.control_owner||"agent");
    setUrl(d.url||"");setTitle(d.title||"");
    const meta=d.session?.metadata||{};
    setCurrentTaskDescription(meta.current_task_description||"");
    setCurrentTaskEnvironment(meta.current_task_environment||"");
    setStep(meta.step||0);
    if(meta.provider)setActiveProvider(meta.provider);
    if(meta.model)setActiveModel(meta.model);
    setStatus(d.is_running?"thinking":d.session?.status==="completed"?"done":"idle");
  },[]);

  const loadBotWorkspace=useCallback(async(id,changeSelection=true)=>{
    if(changeSelection){setSelectedBotId(id);selectedBotRef.current=id;}
    try{
      const [workspace,snapshot]=await Promise.all([
        fetch(`${API}/workspace/state?bot_id=${encodeURIComponent(id)}`).then(r=>r.json()),
        fetch(`${API}/bots/${encodeURIComponent(id)}/snapshot`).then(r=>r.ok?r.json():{}),
      ]);
      applyWorkspace(workspace);
      if(snapshot.screenshot)setScreenshot(snapshot.screenshot); else setScreenshot("");
      if(snapshot.url!==undefined)setUrl(snapshot.url||"");
      if(snapshot.title!==undefined)setTitle(snapshot.title||"");
    }catch(e){setStartError(e.message||"Could not load this bot workspace.");}
  },[applyWorkspace]);

  const refreshBots=useCallback(()=>fetch(`${API}/bots`).then(r=>r.json()).then(d=>setBots(d.bots||[])).catch(()=>{}),[]);

  useEffect(()=>{
    Promise.all([
      fetch(`${API}/models`).then(r=>r.json()),
      fetch(`${API}/config`).then(r=>r.json()),
      fetch(`${API}/workspace/state?bot_id=primary`).then(r=>r.json()),
      fetch(`${API}/bots`).then(r=>r.json()),
    ]).then(([models,cfg,workspace,botData])=>{
      const providerId=cfg.active_provider||"ollama_local";
      const modelId=cfg.active_model||"qwen3:4b";
      const hydrated={...models};
      if(!hydrated[providerId]?.includes(modelId))hydrated[providerId]=[modelId,...(hydrated[providerId]||[])];
      setAllModels(hydrated);setProvider(providerId);setModel(modelId);
      setBots(botData.bots||[]);applyWorkspace(workspace);setConfigLoaded(true);
    }).catch(()=>setConfigLoaded(true));
  },[applyWorkspace]);

  const refreshAttachments=useCallback(()=>{
    fetch(`${API}/attachments`).then(r=>r.json()).then(d=>setAttachments(d.attachments||[])).catch(()=>{});
  },[]);

  useEffect(()=>{refreshAttachments();},[refreshAttachments]);

  useEffect(()=>{
    fetch(`${API}/computer/state`).then(r=>r.json()).then(d=>{
      if(d.control_owner)setControlOwner(d.control_owner);
      if(d.url)setUrl(d.url);
      if(d.title)setTitle(d.title);
    }).catch(()=>{});
  },[]);

  useEffect(()=>{
    if(pageView!=="computer"||controlOwner!=="human")return;
    const refresh=()=>fetch(`${API}/screenshot`).then(r=>r.json()).then(d=>{
      if(d.screenshot)setScreenshot(d.screenshot);
      if(d.url!==undefined)setUrl(d.url||"");
      if(d.title!==undefined)setTitle(d.title||"");
    }).catch(()=>{});
    const id=setInterval(refresh,900);
    return()=>clearInterval(id);
  },[pageView,controlOwner]);

  useEffect(()=>{
    const list=allModels[provider];
    if(list?.length && !list.includes(model)) setModel(list[0]);
  },[provider,model,allModels]);

  useEffect(()=>{
    if(!configLoaded) return;
    fetch(`${API}/config`,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({active_provider:provider,active_model:model}),
    }).catch(()=>{});
  },[provider,model,configLoaded]);

  useEffect(()=>{
    function connect(){
      const ws=new WebSocket(WS_URL); wsRef.current=ws;
      ws.onopen=()=>setConnected(true);
      ws.onclose=()=>{setConnected(false);setTimeout(connect,3000);};
      ws.onerror=()=>setConnected(false);
      ws.onmessage=e=>{
        const msg=JSON.parse(e.data);
        if(msg.bot_id&&msg.bot_id!==selectedBotRef.current){refreshBots();return;}
        if(msg.type==="screenshot"){
          if(msg.data)setScreenshot(msg.data);
          if(msg.url)setUrl(msg.url); if(msg.title)setTitle(msg.title);
          if(msg.step)setStep(msg.step); if(msg.elapsed)setElapsed(msg.elapsed);
          if(msg.provider){setActiveProvider(msg.provider);}
          if(msg.model)setActiveModel(msg.model);
          if(msg.tasks)setTasks(msg.tasks);
          if(msg.progress)setProgress(msg.progress);
        }
        else if(msg.type==="session_state"){
          const meta=msg.session?.metadata||{};
          setCurrentTaskEnvironment(meta.current_task_environment||"");
          setCurrentTaskDescription(meta.current_task_description||"");
        }
        else if(msg.type==="planning"){setIsPlanning(true);addLog({kind:"planning",text:`🧠 ${msg.message}`});}
        else if(msg.type==="plan_ready"){
          setIsPlanning(false);setTasks(msg.tasks||[]);
          addLog({kind:"plan",text:`📋 Plan ready: ${msg.tasks?.length} tasks`});
          msg.tasks?.forEach(t=>addLog({kind:"info",text:`  ○ [${t.id}] ${t.description}`}));
        }
        else if(msg.type==="thinking"){setStatus("thinking");addLog({kind:"thinking",text:msg.message});}
        else if(msg.type==="ai_decision"){
          setThought(msg.thought||"");setSummary(msg.summary||"");setStatus("acting");
          if(msg.provider)setActiveProvider(msg.provider);
          if(msg.model)setActiveModel(msg.model);
          addLog({kind:"decision",step:msg.step,action:msg.action,summary:msg.summary});
        }
        else if(msg.type==="task_completed"){
          setTasks(msg.tasks||[]);setProgress(msg.progress||"");
          addLog({kind:"task_done",task_id:msg.task_id,finding:msg.finding,auto:msg.auto,
                  text:`${msg.auto?"✅ AUTO":"✓"} [${msg.task_id}]: ${msg.finding}`});
        }
        else if(msg.type==="task_skipped"){
          setTasks(msg.tasks||[]);
          addLog({kind:"task_skip",text:`⊘ Skipped [${msg.task_id}]: ${msg.reason}`});
        }
        else if(msg.type==="task_injected"){
          setTasks(msg.tasks||[]);
          addLog({kind:"inject",text:`➕ Injected [${msg.task_id}]: ${msg.description}`});
        }
        else if(msg.type==="replanning"){
          setIsReplanning(true);setReplanMsg("");
          addLog({kind:"replan",text:msg.message});
        }
        else if(msg.type==="replan_applied"){
          setIsReplanning(false);setTasks(msg.tasks||[]);
          const chg=(msg.changes||[]).join(", ");
          setReplanMsg(msg.assessment||"Plan updated");
          addLog({kind:"replan",text:`🔄 Replan applied: ${msg.assessment||""} | ${chg}`});
          setTimeout(()=>setReplanMsg(""),8000);
        }
        else if(msg.type==="candidates"){setCandidates(msg.candidates||[]);setCandidateTask(msg.task||"");}
        else if(msg.type==="control_state"){
          setControlOwner(msg.owner||"agent");
          if(msg.message){setComputerMessage(msg.message);setTimeout(()=>setComputerMessage(""),3500);}
        }
        else if(msg.type==="command_start"){
          setCommandEvents(prev=>[...prev.slice(-500),{kind:"meta",text:`$ ${msg.command}  (cwd: ${msg.cwd})`}]);
          addLog({kind:"command",text:`Command started: ${msg.command}`});
          setRightTab("log");
        }
        else if(msg.type==="command_output"){
          const kind=msg.stream==="stderr"?"stderr":"stdout";
          const prefix=kind==="stderr"?"ERR":"OUT";
          setCommandEvents(prev=>[...prev.slice(-500),{kind,text:`${prefix}> ${msg.line||""}`}]);
        }
        else if(msg.type==="command_done"){
          const text=`Command finished: exit ${msg.exit_code}${msg.timed_out?" (timeout)":""} in ${msg.elapsed}s`;
          setCommandEvents(prev=>[...prev.slice(-500),{kind:"meta",text}]);
          addLog({kind:"command",text});
        }
        else if(msg.type==="agent_start"){
          setIsRunning(true);setStatus("thinking");setStep(0);setElapsed("");
          setControlOwner("agent");
          setStartError("");
          setLog([]);setThought("");setSummary("");setDoneResult(null);
          setCommandEvents([]);
          setTasks([]);setProgress("");setIsPlanning(false);setIsReplanning(false);
          setCurrentTaskEnvironment("");setCurrentTaskDescription("");
          setReplanMsg("");setCandidates([]);setCandidateTask("");
          addLog({kind:"start",text:`🚀 Started | ${msg.goal}`});
          setRightTab("log");
        }
        else if(msg.type==="agent_done"){
          setIsRunning(false);setStatus("done");setThought("");
          setIsPlanning(false);setIsReplanning(false);
          setCurrentTaskEnvironment("");setCurrentTaskDescription("");
          if(msg.tasks)setTasks(msg.tasks);
          setDoneResult({...msg});
          addLog({kind:"done",text:`✅ Done · ${msg.steps} steps · ${msg.elapsed}`});
          setRightTab("browser");
          if(msg.auto_restart){
            const ng=msg.restart_goal||goalRef.current;
            setTimeout(()=>{
              fetch(`${API}/start`,{method:"POST",headers:{"Content-Type":"application/json"},
                body:JSON.stringify({
                  goal:ng,provider:provRef.current,model:modRef.current,auto_restart:true,
                  attachments: attachmentsRef.current.map(a=>a.id),
                  bot_id:selectedBotRef.current,
                })});
            },3000);
            addLog({kind:"info",text:"🔄 Auto-restarting in 3s…"});
          }
        }
        else if(msg.type==="agent_stopped"){
          setIsRunning(false);setStatus("idle");setIsPlanning(false);setIsReplanning(false);
          setCurrentTaskEnvironment("");setCurrentTaskDescription("");
          addLog({kind:"info",text:"⏹ Stopped"});
        }
        else if(msg.type==="error"){
          setStatus("error");setStartError(msg.message||"The agent stopped unexpectedly.");
          addLog({kind:"error",text:`❌ ${msg.message}`});
        }
        else if(msg.type==="warning"){addLog({kind:"warning",text:`⚠ ${msg.message}`});}
        else if(msg.type==="model_queue"){addLog({kind:"info",text:`Queued: ${msg.message}`});}
      };
    }
    connect(); return()=>wsRef.current?.close();
  },[addLog,refreshBots]);

  useEffect(()=>{logRef.current?.scrollTo({top:logRef.current.scrollHeight,behavior:"smooth"});},[log]);

  const startAgent=async()=>{
    if(!connected){setStartError("Backend is offline. Start it and try again.");return;}
    if(!goal.trim()){setStartError("Describe a task before starting the agent.");return;}
    setStartError("");
    try{
      const r=await fetch(`${API}/start`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          goal,provider,model,auto_restart:autoRestart,restart_goal:"",
          attachments: attachments.map(a=>a.id),
          bot_id:selectedBotId,
        })});
      const d=await r.json();
      if(!r.ok)throw new Error(d.detail||d.error||"The agent could not be started.");
    }catch(e){setStartError(e.message||"The agent could not be started.");}
  };
  const stopAgent=async()=>{await fetch(`${API}/stop`,{method:"POST"});};

  const updateComputerFromResponse=(d)=>{
    if(d?.screenshot)setScreenshot(d.screenshot);
    if(d?.url!==undefined)setUrl(d.url||"");
    if(d?.title!==undefined)setTitle(d.title||"");
    if(d?.control_owner)setControlOwner(d.control_owner);
  };

  const manualAction=async(action)=>{
    if(computerBusy)return null;
    setComputerBusy(true);
    try{
      const r=await fetch(`${API}/manual`,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify(action)});
      const d=await r.json();
      if(!r.ok)throw new Error(d.detail||d.error||"Browser interaction failed");
      updateComputerFromResponse(d);
      return d;
    }catch(e){
      setComputerMessage(e.message||"Browser interaction failed");
      setTimeout(()=>setComputerMessage(""),3500);
      return null;
    }finally{setComputerBusy(false);}
  };

  const takeControl=async()=>{
    const r=await fetch(`${API}/computer/take-control`,{method:"POST"});
    const d=await r.json();
    setControlOwner(d.control_owner||"human");
    setComputerMessage("You are now driving this browser.");
    setTimeout(()=>setComputerMessage(""),3500);
  };

  const resumeAgent=async()=>{
    const r=await fetch(`${API}/computer/resume-agent`,{method:"POST"});
    const d=await r.json();
    setControlOwner(d.control_owner||"agent");
    setComputerMessage(isRunning?"Agent resumed from the current page.":"Browser returned to agent mode.");
    setTimeout(()=>setComputerMessage(""),3500);
  };

  const manualNavigate=async()=>{
    if(!manualUrl)return;
    let u=manualUrl; if(!u.startsWith("http"))u="https://"+u;
    await manualAction({action:"navigate",url:u});
    setManualUrl("");
  };
  const uploadAttachments=async(files)=>{
    if(!files?.length)return;
    const form=new FormData();
    [...files].forEach(f=>form.append("files",f));
    await fetch(`${API}/attachments/upload`,{method:"POST",body:form});
    await refreshAttachments();
  };
  const removeAttachment=async(id)=>{
    await fetch(`${API}/attachments/${encodeURIComponent(id)}`,{method:"DELETE"});
    await refreshAttachments();
  };
  const injectTask=async(description,priority)=>{
    await fetch(`${API}/inject`,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({description,priority})});
  };

  const clickComputer=async(e)=>{
    if(isRunning&&controlOwner!=="human")return;
    const img=e.currentTarget;
    const rect=img.getBoundingClientRect();
    const x=Math.round((e.clientX-rect.left)*(img.naturalWidth/rect.width));
    const y=Math.round((e.clientY-rect.top)*(img.naturalHeight/rect.height));
    await manualAction({action:"click",x,y});
  };

  const scrollComputer=async(direction)=>{
    if(isRunning&&controlOwner!=="human")return;
    await manualAction({action:"scroll",direction,amount:520});
  };

  const wheelComputer=(deltaY)=>{
    if((isRunning&&controlOwner!=="human")||!deltaY)return;
    wheelDeltaRef.current+=deltaY;
    clearTimeout(wheelTimerRef.current);
    wheelTimerRef.current=setTimeout(()=>{
      const delta=wheelDeltaRef.current;wheelDeltaRef.current=0;
      manualAction({action:"scroll",direction:delta<0?"up":"down",amount:Math.max(160,Math.min(900,Math.abs(Math.round(delta*2))))});
    },70);
  };

  const selectBot=async(id,page="computer")=>{
    await loadBotWorkspace(id,true);setPageView(page);refreshBots();
  };

  const createBot=async(name,role)=>{
    const r=await fetch(`${API}/bots`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,role})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||"Could not create bot");
    await refreshBots();await selectBot(d.bot.id,"bots");
  };

  const sendOperatorText=async(submit=false)=>{
    if(!operatorText)return;
    await manualAction({action:submit?"type_and_submit":"type",text:operatorText});
    setOperatorText("");
  };

  const sColor={idle:"#6b7280",thinking:"#f59e0b",acting:"#3b82f6",done:"#22c55e",error:"#ef4444"}[status];
  const sLabel={idle:"Idle",thinking:"Thinking…",acting:"Executing",done:"Done",error:"Error"}[status];
  const pColor=PROVIDER_COLORS[activeProvider]||"#6366f1";

  return <NexusShell {...{
    pageView,setPageView,mobileNav,setMobileNav,connected,isRunning,status,sLabel,sColor,
    goal,setGoal,startAgent,stopAgent,provider,setProvider,model,setModel,allModels,autoRestart,setAutoRestart,
    screenshot,url,title,controlOwner,computerBusy,manualUrl,setManualUrl,manualNavigate,manualAction,
    clickComputer,scrollComputer,wheelComputer,operatorText,setOperatorText,sendOperatorText,takeControl,resumeAgent,computerMessage,
    tasks,progress,currentTaskEnvironment,currentTaskDescription,isPlanning,isReplanning,replanMsg,candidates,candidateTask,
    log,thought,summary,step,elapsed,activeProvider,activeModel,pColor,attachments,uploadAttachments,removeAttachment,
    refreshAttachments,fileInputRef,injectTask,activityFilter,setActivityFilter,commandEvents,
    showSettings,setShowSettings,showProfiles,setShowProfiles,showResults,setShowResults,showLogs,setShowLogs,
    doneResult,setDoneResult,startError,SettingsPanel,ProfilesPanel,FileBrowser,ResultsModal,InjectPanel,
    bots,selectedBotId,selectBot,createBot,
    api:API,startAgain:()=>{setDoneResult(null);setTimeout(startAgent,300)}
  }}/>;

  return(
    <div style={{display:"flex",flexDirection:"column",height:"100vh",background:"#0a0c12",
      color:"#e2e8f0",overflow:"hidden",fontFamily:"'JetBrains Mono','Fira Code',monospace"}}>

      {doneResult?.report&&<ResultsModal result={doneResult} onClose={()=>setDoneResult(null)}
        onRestart={()=>setDoneResult(null)} onRunAgain={()=>{setDoneResult(null);setTimeout(startAgent,300);}}/>}
      {showSettings&&<SettingsPanel onClose={()=>setShowSettings(false)}/>}
      {showProfiles&&<ProfilesPanel onClose={()=>setShowProfiles(false)}/>}
      {showResults&&<FileBrowser title="📁 Results" listUrl={`${API}/results`}
        fetchBase={`${API}/results`} onClose={()=>setShowResults(false)}/>}
      {showLogs&&<FileBrowser title="📋 Logs" listUrl={`${API}/logs`}
        fetchBase={`${API}/logs`} onClose={()=>setShowLogs(false)}/>}

      {/* Header */}
      <div style={{padding:"7px 14px",borderBottom:"1px solid #1a1f2e",background:"#0d1017",
        display:"flex",alignItems:"center",gap:10,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <div style={{width:27,height:27,borderRadius:6,
            background:"linear-gradient(135deg,#6366f1,#8b5cf6)",
            display:"flex",alignItems:"center",justifyContent:"center",fontSize:14}}>🤖</div>
          <div>
            <div style={{fontSize:12,fontWeight:700,color:"#a5b4fc"}}>AI Browser Agent</div>
            <div style={{fontSize:8,color:"#1f2937"}}>Task-Planned · Multi-Provider · Adaptive</div>
          </div>
        </div>
        {isRunning&&(
          <div style={{padding:"2px 8px",borderRadius:10,fontSize:9,fontWeight:600,
            background:`${pColor}18`,border:`1px solid ${pColor}38`,color:pColor}}>
            {PROVIDER_LABELS[activeProvider]||activeProvider} / {activeModel}
            {progress&&<span style={{color:"#374151",marginLeft:5}}>{progress}</span>}
          </div>
        )}
        {isRunning&&currentTaskEnvironment&&(
          <div style={{padding:"2px 8px",borderRadius:10,fontSize:9,fontWeight:600,
            background:`${(ENV_COLORS[currentTaskEnvironment]||"#64748b")}18`,
            border:`1px solid ${(ENV_COLORS[currentTaskEnvironment]||"#64748b")}38`,
            color:ENV_COLORS[currentTaskEnvironment]||"#94a3b8"}}>
            {ENV_LABELS[currentTaskEnvironment]||currentTaskEnvironment}
          </div>
        )}
        <div style={{flex:1}}/>
        {[["⚙️",()=>setShowSettings(true)],["🗂",()=>setShowProfiles(true)],["📋",()=>setShowLogs(true)],["📁",()=>setShowResults(true)]].map(([l,fn])=>(
          <button key={l} onClick={fn} style={{padding:"3px 8px",background:"#1a1f2e",
            border:"1px solid #2d3748",borderRadius:4,color:"#4b5563",cursor:"pointer",
            fontSize:12,fontFamily:"inherit"}}>{l}</button>
        ))}
        <div style={{display:"flex",alignItems:"center",gap:4,padding:"3px 8px",borderRadius:12,
          background:`${sColor}18`,border:`1px solid ${sColor}35`}}>
          <div style={{width:5,height:5,borderRadius:"50%",background:sColor,
            animation:["thinking","acting"].includes(status)?"pulse 1s infinite":"none"}}/>
          <span style={{fontSize:9,color:sColor,fontWeight:600}}>{sLabel}</span>
        </div>
        <div style={{fontSize:9,color:connected?"#22c55e":"#ef4444",display:"flex",alignItems:"center",gap:3}}>
          <div style={{width:4,height:4,borderRadius:"50%",background:connected?"#22c55e":"#ef4444"}}/>
          {connected?"OK":"Offline"}
        </div>
      </div>

      {/* Body */}
      <div style={{display:"flex",flex:1,overflow:"hidden"}}>

        {/* Left panel — scrollable */}
        <div style={{width:288,flexShrink:0,borderRight:"1px solid #1a1f2e",background:"#0d1017",
          display:"flex",flexDirection:"column",overflowY:"auto"}}>

          {/* Controls */}
          <div style={{padding:11,borderBottom:"1px solid #1a1f2e",flexShrink:0}}>
            <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>GOAL</label>
            <textarea value={goal} onChange={e=>setGoal(e.target.value)} disabled={isRunning}
              placeholder="Describe what you want the agent to do…"
              rows={3} style={{marginTop:3,width:"100%",background:"#0a0c12",border:"1px solid #1a1f2e",
                borderRadius:5,color:"#e2e8f0",padding:"5px 8px",fontSize:10.5,resize:"vertical",
                outline:"none",boxSizing:"border-box",fontFamily:"inherit"}}/>

            <div style={{marginTop:7}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:6}}>
                <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>ATTACHMENTS</label>
                <div style={{display:"flex",gap:4}}>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    onChange={e=>uploadAttachments(e.target.files).then(()=>{e.target.value="";})}
                    style={{display:"none"}}
                  />
                  <button onClick={()=>fileInputRef.current?.click()} disabled={isRunning}
                    style={{padding:"3px 6px",background:"#1a1f2e",border:"1px solid #2d3748",borderRadius:4,
                      color:"#94a3b8",cursor:isRunning?"not-allowed":"pointer",fontSize:9,fontFamily:"inherit"}}>
                    Upload
                  </button>
                  <button onClick={refreshAttachments}
                    style={{padding:"3px 6px",background:"#1a1f2e",border:"1px solid #2d3748",borderRadius:4,
                      color:"#64748b",cursor:"pointer",fontSize:9,fontFamily:"inherit"}}>
                    Refresh
                  </button>
                </div>
              </div>
              <div style={{marginTop:4,maxHeight:120,overflowY:"auto",display:"flex",flexDirection:"column",gap:4}}>
                {attachments.length===0 && (
                  <div style={{fontSize:9,color:"#4b5563",padding:"6px 8px",background:"#0a0c12",
                    border:"1px solid #1a1f2e",borderRadius:4}}>
                    No uploaded files yet. Add images, PDFs, or text/code files to give the agent extra context.
                  </div>
                )}
                {attachments.map(a=>(
                  <div key={a.id} style={{padding:"5px 6px",background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:4}}>
                    <div style={{display:"flex",alignItems:"center",gap:5}}>
                      <span style={{fontSize:8,color:"#6366f1",textTransform:"uppercase"}}>{a.kind}</span>
                      <span style={{fontSize:9.5,color:"#cbd5e1",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                        {a.name}
                      </span>
                      <button onClick={()=>removeAttachment(a.id)} disabled={isRunning}
                        style={{padding:"2px 5px",background:"#1a1f2e",border:"1px solid #2d3748",borderRadius:3,
                          color:"#f87171",cursor:isRunning?"not-allowed":"pointer",fontSize:8,fontFamily:"inherit"}}>
                        Remove
                      </button>
                    </div>
                    <div style={{fontSize:8.5,color:"#64748b",marginTop:2,lineHeight:1.4}}>
                      {a.summary}
                    </div>
                    {a.excerpt && (
                      <div style={{fontSize:8,color:"#475569",marginTop:2,lineHeight:1.4}}>
                        {a.excerpt.slice(0,140)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div style={{marginTop:7}}>
              <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>PROVIDER</label>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:3,marginTop:3}}>
                {Object.entries(PROVIDER_LABELS).map(([p,label])=>{
                  const on=provider===p; const col=PROVIDER_COLORS[p];
                  return<button key={p} onClick={()=>!isRunning&&setProvider(p)} disabled={isRunning}
                    style={{padding:"4px 5px",borderRadius:4,cursor:isRunning?"not-allowed":"pointer",
                      background:on?`${col}15`:"#0a0c12",border:`1px solid ${on?col:"#1a1f2e"}`,
                      color:on?col:"#2d3748",fontSize:9,fontFamily:"inherit",textAlign:"left",
                      transition:"0.15s"}}>{label}</button>;
                })}
              </div>
            </div>

            <div style={{marginTop:7}}>
              <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>MODEL</label>
              <select value={model} onChange={e=>setModel(e.target.value)} disabled={isRunning}
                style={{marginTop:3,width:"100%",background:"#0a0c12",border:"1px solid #1a1f2e",
                  borderRadius:5,color:"#e2e8f0",padding:"4px 7px",fontSize:10.5,outline:"none"}}>
                {(allModels[provider]||[]).map(m=><option key={m} value={m}>{m}</option>)}
              </select>
            </div>

            <div onClick={()=>!isRunning&&setAutoRestart(v=>!v)} style={{marginTop:7,
              display:"flex",alignItems:"center",gap:6,cursor:"pointer",padding:"4px 7px",
              background:autoRestart?"#6366f108":"#0a0c12",
              border:`1px solid ${autoRestart?"#6366f128":"#1a1f2e"}`,borderRadius:4}}>
              <div style={{width:22,height:12,borderRadius:6,position:"relative",
                background:autoRestart?"#6366f1":"#1a1f2e",flexShrink:0,transition:"0.2s"}}>
                <div style={{position:"absolute",top:1,left:autoRestart?11:1,width:10,height:10,
                  borderRadius:"50%",background:"white",transition:"0.2s"}}/>
              </div>
              <span style={{fontSize:9.5,color:autoRestart?"#a5b4fc":"#2d3748"}}>Auto-restart</span>
            </div>

            <button onClick={isRunning?stopAgent:startAgent} disabled={!connected} style={{
              marginTop:8,width:"100%",padding:"7px 0",borderRadius:5,border:"none",
              cursor:connected?"pointer":"not-allowed",fontFamily:"inherit",fontWeight:700,fontSize:11,
              color:"white",
              background:isRunning?"linear-gradient(135deg,#ef4444,#dc2626)":"linear-gradient(135deg,#6366f1,#8b5cf6)",
              boxShadow:isRunning?"0 0 12px #ef444422":"0 0 12px #6366f122"}}>
              {isRunning?`⏹ Stop  (${elapsed})`:"▶ Start Agent"}
            </button>
          </div>

          {/* Manual nav */}
          <div style={{padding:"7px 11px",borderBottom:"1px solid #1a1f2e",flexShrink:0}}>
            <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>NAVIGATE</label>
            <div style={{display:"flex",gap:4,marginTop:3}}>
              <input value={manualUrl} onChange={e=>setManualUrl(e.target.value)}
                onKeyDown={e=>e.key==="Enter"&&manualNavigate()} placeholder="site.com"
                style={{flex:1,background:"#0a0c12",border:"1px solid #1a1f2e",borderRadius:4,
                  color:"#e2e8f0",padding:"4px 7px",fontSize:10,outline:"none",fontFamily:"inherit"}}/>
              <button onClick={manualNavigate} style={{padding:"4px 8px",background:"#1a1f2e",
                border:"1px solid #2d3748",borderRadius:4,color:"#4b5563",cursor:"pointer",
                fontSize:10,fontFamily:"inherit"}}>Go</button>
            </div>
          </div>

          {/* Inject task panel */}
          <InjectPanel isRunning={isRunning} onInject={injectTask}/>

          {/* Task checklist */}
          <TaskChecklist tasks={tasks} progress={progress} isPlanning={isPlanning}
            isReplanning={isReplanning} replanMsg={replanMsg}
            taskEnvironment={currentTaskEnvironment} currentTaskDescription={currentTaskDescription}/>

          {/* Candidates */}
          <CandidatesPanel candidates={candidates} task={candidateTask}/>

          {/* Thought */}
          <div style={{padding:"7px 11px",borderBottom:"1px solid #1a1f2e",flexShrink:0}}>
            <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>
              AI THOUGHT {step>0?`· Step ${step}`:""}
            </label>
            <div style={{marginTop:3,minHeight:52,padding:"5px 7px",background:"#0a0c12",
              border:"1px solid #1a1f2e",borderRadius:4,fontSize:10,color:"#64748b",lineHeight:1.6}}>
              {thought?<Typewriter text={thought} speed={10}/>
                :<span style={{color:"#1a1f2e"}}>Waiting…</span>}
            </div>
            {summary&&<div style={{marginTop:3,padding:"3px 7px",background:"#6366f108",
              border:"1px solid #6366f122",borderRadius:3,fontSize:9.5,color:"#818cf8"}}>
              → {summary}</div>}
          </div>

          {/* Activity log */}
          <div style={{flexShrink:0}}>
            <div style={{padding:"5px 11px",borderBottom:"1px solid #1a1f2e"}}>
              <label style={{fontSize:8,color:"#2d3748",textTransform:"uppercase",letterSpacing:1}}>
                LOG ({log.length})
              </label>
            </div>
            <div ref={logRef} style={{maxHeight:260,overflowY:"auto",padding:"6px 11px",
              display:"flex",flexDirection:"column",gap:3}}>
              {log.length===0&&<div style={{color:"#1a1f2e",fontSize:10,textAlign:"center",marginTop:10}}>
                Start the agent</div>}
              {log.map((e,i)=><LogEntry key={i} entry={e}/>)}
            </div>
          </div>

        </div>{/* end left panel */}

        {/* Right panel */}
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          {/* Tabs */}
          <div style={{display:"flex",borderBottom:"1px solid #1a1f2e",background:"#0d1017",flexShrink:0}}>
            {[["browser","🌐 Browser"],["log","📋 Live Log"]].map(([tab,label])=>(
              <button key={tab} onClick={()=>setRightTab(tab)} style={{
                padding:"6px 14px",background:"none",border:"none",cursor:"pointer",
                borderBottom:`2px solid ${rightTab===tab?"#6366f1":"transparent"}`,
                color:rightTab===tab?"#a5b4fc":"#2d3748",
                fontSize:10.5,fontFamily:"inherit",fontWeight:rightTab===tab?700:400}}>
                {label}
              </button>
            ))}
            {rightTab==="browser"&&(
              <div style={{flex:1,display:"flex",alignItems:"center",gap:7,padding:"0 10px"}}>
                <div style={{flex:1,background:"#0a0c12",border:"1px solid #1a1f2e",
                  borderRadius:4,padding:"2px 8px",fontSize:10,color:"#1f2937",
                  overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{url}</div>
                {title&&<div style={{fontSize:9,color:"#1a1f2e",maxWidth:140,
                  overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{title}</div>}
              </div>
            )}
            {rightTab==="log"&&isRunning&&(
              <div style={{display:"flex",alignItems:"center",paddingRight:12,marginLeft:"auto"}}>
                <span style={{fontSize:9,color:"#22c55e",animation:"pulse 1s infinite"}}>● LIVE</span>
              </div>
            )}
          </div>

          {rightTab==="browser"?(
            <div style={{flex:1,overflow:"auto",position:"relative",background:"#050507",
              display:"flex",alignItems:"flex-start",justifyContent:"center"}}>
              {screenshot
                ?<img src={`data:image/jpeg;base64,${screenshot}`} alt="Browser"
                    style={{maxWidth:"100%",objectFit:"contain",objectPosition:"top"}}/>
                :<div style={{display:"flex",flexDirection:"column",alignItems:"center",
                    justifyContent:"center",height:"100%",color:"#1a1f2e"}}>
                  <div style={{fontSize:36,marginBottom:8}}>🌐</div>
                  <div style={{fontSize:12}}>{connected?"Ready":"Connecting…"}</div>
                </div>}
              {isRunning&&step>0&&(
                <div style={{position:"sticky",top:8,left:"auto",right:8,
                  float:"right",background:"#0d1017cc",
                  border:"1px solid #1a1f2e",borderRadius:4,padding:"4px 10px",fontSize:10,
                  backdropFilter:"blur(4px)",zIndex:10}}>
                  <span style={{color:pColor,fontWeight:700}}>
                    {PROVIDER_LABELS[activeProvider]||activeProvider}
                  </span>
                  <span style={{color:"#2d3748"}}> · Step {step} · {elapsed}</span>
                  {progress&&<span style={{color:"#06b6d4",marginLeft:5}}>· {progress}</span>}
                </div>
              )}
            </div>
          ):(
            <div style={{flex:1,overflow:"hidden"}}><LiveLog isRunning={isRunning} commandEvents={commandEvents}/></div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
        *{box-sizing:border-box;}
        ::-webkit-scrollbar{width:3px;}
        ::-webkit-scrollbar-track{background:transparent;}
        ::-webkit-scrollbar-thumb{background:#1a1f2e;border-radius:2px;}
        select option{background:#0a0c12;}
      `}</style>
    </div>
  );
}
