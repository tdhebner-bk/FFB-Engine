#!/usr/bin/env python3
"""
dashboard.py — season-to-date dashboard built from every data/history/week_N.json.

    python3 dashboard.py        # (weekly_report.py also calls this automatically)

Writes reports/dashboard.html: a self-contained page (no external libraries)
with, per team, a small-multiple rank trend (Bozo poll / Power / FantasyPros)
and a playoff-odds line; this week's ballot heatmap; and a table of the latest
snapshot. Everything renders client-side from the JSON embedded in the page.
"""

import glob, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))


def collect(data_dir):
    snaps = []
    for p in glob.glob(os.path.join(data_dir, "history", "week_*.json")):
        m = re.search(r"week_(\d+)\.json$", p)
        if m: snaps.append(json.load(open(p, encoding="utf-8")))
    snaps.sort(key=lambda s: s["report_week"])
    slim = []
    for s in snaps:
        slim.append({"week": s["report_week"], "teams": {
            n: {k: t.get(k) for k in ("bozo_rank", "power_rank", "power_pts", "fp_rank", "fp_score", "standing",
                                      "record", "pf", "consensus_rank", "playoff_odds", "title_odds",
                                      "poll_ballots", "first_votes", "poll_sd")}
            for n, t in s["teams"].items()},
            "predictions": s.get("predictions", [])})
    return slim


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BKN FFB Dashboard</title>
<style>
:root{
  color-scheme:light;
  --surface-0:#f4f3f0; --surface-1:#fcfcfb; --border:#e3e2dd; --grid:#ecebe7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#8a8984;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --odds:#52514e;
  --seq-0:#f0efec; --seq-1:#cde2fb; --seq-2:#9ec5f4; --seq-3:#6da7ec; --seq-4:#3987e5; --seq-5:#256abf; --seq-6:#104281;
  --good:#006300; --bad:#b42d2d;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --surface-0:#111110; --surface-1:#1a1a19; --border:#2e2e2b; --grid:#262624;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8a8984;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --odds:#c3c2b7;
    --seq-0:#383835; --seq-1:#104281; --seq-2:#184f95; --seq-3:#256abf; --seq-4:#3987e5; --seq-5:#6da7ec; --seq-6:#b7d3f6;
    --good:#4cc24c; --bad:#e66767;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --surface-0:#111110; --surface-1:#1a1a19; --border:#2e2e2b; --grid:#262624;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8a8984;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --odds:#c3c2b7;
  --seq-0:#383835; --seq-1:#104281; --seq-2:#184f95; --seq-3:#256abf; --seq-4:#3987e5; --seq-5:#6da7ec; --seq-6:#b7d3f6;
  --good:#4cc24c; --bad:#e66767;
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:16px;margin:32px 0 4px}
.sub{color:var(--text-secondary);margin:0 0 16px}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--text-secondary);font-size:13px;margin:8px 0 12px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.key{display:inline-block;width:16px;height:2px;border-radius:1px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 12px 8px;position:relative}
.card h3{margin:0;font-size:15px;display:flex;justify-content:space-between;align-items:baseline}
.card h3 small{font-weight:400;color:var(--text-secondary);font-size:12px}
.meta{color:var(--text-secondary);font-size:12px;margin:2px 0 6px}
.up{color:var(--good)} .down{color:var(--bad)}
svg{display:block;width:100%;height:auto;overflow:visible}
svg text{fill:var(--text-muted);font-size:10px}
.tip{position:fixed;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);border-radius:8px;
  padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;z-index:9;min-width:140px}
.tip .h{color:var(--text-secondary);margin-bottom:4px}
.tip .r{display:flex;align-items:center;gap:6px;justify-content:space-between}
.tip .r b{font-variant-numeric:tabular-nums}
.tip .r span{display:flex;align-items:center;gap:6px;color:var(--text-secondary)}
.panel{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid var(--grid);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--text-secondary);font-weight:600}
.heat td{padding:0;border:2px solid var(--surface-1);text-align:center;min-width:30px;height:26px;font-size:11px}
.heat th{font-weight:500;font-size:11px;text-align:center;padding:2px}
.heat th:first-child{text-align:left;padding-right:8px}
.heat td.name{text-align:left;padding-right:8px;border:none;font-size:13px}
</style></head>
<body><main>
<h1>BKN FFB — season to date</h1>
<p class="sub" id="sub"></p>

