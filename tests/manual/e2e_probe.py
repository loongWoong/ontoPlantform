# -*- coding: utf-8 -*-
"""Probe the API exactly the way web/index.html does, to find frontend/backend gaps."""
import json
import urllib.request

B = "http://127.0.0.1:8100"

def call(method, path, body=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(B + path, data=data if method == "POST" else None,
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra and not cond else ""))

# ---- workspace ----
st, ws = call("POST", "/api/workspaces", {"name": "高速运营", "user": "admin"})
wid = ws["id"]
st, ws2 = call("POST", "/api/workspaces", {"name": "第二个空间", "user": "admin"})
st, lst = call("GET", "/api/workspaces?user=admin")
check("workspaces list", st == 200 and isinstance(lst, list) and len(lst) >= 2)

# ---- ontology ----
st, ont = call("POST", f"/api/workspaces/{wid}/ontology", {"name": "traffic", "user": "admin"})
check("create ontology", st == 200)
st, ont = call("POST", f"/api/workspaces/{wid}/ontology/objects", {
    "name": "Vehicle", "description": "车辆",
    "properties": [{"name": "plate", "type": "string"}, {"name": "balance", "type": "number"}],
    "user": "admin"})
check("add object Vehicle", st == 200 and "Vehicle" in ont.get("objects", {}))
st, ont = call("POST", f"/api/workspaces/{wid}/ontology/objects", {
    "name": "TollStation", "description": "收费站",
    "properties": [{"name": "code", "type": "string"}, {"name": "status", "type": "string"}],
    "user": "admin"})
st, ont = call("POST", f"/api/workspaces/{wid}/ontology/relations",
               {"name": "passes", "source": "Vehicle", "target": "TollStation", "user": "admin"})
check("add relation passes", st == 200 and "passes" in ont.get("relations", {}))
st, ont = call("POST", f"/api/workspaces/{wid}/ontology/vocabulary",
               {"term": "收费站", "concept": "TollStation", "kind": "object", "user": "admin"})
check("add term 收费站", st == 200 and "收费站" in ont.get("vocabulary", {}))
st, v = call("POST", f"/api/workspaces/{wid}/ontology/validate", {"user": "admin"})
check("validate ok", st == 200 and v.get("ok") is True, json.dumps(v, ensure_ascii=False))
st, pub = call("POST", f"/api/workspaces/{wid}/ontology/publish", {"user": "admin"})
check("publish ontology -> returns {ontology, runtime}",
      st == 200 and "runtime" in pub and "ontology" in pub,
      json.dumps(pub, ensure_ascii=False)[:200])
# FRONTEND BUG CHECK: publishOntology does S.runtime = r.runtime -- but API returns dict with ontology+runtime keys. OK.

# ---- runtime describe ----
st, rt = call("GET", f"/api/workspaces/{wid}/runtime?user=admin")
check("runtime describe has objects/relations/functions",
      st == 200 and "objects" in rt and "relations" in rt and "functions" in rt,
      json.dumps(rt, ensure_ascii=False)[:300])

# ---- datasources ----
st, ds = call("POST", f"/api/workspaces/{wid}/datasources", {
    "name": "车辆主数据", "kind": "static",
    "assets": [{"name": "vehicles", "config": {"rows": [
        {"vid": "V001", "plate": "鲁A-1001", "balance": 50},
        {"vid": "V002", "plate": "鲁A-1002", "balance": 3}]}}],
    "user": "admin"})
check("connect datasource", st == 200 and ds.get("kind") == "static", json.dumps(ds, ensure_ascii=False)[:200])
st, ds_list = call("GET", f"/api/workspaces/{wid}/datasources?user=admin")
check("datasources list is array", st == 200 and isinstance(ds_list, list) and len(ds_list) == 1,
      json.dumps(ds_list, ensure_ascii=False)[:200])
# FRONTEND BUG CHECK: renderData expects d.assets to be list or dict; DataSource.to_dict stores assets as dict?
print("   datasource assets shape:", json.dumps(ds_list[0].get("assets"), ensure_ascii=False)[:200])

