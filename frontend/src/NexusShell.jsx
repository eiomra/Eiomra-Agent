import {useState} from "react";
import botAvatar from "./assets/bot-avatar.svg";

const NAV_ITEMS=[
  ["home","Home","⌂"], ["bots","Bots","◎"], ["tasks","Tasks","▣"],
  ["computer","Computer","▱"], ["activity","Activity","◷"],
];

function StatusPill({status}){
  const value=(status||"pending").toLowerCase().replaceAll("_","-");
  return <span className={`status-pill status-${value}`}><i/>{status||"Pending"}</span>;
}

function EmptyState({icon="E",title,body}){
  return <div className="empty-state"><div className="empty-orb">{icon}</div><h3>{title}</h3><p>{body}</p></div>;
}

function MetricCard({icon,label,value,tone="purple"}){
  return <div className="metric-card"><div className={`metric-icon ${tone}`}>{icon}</div><div><div className="metric-label">{label}</div><div className="metric-value">{value}</div></div></div>;
}

function ComputerFrame({compact=false,screenshot,url,title,isRunning,controlOwner,computerBusy,
  manualUrl,setManualUrl,manualNavigate,manualAction,clickComputer,scrollComputer,wheelComputer,
  operatorText,setOperatorText,sendOperatorText,takeControl,resumeAgent,computerMessage}){
  const canDrive=!isRunning||controlOwner==="human";
  return <section className={`computer-frame ${compact?"is-compact":""}`}>
    <div className="computer-titlebar">
      <div className="window-dots"><i/><i/><i/></div>
      <div className="computer-identity"><span className="computer-logo"><img src={botAvatar} alt=""/></span><div><strong>Eiomra Computer</strong><small>{title||"Persistent Chromium workspace"}</small></div></div>
      <div className={`owner-chip owner-${controlOwner}`}><i/>{canDrive?"Manual control":"Agent control"}</div>
      {!compact&&<button className={controlOwner==="human"?"btn btn-primary":"btn btn-secondary"} onClick={controlOwner==="human"?resumeAgent:takeControl}>
        {controlOwner==="human"?(isRunning?"Resume agent":"Return to agent"):"Take control"}
      </button>}
    </div>
    <div className="browser-chrome">
      <div className="browser-nav-buttons">
        <button aria-label="Back" onClick={()=>manualAction({action:"back"})} disabled={!canDrive}>←</button>
        <button aria-label="Forward" onClick={()=>manualAction({action:"forward"})} disabled={!canDrive}>→</button>
        <button aria-label="Reload" onClick={()=>manualAction({action:"reload"})} disabled={!canDrive}>↻</button>
      </div>
      <form className="address-bar" onSubmit={e=>{e.preventDefault();manualNavigate();}}><span>⌁</span><input value={manualUrl} onChange={e=>setManualUrl(e.target.value)} placeholder={url||"Enter a URL"} disabled={!canDrive}/></form>
      <span className="secure-label">Private profile</span>
    </div>
    <div className={`computer-screen ${canDrive?"is-interactive":"is-observing"}`}
      onWheel={e=>{if(canDrive)wheelComputer(e.deltaY);}}>
      {screenshot?<img src={`data:image/jpeg;base64,${screenshot}`} alt="Live shared Chromium browser" onClick={clickComputer} draggable="false"/>:
        <EmptyState icon="▱" title="Browser is ready" body="Navigate to a site or start an agent task to see its computer."/>}
      {!canDrive&&<button className="control-overlay" onClick={takeControl}><span>Agent is using the browser</span><strong>Take control to click or type</strong></button>}
      {computerBusy&&<div className="computer-loading"><i/>Sending input…</div>}
      {computerMessage&&<div className="computer-toast">{computerMessage}</div>}
    </div>
    {!compact&&<div className="computer-input-dock">
      <div className="input-help"><strong>{canDrive?"You are connected":"View only"}</strong><span>{canDrive?"Click the screen, scroll, or type into the focused field.":"The bot has exclusive input control."}</span></div>
      <div className="scroll-controls"><button onClick={()=>scrollComputer("up")} disabled={!canDrive}>↑ Scroll</button><button onClick={()=>scrollComputer("down")} disabled={!canDrive}>↓ Scroll</button></div>
      <div className="operator-input"><input value={operatorText} onChange={e=>setOperatorText(e.target.value)} disabled={!canDrive}
        onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();sendOperatorText(false);}}} placeholder="Type into the focused browser field…"/>
        <button onClick={()=>sendOperatorText(false)} disabled={!canDrive||!operatorText}>Type</button>
        <button className="send-key" onClick={()=>sendOperatorText(true)} disabled={!canDrive||!operatorText}>Type + Enter</button>
        <button onClick={()=>manualAction({action:"press",key:"Tab"})} disabled={!canDrive}>Tab</button>
        <button onClick={()=>manualAction({action:"press",key:"Enter"})} disabled={!canDrive}>Enter</button>
      </div>
    </div>}
  </section>;
}

