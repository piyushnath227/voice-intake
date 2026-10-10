"""Tiny read-only dashboard served at GET / (bonus). Uses textContent only, so stored data can't inject HTML."""
DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Patient Registry</title>
<style>
body{font:15px system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}
header{padding:16px 20px;background:#0f4c81;color:#fff;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
h1{font-size:18px;margin:0}main{padding:16px;overflow-x:auto}
input{padding:8px;border:1px solid #c9ced6;border-radius:6px;min-width:220px}
table{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #eceff3;white-space:nowrap}
th{background:#eef2f7;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.muted{color:#69727d;padding:12px}
</style></head><body>
<header><h1>Patient Registry <small style="opacity:.7">(demo data only)</small></h1>
<input id="q" placeholder="Filter by last name, phone, city..."></header>
<main><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table><div id="msg" class="muted"></div></main>
<script>
const COLS=["first_name","last_name","date_of_birth","sex","phone_number","email","address_line_1","address_line_2","city","state","zip_code","insurance_provider","preferred_language","created_at"];
let rows=[];
const head=document.getElementById("head"),body=document.getElementById("body"),msg=document.getElementById("msg");
COLS.forEach(c=>{const th=document.createElement("th");th.textContent=c.replace(/_/g," ");head.appendChild(th)});
function render(){const q=document.getElementById("q").value.toLowerCase();body.textContent="";
 const shown=rows.filter(r=>!q||COLS.some(c=>String(r[c]||"").toLowerCase().includes(q)));
 shown.forEach(r=>{const tr=document.createElement("tr");COLS.forEach(c=>{const td=document.createElement("td");td.textContent=r[c]??"";tr.appendChild(td)});body.appendChild(tr)});
 msg.textContent=shown.length?shown.length+" patient(s)":"No patients found."}
fetch("/patients").then(r=>r.json()).then(j=>{if(j.error)throw new Error(j.error.message);rows=j.data;render()}).catch(e=>{msg.textContent="Could not load patients: "+e.message});
document.getElementById("q").addEventListener("input",render);
</script></body></html>"""