# ---- projections ----
st, pj = call("POST", f"/api/workspaces/{wid}/projections", {
    "name": "车辆投影", "datasource_id": ds["id"],
    "object_mappings": [{"asset": "vehicles", "object_type": "Vehicle",
                         "id_field": "vid",
                         "fields": [{"source_field": "plate", "property": "plate"},
                                    {"source_field": "balance", "property": "balance"}]}],
    "user": "admin"})
check("create projection", st == 200, json.dumps(pj, ensure_ascii=False)[:300])
st, run = call("POST", f"/api/workspaces/{wid}/projections/{pj['id']}/run", {"user": "admin"})
check("run projection summary keys", st == 200 and "objects_upserted" in run,
      json.dumps(run, ensure_ascii=False)[:200])
# FRONTEND BUG CHECK: runProjection toast uses r.applied -- API returns objects_upserted etc. r.applied is undefined!
print("   run summary:", json.dumps(run, ensure_ascii=False))

# ---- state find / get / related ----
st, objs = call("GET", f"/api/workspaces/{wid}/state/find?type=Vehicle&user=admin")
check("state find Vehicle -> 2 objects", st == 200 and len(objs) == 2, json.dumps(objs, ensure_ascii=False)[:200])
print("   object shape:", json.dumps(objs[0], ensure_ascii=False)[:200] if objs else "none")
st, one = call("GET", f"/api/workspaces/{wid}/state/get?ref=Vehicle/V001&user=admin")
check("state get", st == 200 and one.get("type") == "Vehicle", json.dumps(one, ensure_ascii=False)[:200])
st, rel = call("GET", f"/api/workspaces/{wid}/state/related?ref=Vehicle/V001&relation=passes&direction=out&user=admin")
check("state related", st == 200, json.dumps(rel, ensure_ascii=False)[:200])

# ---- capability (workflow) ----
st, cap = call("POST", f"/api/workspaces/{wid}/capabilities", {
    "key": "recharge", "name": "车辆充值", "target_type": "Vehicle",
    "description": "给车辆充值",
    "inputs": [{"name": "amount", "type": "number"}],
    "implementation": {"kind": "workflow", "steps": [
        {"as": "output", "kind": "set_property", "ref": "{{target}}",
         "properties": {"balance": "{{args.amount}}"}}]},
    "user": "admin"})
check("create workflow capability", st == 200, json.dumps(cap, ensure_ascii=False)[:300])
st, caps = call("GET", f"/api/workspaces/{wid}/capabilities?user=admin")
check("capabilities list", st == 200 and isinstance(caps, list) and len(caps) == 1)
st, pubcap = call("POST", f"/api/workspaces/{wid}/capabilities/{cap['id']}/publish", {"user": "admin"})
check("publish capability", st == 200, json.dumps(pubcap, ensure_ascii=False)[:300])
st, inv = call("POST", f"/api/workspaces/{wid}/invoke", {
    "function": "Vehicle.recharge", "target": "Vehicle/V001", "args": {"amount": 100}, "user": "admin"})
check("invoke capability", st == 200 and inv.get("ok") is True, json.dumps(inv, ensure_ascii=False)[:400])
print("   invoke result:", json.dumps(inv, ensure_ascii=False)[:300])

# ---- application ----
st, app = call("POST", f"/api/workspaces/{wid}/applications", {"name": "运营工作台", "user": "admin"})
aid = app["id"]
st, _ = call("POST", f"/api/workspaces/{wid}/applications/{aid}/views", {
    "alias": "vehicles", "title": "车辆列表", "kind": "table", "object_type": "Vehicle", "user": "admin"})
st, _ = call("POST", f"/api/workspaces/{wid}/applications/{aid}/queries", {
    "alias": "low_balance", "object_type": "Vehicle",
    "filters": {"balance__lt": 10}, "user": "admin"})
st, _ = call("POST", f"/api/workspaces/{wid}/applications/{aid}/capabilities", {
    "capability_id": cap["id"], "user": "admin"})
st, _ = call("POST", f"/api/workspaces/{wid}/applications/{aid}/agents", {
    "alias": "助手", "goal": "检查低余额车辆", "user": "admin"})