function TaskRows({tasks,onOpenComputer}){
  if(!tasks.length)return <EmptyState icon="▣" title="No task plan yet" body="Describe a goal on Home. Eiomra will break it into trackable steps here."/>;
  return <div className="task-table"><div className="task-table-head"><span>Task</span><span>Status</span><span>Environment</span><span>Result</span></div>
    {tasks.map((task,index)=><button className="task-row" key={task.id||index} onClick={onOpenComputer}>
      <span className="task-main"><b>{String(index+1).padStart(2,"0")}</b><span><strong>{task.description}</strong><small>{task.id}</small></span></span>
      <StatusPill status={task.status}/><span className="task-environment">{task.environment||"Auto"}</span><span className="task-finding">{task.finding||task.reason||"—"}</span>
    </button>)}
  </div>;
}

export default function NexusShell(props){
  const {pageView,setPageView,mobileNav,setMobileNav,connected,isRunning,status,sLabel,goal,setGoal,startAgent,stopAgent,
    provider,setProvider,model,setModel,allModels,screenshot,url,title,controlOwner,computerBusy,manualUrl,setManualUrl,
    manualNavigate,manualAction,clickComputer,scrollComputer,wheelComputer,operatorText,setOperatorText,sendOperatorText,takeControl,resumeAgent,
    computerMessage,tasks,progress,currentTaskDescription,isPlanning,isReplanning,replanMsg,log,thought,summary,step,elapsed,
    activeModel,attachments,uploadAttachments,removeAttachment,fileInputRef,injectTask,activityFilter,setActivityFilter,commandEvents,
    showSettings,setShowSettings,showProfiles,setShowProfiles,showResults,setShowResults,showLogs,setShowLogs,doneResult,setDoneResult,startError,
    startAgain,SettingsPanel,ProfilesPanel,FileBrowser,ResultsModal,InjectPanel,api,
    bots:botList=[],selectedBotId,selectBot,createBot}=props;

  const completed=tasks.filter(t=>t.status==="completed").length;
  const active=tasks.filter(t=>["in_progress","running"].includes(t.status)).length;
  const needsAttention=tasks.filter(t=>["failed","skipped","waiting"].includes(t.status)).length;
  const filteredLog=activityFilter==="all"?log:log.filter(e=>e.kind===activityFilter);
  const [searchQuery,setSearchQuery]=useState("");
  const normalizedSearch=searchQuery.trim().toLowerCase();
  const searchResults=normalizedSearch?[...botList.filter(bot=>`${bot.name} ${bot.role}`.toLowerCase().includes(normalizedSearch)).map(bot=>({kind:"bot",id:bot.id,label:bot.name,meta:bot.role})),
    ...tasks.filter(task=>`${task.description} ${task.finding||""}`.toLowerCase().includes(normalizedSearch)).map(task=>({kind:"task",id:task.id,label:task.description,meta:task.status}))].slice(0,8):[];
  const selectedBot=botList.find(bot=>bot.id===selectedBotId)||botList[0]||{name:"Primary Browser Agent"};
  const computerProps={screenshot,url,title,isRunning,controlOwner,computerBusy,manualUrl,setManualUrl,manualNavigate,manualAction,
    clickComputer,scrollComputer,wheelComputer,operatorText,setOperatorText,sendOperatorText,takeControl,resumeAgent,computerMessage};
  const go=page=>{setPageView(page);setMobileNav(false);};
  const runGoal=()=>{if(goal.trim())startAgent();};

  const home=<div className="page home-page">
    <div className="page-heading"><div><span className="eyebrow">AI WORKSPACE</span><h1>Good to see you</h1><p>What would you like your agent to accomplish?</p></div><button className="btn btn-primary desktop-only" onClick={()=>go("bots")}>Open bot workspace</button></div>
    <section className="goal-composer"><textarea value={goal} onChange={e=>setGoal(e.target.value)} disabled={isRunning} placeholder="Describe a task for Eiomra Agent…" rows={3}/>
      <div className="composer-footer"><div className="quick-actions">
        <button onClick={()=>setGoal(`Research ${goal}`)} disabled={isRunning}>⌕ Research</button><button onClick={()=>setGoal(`Create ${goal}`)} disabled={isRunning}>✎ Create</button><button onClick={()=>setGoal(`Analyze ${goal}`)} disabled={isRunning}>⌁ Analyze</button>
        <label className="upload-button">＋ Attach<input ref={fileInputRef} type="file" multiple onChange={e=>uploadAttachments(e.target.files).then(()=>{e.target.value="";})}/></label>
      </div><div className="run-controls"><select value={provider} onChange={e=>setProvider(e.target.value)} disabled={isRunning}>{Object.keys(allModels).map(p=><option value={p} key={p}>{p.replaceAll("_"," ")}</option>)}</select>
        <select value={model} onChange={e=>setModel(e.target.value)} disabled={isRunning}>{(allModels[provider]||[]).map(m=><option key={m}>{m}</option>)}</select>
        <button className={isRunning?"run-button stop":"run-button"} aria-label={isRunning?"Stop agent":"Start agent"} title={isRunning?"Stop agent":"Start agent"} onClick={isRunning?stopAgent:runGoal} disabled={!connected||(!isRunning&&!goal.trim())}>{isRunning?"■":"↑"}</button></div></div>
      {startError&&<div className="start-error"><strong>Couldn’t start the agent.</strong><span>{startError}</span></div>}
      {attachments.length>0&&<div className="attachment-strip">{attachments.map(a=><span key={a.id}>{a.name}<button onClick={()=>removeAttachment(a.id)}>×</button></span>)}</div>}
    </section>
    <div className="home-grid"><div className="home-main"><div className="section-heading"><div><h2>Current plan</h2><p>{progress||"Waiting for a task"}</p></div><button onClick={()=>go("tasks")}>View all</button></div>
      <div className="recent-task-grid">{(tasks.length?tasks.slice(0,4):[{description:"Your first task will appear here",status:"pending",finding:"Start with a goal above"}]).map((task,i)=><button className="recent-task-card" key={task.id||i} onClick={()=>go("computer")}><StatusPill status={task.status}/><h3>{task.description}</h3><p>{task.finding||task.reason||"Agent step ready to run"}</p><div className="recent-task-foot"><span>{task.environment||"Browser + tools"}</span><b>→</b></div></button>)}</div>
      <div className="section-heading active-heading"><div><h2>Active bot</h2><p>Live execution and browser state</p></div><button onClick={()=>go("computer")}>Open computer</button></div>
      <button className="active-bot-row" onClick={()=>go("computer")}><span className="bot-avatar"><img src={botAvatar} alt=""/></span><span className="bot-copy"><strong>{selectedBot.name}</strong><small>{currentTaskDescription||"Ready for your next goal"}</small></span><span className="bot-working"><small>{isRunning?"Working now":"Available"}</small><i><b style={{width:`${isRunning?Math.max(8,Math.min(100,(completed/Math.max(tasks.length,1))*100)):0}%`}}/></i></span><StatusPill status={isRunning?(controlOwner==="human"?"waiting":"running"):status==="done"?"completed":"idle"}/><b>›</b></button>
    </div><aside className="home-aside"><div className="panel status-panel"><div className="section-heading"><h2>Workspace status</h2><button onClick={()=>go("tasks")}>View all</button></div><dl>
      <div><dt><i className="green"/>Agent</dt><dd>{isRunning?"Running":"Ready"}</dd></div><div><dt><i className="blue"/>Completed steps</dt><dd>{completed}</dd></div><div><dt><i className="purple"/>Total steps</dt><dd>{tasks.length}</dd></div><div><dt><i className={connected?"green":"red"}/>Backend</dt><dd>{connected?"Connected":"Offline"}</dd></div></dl></div>
      <div className="panel mini-activity"><div className="section-heading"><h2>Recent activity</h2><button onClick={()=>go("activity")}>View all</button></div>{log.slice(-5).reverse().map((entry,i)=><div className="mini-activity-row" key={entry.ts||i}><span>{entry.kind==="error"?"!":entry.kind==="done"?"✓":"·"}</span><div><strong>{entry.text||entry.summary}</strong><small>{entry.kind}</small></div></div>)}{!log.length&&<p className="muted-copy">Agent events will appear here in real time.</p>}</div>
    </aside></div>
  </div>;

  const botsPage=<div className="page"><div className="page-heading"><div><span className="eyebrow">RUNTIMES</span><h1>Bots</h1><p>Create, manage, and monitor your AI workforce.</p></div><button className="btn btn-primary" onClick={()=>go("home")}>＋ New task</button></div>
    <div className="bot-layout"><div className="bot-grid">{botList.map(bot=><article className={`bot-card ${bot.id===selectedBotId?"featured":""}`} key={bot.id}><div className="bot-card-top"><span className="bot-avatar large"><img src={botAvatar} alt=""/></span><div><h2>{bot.name}</h2><p>{bot.role}</p></div><StatusPill status={bot.is_running?"running":bot.status||"ready"}/></div><div className="bot-metrics"><div><span>Current steps</span><b>{bot.task_count||0}</b></div><div><span>Progress</span><b>{bot.progress||"0/0"}</b></div><div><span>Browser</span><b>{bot.id===selectedBotId&&connected?"Live":"Saved"}</b></div></div><div className="bot-capabilities"><span>Persistent task history</span><span>Human takeover</span><span>Saved computer state</span><span>Multi-provider AI</span></div><div className="bot-card-actions"><button className="btn btn-primary" onClick={()=>selectBot(bot.id,"computer")}>Open computer</button><button className="btn btn-secondary" onClick={()=>selectBot(bot.id,"home")}>Assign task</button></div></article>)}
      <button className="bot-card create-runtime" onClick={()=>{const name=window.prompt("Name this bot");if(name)createBot(name,"Browser and workspace automation");}}><span>＋</span><h2>Create bot workspace</h2><p>Add another durable bot with its own tasks, history, and saved computer view.</p><small>Runs are safely serialized through the local browser worker.</small></button></div>
      <aside className="panel bot-side"><span className="eyebrow">LIVE SUMMARY</span><h2>Bot performance</h2><dl><div><dt>Status</dt><dd>{sLabel}</dd></div><div><dt>Model</dt><dd>{activeModel||model}</dd></div><div><dt>Current step</dt><dd>{step||0}</dd></div><div><dt>Elapsed</dt><dd>{elapsed||"—"}</dd></div></dl><button onClick={()=>setShowProfiles(true)}>Manage browser profiles</button></aside>
    </div>
  </div>;

  const taskPage=<div className="page"><div className="page-heading"><div><span className="eyebrow">EXECUTION PLAN</span><h1>Tasks</h1><p>Track the work being completed by your active agent.</p></div><button className="btn btn-primary" onClick={()=>go("home")}>＋ New task</button></div>
    <div className="metric-grid"><MetricCard icon="▣" label="Total steps" value={tasks.length}/><MetricCard icon="◌" label="In progress" value={active} tone="blue"/><MetricCard icon="✓" label="Completed" value={completed} tone="green"/><MetricCard icon="!" label="Needs attention" value={needsAttention} tone="amber"/></div>
    {(isPlanning||isReplanning)&&<div className="planning-banner"><i/> {isReplanning?"Reviewing and adapting the plan…":"Building the task plan…"}</div>}{replanMsg&&<div className="planning-banner success">{replanMsg}</div>}
    <section className="panel table-panel"><div className="table-toolbar"><div><h2>All steps</h2><p>{progress||"0/0 completed"}</p></div><button className="btn btn-secondary" onClick={()=>go("computer")}>View live computer</button></div><TaskRows tasks={tasks} onOpenComputer={()=>go("computer")}/></section>
  </div>;

  const computer=<div className="page computer-page"><div className="page-heading computer-heading"><div><span className="eyebrow">SHARED BROWSER SESSION</span><h1>Bot computer</h1><p>Watch the agent work, or take over the exact same persistent browser.</p></div><div className="computer-page-actions"><StatusPill status={isRunning?(controlOwner==="human"?"waiting":"running"):"ready"}/>{isRunning&&<button className="btn btn-danger" onClick={stopAgent}>Stop run</button>}</div></div>
    <div className="computer-workspace"><ComputerFrame {...computerProps}/><aside className="computer-sidebar"><section className="panel now-panel"><span className="eyebrow">NOW</span><h2>{isRunning?(controlOwner==="human"?"Waiting for you":"Agent is working"):"Computer ready"}</h2><p>{currentTaskDescription||summary||"Start a task or use the browser manually."}</p><div className="thought-box"><span>Latest thought</span><p>{thought||"No active reasoning yet."}</p></div></section>
      <section className="panel checklist-panel"><div className="section-heading"><h2>Task plan</h2><span>{progress}</span></div><div className="compact-tasks">{tasks.map((task,i)=><div key={task.id||i}><span className={`task-check ${task.status}`}>{task.status==="completed"?"✓":i+1}</span><p>{task.description}</p></div>)}{!tasks.length&&<p className="muted-copy">No active plan.</p>}</div></section>{isRunning&&<InjectPanel isRunning={isRunning} onInject={injectTask}/>}</aside></div>
  </div>;

  const activity=<div className="page"><div className="page-heading"><div><span className="eyebrow">OBSERVABILITY</span><h1>Activity</h1><p>Live decisions, tool actions, terminal output, and agent events.</p></div><div className="live-chip"><i className={connected?"online":""}/>{connected?"Live":"Disconnected"}</div></div>
    <section className="activity-layout"><div className="panel activity-feed"><div className="activity-toolbar"><div>{["all","thinking","decision","error"].map(f=><button key={f} className={activityFilter===f?"active":""} onClick={()=>setActivityFilter(f)}>{f}</button>)}</div><button onClick={()=>setShowLogs(true)}>Open saved logs</button></div><div className="timeline">{filteredLog.slice().reverse().map((entry,i)=><div className={`timeline-item kind-${entry.kind}`} key={entry.ts||i}><span className="timeline-dot"/><div><small>{entry.kind} {entry.step?`· Step ${entry.step}`:""}</small><p>{entry.text||entry.summary||entry.finding}</p></div></div>)}{!filteredLog.length&&<EmptyState icon="◷" title="No activity yet" body="Start an agent task to stream its work here."/>}</div></div>
      <aside className="panel terminal-panel"><div className="terminal-head"><span>Terminal output</span><i>{commandEvents.length} lines</i></div><pre>{commandEvents.length?commandEvents.slice(-120).map((e,i)=><span className={e.kind} key={i}>{e.text}{"\n"}</span>):"No command output in this session."}</pre></aside></section>
  </div>;

  const body=pageView==="home"?home:pageView==="bots"?botsPage:pageView==="tasks"?taskPage:pageView==="computer"?computer:activity;
  return <div className="nexus-app">
    {doneResult?.report&&<ResultsModal result={doneResult} onClose={()=>setDoneResult(null)} onRestart={()=>setDoneResult(null)} onRunAgain={startAgain}/>} {showSettings&&<SettingsPanel onClose={()=>setShowSettings(false)}/>} {showProfiles&&<ProfilesPanel onClose={()=>setShowProfiles(false)}/>} {showResults&&<FileBrowser title="Results" listUrl={`${api}/results`} fetchBase={`${api}/results`} onClose={()=>setShowResults(false)}/>} {showLogs&&<FileBrowser title="Logs" listUrl={`${api}/logs`} fetchBase={`${api}/logs`} onClose={()=>setShowLogs(false)}/>}
    <aside className={`main-sidebar ${mobileNav?"is-open":""}`}><button className="brand" onClick={()=>go("home")}><span><img src={botAvatar} alt="Eiomra bot"/></span><strong>Eiomra <b>Agent</b></strong></button><nav>{NAV_ITEMS.map(([id,label,icon])=><button key={id} className={pageView===id?"active":""} onClick={()=>go(id)}><span>{id==="bots"?<img src={botAvatar} alt=""/>:icon}</span>{label}{id==="computer"&&controlOwner==="human"?<i className="nav-alert"/>:null}</button>)}</nav><div className="sidebar-spacer"/><div className="sidebar-tools"><button onClick={()=>setShowResults(true)}><span>▤</span>Results</button><button onClick={()=>setShowProfiles(true)}><span>◎</span>Profiles</button><button onClick={()=>setShowSettings(true)}><span>⚙</span>Settings</button></div><div className="workspace-switcher"><span className="bot-avatar small"><img src={botAvatar} alt=""/></span><div><strong>Local workspace</strong><small>{connected?"All systems connected":"Backend offline"}</small></div><i className={connected?"online":""}/></div></aside>
    {mobileNav&&<button className="nav-backdrop" aria-label="Close navigation" onClick={()=>setMobileNav(false)}/>}<div className="app-column"><header className="topbar"><button className="menu-button" onClick={()=>setMobileNav(true)}>☰</button><div className="command-search"><span>⌕</span><input value={searchQuery} onChange={e=>setSearchQuery(e.target.value)} placeholder="Search tasks or bots…" onKeyDown={e=>{if(e.key==="Escape")setSearchQuery("");}}/><kbd>⌘ K</kbd>{normalizedSearch&&<div className="search-results">{searchResults.map((item,i)=><button key={`${item.kind}-${item.id}-${i}`} onClick={()=>{if(item.kind==="bot")selectBot(item.id,"computer");else go("tasks");setSearchQuery("");}}><span>{item.kind==="bot"?<img src={botAvatar} alt=""/>:"▣"}</span><div><strong>{item.label}</strong><small>{item.kind} · {item.meta}</small></div></button>)}{!searchResults.length&&<p>No matching tasks or bots.</p>}</div>}</div><div className="topbar-actions"><div className={`connection-chip ${connected?"online":""}`}><i/>{connected?"Connected":"Offline"}</div><button className="icon-button" onClick={()=>setShowLogs(true)}>◷</button><button className="icon-button" onClick={()=>setShowSettings(true)}>?</button><button className="btn btn-primary" onClick={()=>go("home")}>＋ New task</button><span className="user-avatar">EA</span></div></header><main>{body}</main><footer><span>Eiomra Agent v1.0</span><i className={connected?"online":""}/><span>{connected?"All systems operational":"Backend unavailable"}</span></footer></div>
  </div>;
}