<h2>Rank trends by team</h2>
<p class="sub">Lower is better. Cards are ordered by this week's consensus rank. Hover a card for that week's numbers.</p>
<div class="legend">
  <span><i class="key" style="background:var(--s1)"></i>Bozo poll</span>
  <span><i class="key" style="background:var(--s2)"></i>Power (model)</span>
  <span><i class="key" style="background:var(--s3)"></i>FantasyPros</span>
  <span><i class="key" style="background:var(--odds)"></i>Playoff odds (lower panel)</span>
</div>
<div class="grid" id="cards"></div>

<h2 id="heat-h">This week's ballots</h2>
<p class="sub">How many ballots put each team at each spot. Darker = more votes.</p>
<div class="panel"><table class="heat" id="heat"></table></div>

<h2>Latest snapshot</h2>
<div class="panel"><table id="tbl"></table></div>
</main>
<div class="tip" id="tip"></div>
<script>
const SNAPS = __DATA__;
const last = SNAPS[SNAPS.length-1], prev = SNAPS.length>1 ? SNAPS[SNAPS.length-2] : null;
const weeks = SNAPS.map(s=>s.week), NT = Object.keys(last.teams).length;
const wkLabel = w => w===0 ? "Pre" : "W"+w;
const ord = n => { const s=["th","st","nd","rd"], v=n%100; return n+(s[(v-20)%10]||s[v]||s[0]); };
const cRank = t => parseInt(String(t.consensus_rank).replace("t",""));
document.getElementById("sub").textContent =
  `Through ${last.week===0?"preseason":"Week "+last.week} · ${SNAPS.length} snapshot${SNAPS.length>1?"s":""} · Bozo = Google Form poll (Borda count), Power = FFB engine, FantasyPros = League Analyzer weekly power rankings`;

const SVGNS="http://www.w3.org/2000/svg";
const el=(tag,attrs,parent)=>{const e=document.createElementNS(SVGNS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);parent&&parent.appendChild(e);return e;};
const tip=document.getElementById("tip");
function showTip(ev, head, rows){
  tip.replaceChildren();
  const h=document.createElement("div"); h.className="h"; h.textContent=head; tip.appendChild(h);
  for(const [color,label,val] of rows){
    const r=document.createElement("div"); r.className="r";
    const s=document.createElement("span"); const k=document.createElement("i"); k.className="key"; k.style.background=color; s.appendChild(k);
    s.appendChild(document.createTextNode(label)); r.appendChild(s);
    const b=document.createElement("b"); b.textContent=val; r.appendChild(b); tip.appendChild(r);
  }
  tip.style.display="block";
  const x=Math.min(ev.clientX+14, innerWidth-tip.offsetWidth-8), y=Math.min(ev.clientY+14, innerHeight-tip.offsetHeight-8);
  tip.style.left=x+"px"; tip.style.top=y+"px";
}
const hideTip=()=>{tip.style.display="none";};