st, appget = call("GET", f"/api/workspaces/{wid}/applications/{aid}?user=admin")
check("application get has views/queries/agents/capabilities",
      all(k in appget for k in ("views", "queries", "agents", "capabilities")),
      json.dumps(appget, ensure_ascii=False)[:400])
st, qres = call("POST", f"/api/workspaces/{wid}/applications/{aid}/query", {"alias": "low_balance", "user": "admin"})
check("run app query", st == 200 and "objects" in qres, json.dumps(qres, ensure_ascii=False)[:300])
print("   query result shape:", json.dumps(qres, ensure_ascii=False)[:300])
st, ares = call("POST", f"/api/workspaces/{wid}/applications/{aid}/agents/%E5%8A%A9%E6%89%8B/agent", {"user": "admin"})
check("run app agent without llm -> clean 400 (frontend pre-checks)",
      st == 400 and "LLM" in ares.get("error", ""), f"status={st} {ares}")

# ---- ask / agent ----
st, ans = call("POST", f"/api/workspaces/{wid}/ask", {"question": "哪些车辆余额低于10？", "user": "admin"})
check("ask", st == 200 and "answer" in ans, json.dumps(ans, ensure_ascii=False)[:300])
print("   ask:", json.dumps(ans, ensure_ascii=False)[:300])
st, agr = call("POST", f"/api/workspaces/{wid}/agent", {"goal": "查看所有车辆", "user": "admin"})
check("agent without llm -> clean 400 (frontend pre-checks)",
      st == 400 and "LLM" in agr.get("error", ""), f"status={st} {agr}")

# ---- changes / audit ----
st, ch = call("GET", f"/api/workspaces/{wid}/changes?after=0&user=admin")
check("changes list", st == 200 and isinstance(ch, list) and len(ch) >= 1, json.dumps(ch, ensure_ascii=False)[:200])
print("   change event shape:", json.dumps(ch[0], ensure_ascii=False)[:300] if ch else "none")
st, au = call("GET", f"/api/workspaces/{wid}/audit?user=admin&limit=50")
check("audit list", st == 200 and isinstance(au, list) and len(au) >= 1)
print("   audit event shape:", json.dumps(au[0], ensure_ascii=False)[:300])

# ---- expand normalization (B2 regression) ----
st, app2 = call("POST", f"/api/workspaces/{wid}/applications", {"name": "展开测试", "user": "admin"})
aid2 = app2["id"]
st, r = call("POST", f"/api/workspaces/{wid}/applications/{aid2}/queries",
             {"alias": "q", "object_type": "Vehicle", "expand": ["passes"], "user": "admin"})
check("add query with expand-as-strings (B2)", st == 200, json.dumps(r, ensure_ascii=False)[:200])
st, r = call("POST", f"/api/workspaces/{wid}/applications/{aid2}/query", {"alias": "q", "user": "admin"})
check("run query with expand", st == 200 and isinstance(r.get("expand"), list), json.dumps(r, ensure_ascii=False)[:200])

# ---- contains filter with numeric needle (B3 regression) ----
st, r = call("GET", f"/api/workspaces/{wid}/state/find?type=Vehicle&plate__contains=001&user=admin")
check("contains filter with numeric-looking needle (B3)",
      st == 200 and isinstance(r, list) and len(r) >= 1,
      f"status={st} got {json.dumps(r, ensure_ascii=False)[:150]}")
st, r = call("GET", f"/api/workspaces/{wid}/state/find?type=Vehicle&balance__gt=50&user=admin")
check("numeric gt filter still works (B3)", st == 200 and isinstance(r, list) and len(r) == 1,
      f"status={st} got {json.dumps(r, ensure_ascii=False)[:150]}")

# ---- health exposes llm_configured (B4) ----
st, h = call("GET", "/api/health")
check("health has llm_configured (B4)", st == 200 and "llm_configured" in h, json.dumps(h))

# ---- 404 shape ----
st, e = call("GET", f"/api/workspaces/{wid}/state/get?ref=Nope/X&user=admin")
check("state get unknown -> null body (frontend renders 不存在)", st == 200 and e is None,
      f"status={st} {e}")

print("\n=== probe done ===")