const SERIES=[["bozo_rank","var(--s1)","Bozo"],["power_rank","var(--s2)","Power"],["fp_rank","var(--s3)","FantasyPros"]];
const order = Object.keys(last.teams).sort((a,b)=>cRank(last.teams[a])-cRank(last.teams[b]) || last.teams[a].power_rank-last.teams[b].power_rank);
const cards=document.getElementById("cards");
for(const name of order){
  const t=last.teams[name], pt=prev&&prev.teams[name];
  const card=document.createElement("div"); card.className="card"; cards.appendChild(card);
  const h=document.createElement("h3"); h.textContent=name;
  const sm=document.createElement("small"); sm.textContent=`consensus ${t.consensus_rank}`+(t.record?` · ${t.record}`:""); h.appendChild(sm); card.appendChild(h);
  const meta=document.createElement("div"); meta.className="meta";
  const po=Math.round(t.playoff_odds*100); meta.textContent=`Playoff odds ${po}%`;
  if(pt&&pt.playoff_odds!=null){const d=Math.round((t.playoff_odds-pt.playoff_odds)*100); if(d){const s=document.createElement("span"); s.className=d>0?"up":"down"; s.textContent=` ${d>0?"▲":"▼"}${Math.abs(d)}`; meta.appendChild(s);}}
  card.appendChild(meta);

  const W=260,H=156,L=24,R=8,T=6,B=26, OH=34, OG=10;
  const svg=el("svg",{viewBox:`0 0 ${W} ${H+OG+OH}`,role:"img","aria-label":`${name} rank trend`},card);
  const x=i=> weeks.length===1 ? (L+W-R)/2 : L+(W-L-R)*i/(weeks.length-1);
  const y=r=> T+(H-T-B)*(r-1)/(NT-1);
  for(const r of [1,6,12].filter(r=>r<=NT)){
    el("line",{x1:L,x2:W-R,y1:y(r),y2:y(r),stroke:"var(--grid)","stroke-width":1},svg);
    const tx=el("text",{x:L-6,y:y(r)+3,"text-anchor":"end"},svg); tx.textContent=r;
  }
  weeks.forEach((w,i)=>{const tx=el("text",{x:x(i),y:H-6,"text-anchor":"middle"},svg); tx.textContent=wkLabel(w);});
  for(const [key,color] of SERIES){
    const pts=SNAPS.map((s,i)=>[i,s.teams[name]&&s.teams[name][key]]).filter(p=>p[1]!=null);
    if(pts.length>1) el("polyline",{points:pts.map(([i,v])=>`${x(i)},${y(v)}`).join(" "),fill:"none",stroke:color,"stroke-width":2,"stroke-linejoin":"round","stroke-linecap":"round"},svg);
    for(const [i,v] of pts) el("circle",{cx:x(i),cy:y(v),r:3.5,fill:color,stroke:"var(--surface-1)","stroke-width":2},svg);
  }
  // playoff odds panel (separate scale -> separate panel, 0-100%)
  const oy=v=>H+OG+OH-4-(OH-8)*v;
  el("line",{x1:L,x2:W-R,y1:oy(0),y2:oy(0),stroke:"var(--grid)","stroke-width":1},svg);
  el("line",{x1:L,x2:W-R,y1:oy(.5),y2:oy(.5),stroke:"var(--grid)","stroke-width":1,"stroke-dasharray":"2 3"},svg);
  const t50=el("text",{x:L-6,y:oy(.5)+3,"text-anchor":"end"},svg); t50.textContent="50%";
  const opts=SNAPS.map((s,i)=>[i,s.teams[name]&&s.teams[name].playoff_odds]).filter(p=>p[1]!=null);
  if(opts.length>1) el("polyline",{points:opts.map(([i,v])=>`${x(i)},${oy(v)}`).join(" "),fill:"none",stroke:"var(--odds)","stroke-width":2,"stroke-linejoin":"round"},svg);
  for(const [i,v] of opts) el("circle",{cx:x(i),cy:oy(v),r:3,fill:"var(--odds)",stroke:"var(--surface-1)","stroke-width":2},svg);
  // crosshair + tooltip: snap to nearest week
  const cross=el("line",{y1:T,y2:H+OG+OH-4,stroke:"var(--text-muted)","stroke-width":1,visibility:"hidden"},svg);
  const hit=el("rect",{x:0,y:0,width:W,height:H+OG+OH,fill:"transparent"},svg);
  hit.addEventListener("pointermove",ev=>{
    const bb=svg.getBoundingClientRect(), px=(ev.clientX-bb.left)*W/bb.width;
    let i=0,best=1e9; weeks.forEach((w,j)=>{const d=Math.abs(x(j)-px); if(d<best){best=d;i=j;}});
    cross.setAttribute("x1",x(i)); cross.setAttribute("x2",x(i)); cross.setAttribute("visibility","visible");
    const s=SNAPS[i].teams[name]||{};
    showTip(ev, `${name} · ${wkLabel(weeks[i])}`, [
      ["var(--s1)","Bozo", s.bozo_rank?ord(s.bozo_rank):"—"],
      ["var(--s2)","Power", s.power_rank?`${ord(s.power_rank)} (${s.power_pts})`:"—"],
      ["var(--s3)","FantasyPros", s.fp_rank?`${ord(s.fp_rank)} (${s.fp_score})`:"—"],
      ["var(--odds)","Playoff odds", s.playoff_odds!=null?Math.round(s.playoff_odds*100)+"%":"—"]]);
  });
  hit.addEventListener("pointerleave",()=>{cross.setAttribute("visibility","hidden");hideTip();});
}

// ballot heatmap
const heat=document.getElementById("heat");
const hasBallots=Object.values(last.teams).some(t=>t.poll_ballots&&t.poll_ballots.length);
if(!hasBallots){document.getElementById("heat-h").textContent="This week's ballots (none yet)";}
else{
  const hr=document.createElement("tr"); const th0=document.createElement("th"); hr.appendChild(th0);
  for(let r=1;r<=NT;r++){const th=document.createElement("th"); th.textContent=ord(r); hr.appendChild(th);} heat.appendChild(hr);
  const nb=Math.max(...Object.values(last.teams).map(t=>(t.poll_ballots||[]).length));
  const byBozo=Object.keys(last.teams).sort((a,b)=>last.teams[a].bozo_rank-last.teams[b].bozo_rank);
  for(const name of byBozo){
    const tr=document.createElement("tr"); const nm=document.createElement("td"); nm.className="name"; nm.textContent=`${last.teams[name].bozo_rank}. ${name}`; tr.appendChild(nm);
    const bal=last.teams[name].poll_ballots||[];
    for(let r=1;r<=NT;r++){
      const c=bal.filter(v=>v===r).length, td=document.createElement("td");
      const step=c===0?0:Math.min(6,Math.ceil(6*c/nb));
      td.style.background=`var(--seq-${step})`; td.style.color=step>=4?"#fff":"var(--text-secondary)";
      td.textContent=c||""; td.tabIndex=0;
      const show=ev=>showTip(ev,`${name} · ${ord(r)}`,[["var(--seq-4)","ballots",`${c} of ${bal.length}`]]);
      td.addEventListener("pointermove",show); td.addEventListener("focus",e=>{const b=td.getBoundingClientRect();show({clientX:b.right,clientY:b.bottom});});
      td.addEventListener("pointerleave",hideTip); td.addEventListener("blur",hideTip);
      tr.appendChild(td);
    }
    heat.appendChild(tr);
  }
}

// table view
const tbl=document.getElementById("tbl");
const cols=[["Team",n=>n],["Bozo",(n,t)=>t.bozo_rank??"—"],["1st votes",(n,t)=>t.first_votes??"—"],["Power",(n,t)=>`${t.power_rank} (${t.power_pts})`],
  ["Record",(n,t)=>t.record??"—"],["Standing",(n,t)=>t.standing??"n/a"],["PF",(n,t)=>t.pf!=null?t.pf.toFixed(1):"—"],
  ["FantasyPros",(n,t)=>t.fp_rank?`${t.fp_rank} (${t.fp_score})`:"—"],["Consensus",(n,t)=>t.consensus_rank],
  ["Playoff",(n,t)=>Math.round(t.playoff_odds*100)+"%"],["Title",(n,t)=>t.title_odds!=null?Math.round(t.title_odds*100)+"%":"—"],
  ["Next",(n)=>{const p=last.predictions.find(p=>p.team===n); return p?`${p.spread>0?"+":""}${p.spread||"PK"} vs ${p.opp} (${Math.round(p.win_prob*100)}%)`:"—";}]];
const thr=document.createElement("tr"); for(const [h] of cols){const th=document.createElement("th"); th.textContent=h; thr.appendChild(th);} tbl.appendChild(thr);
for(const n of order){const tr=document.createElement("tr"); for(const [,f] of cols){const td=document.createElement("td"); td.textContent=f(n,last.teams[n]); tr.appendChild(td);} tbl.appendChild(tr);}
</script>
</body></html>
"""


def write(data_dir, reports_dir):
    snaps = collect(data_dir)
    if not snaps: return None
    os.makedirs(reports_dir, exist_ok=True)
    out = os.path.join(reports_dir, "dashboard.html")
    data = json.dumps(snaps, ensure_ascii=False).replace("</", "<\\/")
    open(out, "w", encoding="utf-8").write(PAGE.replace("__DATA__", data))
    return out


if __name__ == "__main__":
    print(write(os.path.join(HERE, "data"), os.path.join(HERE, "reports")))
