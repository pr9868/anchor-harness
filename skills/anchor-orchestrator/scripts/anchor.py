#!/usr/bin/env python3
"""
anchor.py — the deterministic helper for the Anchor workflow harness.

The agent (orchestrator) does judgment work inside nodes; THIS script does the
deterministic graph work: validation, readiness, verify (a closed DSL),
per-row source merge, run parameters, conditional (when:) skipping, map
fan-out, state/audit I/O, caching, locking, and run inspection.

Every command prints one JSON object: {"status": "ok"|"error", ...} and exits
non-zero on error, so a helper crash can never become a silent skip.

Substrate is flat files under the project root:
  workflows/<name>.md        hybrid YAML front-matter + Markdown body
  workflows/index.yaml       auto-generated catalog (reindex)
  harness.config.yaml        sources/tiers, history, notify
  runs/<id>/                 state.json, record.json, meta/, quarantine/, out/
  runs/.lock/<wf>.lock       per-workflow lock
  .cache/<wf>/<node>/<hash>/ reusable node outputs
  out/<wf>/                  current deliverables

Dependency: PyYAML (pip install pyyaml).
"""
import argparse, json, sys, os, re, hashlib, datetime, shutil, glob, time

try:
    import yaml
except ImportError:
    print(json.dumps({"status": "error", "error": "PyYAML not installed. Run: pip install pyyaml"}))
    sys.exit(2)


# ----------------------------- helpers ------------------------------------

def out(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    sys.exit(code)


def err(msg, **extra):
    out({"status": "error", "error": msg, **extra}, code=1)


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def root(args):
    return os.path.abspath(getattr(args, "root", None) or os.getcwd())


def read_config(r):
    p = os.path.join(r, "harness.config.yaml")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def resolve_audit(config, fm, flag):
    """most-specific wins: run flag > workflow front-matter > config > default 'full'."""
    if flag:
        return flag
    if fm.get("audit"):
        return fm["audit"]
    return (config.get("run") or {}).get("audit", "full")


def resolve_limits(config, fm):
    """runaway backstop: config run.limits overlaid by workflow limits."""
    lim = dict((config.get("run") or {}).get("limits") or {})
    lim.update(fm.get("limits") or {})
    return lim


def parse_workflow(path):
    """Return (front_matter_dict, body_str)."""
    with open(path) as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError(f"{os.path.basename(path)}: no YAML front-matter (--- fences) found")
    fm = yaml.safe_load(m.group(1)) or {}
    return fm, m.group(2)


def wf_path(r, name):
    p = os.path.join(r, "workflows", f"{name}.md")
    if not os.path.exists(p):
        raise FileNotFoundError(f"workflow not found: workflows/{name}.md")
    return p


def producer_of(nodes, output):
    """Which node produces a given namespaced output (e.g. 'fetch_sf.rows')."""
    for n in nodes:
        prod = n.get("produces")
        prods = prod if isinstance(prod, list) else ([prod] if prod else [])
        if output in prods:
            return n["id"]
    return None


def effective_deps(node, nodes, prev_id):
    """depends_on ∪ producers(consumes); implicit prev if both empty."""
    deps = set()
    dep = node.get("depends_on")
    if dep is not None:
        deps.update(dep)
    cons = node.get("consumes") or []
    for c in cons:
        p = producer_of(nodes, c)
        if p:
            deps.add(p)
    if node.get("depends_on") is None and not cons and prev_id:
        deps.add(prev_id)
    return deps


# ----------------------------- validate -----------------------------------

def cmd_validate(args):
    r = root(args)
    try:
        fm, _ = parse_workflow(wf_path(r, args.workflow))
    except Exception as e:
        err(str(e))
    errors = validate_fm(fm, read_config(r))
    if errors:
        out({"status": "error", "valid": False, "errors": errors}, code=1)
    out({"status": "ok", "valid": True, "workflow": fm.get("workflow"), "nodes": len(fm.get("nodes", []))})


def validate_fm(fm, config):
    errors = []
    nodes = fm.get("nodes", [])
    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        errors.append("duplicate node ids")
    idset = set(ids)
    declared_params = set((fm.get("params") or {}).keys())
    all_outputs = set()
    for n in nodes:
        prod = n.get("produces")
        for p in (prod if isinstance(prod, list) else ([prod] if prod else [])):
            all_outputs.add(p)
    # per-node checks
    for n in nodes:
        nid = n.get("id", "<no id>")
        if not nid or nid == "<no id>":
            errors.append("a node is missing an id")
        if "verify" not in n and not n.get("map"):
            errors.append(f"{nid}: every node needs a verify")
        for c in (n.get("consumes") or []):
            if producer_of(nodes, c) is None:
                errors.append(f"{nid}: consumes '{c}' has no producer")
        for d in (n.get("depends_on") or []):
            if d not in idset:
                errors.append(f"{nid}: depends_on '{d}' is not a node")
        g = n.get("gate") or {}
        if g.get("mode") == "choose" and not g.get("options"):
            errors.append(f"{nid}: gate mode 'choose' requires options")
        if n.get("model") and n["model"] not in ("light", "normal", "deep"):
            errors.append(f"{nid}: model must be light|normal|deep")
        mg = n.get("merge")
        if mg:
            sources = config.get("sources", {})
            for s in (n.get("uses_sources") or []):
                if s in sources and not sources[s].get("key"):
                    errors.append(f"{nid}: merge source '{s}' lacks a declared key")
        mp = n.get("map")
        if mp and not mp.get("over"):
            errors.append(f"{nid}: map requires 'over'")
        # param refs
        for ref in re.findall(r"\$\{params\.([a-zA-Z0-9_]+)\}", json.dumps(n)):
            if ref not in declared_params:
                errors.append(f"{nid}: references undeclared param '{ref}'")
    # cycle check (Kahn)
    prev = None
    dep_map = {}
    for n in nodes:
        dep_map[n["id"]] = effective_deps(n, nodes, prev)
        prev = n["id"]
    if _has_cycle(dep_map):
        errors.append("graph has a cycle (must be acyclic)")
    # unreachable check is informational; skipped for brevity
    return errors


def _has_cycle(dep_map):
    indeg = {k: 0 for k in dep_map}
    for k, deps in dep_map.items():
        for d in deps:
            if d in indeg:
                indeg[k] += 1
    q = [k for k, v in indeg.items() if v == 0]
    seen = 0
    # rebuild forward edges
    fwd = {k: [] for k in dep_map}
    for k, deps in dep_map.items():
        for d in deps:
            if d in fwd:
                fwd[d].append(k)
    while q:
        n = q.pop()
        seen += 1
        for m in fwd[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    return seen != len(dep_map)


def topo_order(nodes):
    prev = None
    dep_map = {}
    for n in nodes:
        dep_map[n["id"]] = effective_deps(n, nodes, prev)
        prev = n["id"]
    order, placed = [], set()
    while len(order) < len(nodes):
        progressed = False
        for n in nodes:
            if n["id"] in placed:
                continue
            if dep_map[n["id"]] <= placed:
                order.append(n["id"]); placed.add(n["id"]); progressed = True
        if not progressed:
            break
    return order


# ----------------------------- reindex ------------------------------------

def do_reindex(r):
    """Rebuild workflows/index.yaml. Returns (entries, warnings). Does not exit."""
    wfdir = os.path.join(r, "workflows")
    os.makedirs(wfdir, exist_ok=True)
    entries, warnings, names, triggers = [], [], {}, {}
    for p in sorted(glob.glob(os.path.join(wfdir, "*.md"))):
        try:
            fm, _ = parse_workflow(p)
        except Exception as e:
            warnings.append(str(e)); continue
        name = fm.get("workflow")
        entry = {
            "name": name,
            "intent": fm.get("intent", ""),
            "triggers": fm.get("triggers", []),
            "tags": fm.get("tags", []),
            "params": list((fm.get("params") or {}).keys()),
        }
        entries.append(entry)
        if name in names:
            warnings.append(f"duplicate workflow name '{name}'")
        names[name] = True
        for t in entry["triggers"]:
            key = t.lower().strip()
            if key in triggers:
                warnings.append(f"overlapping trigger '{t}' in '{name}' and '{triggers[key]}'")
            triggers[key] = name
    with open(os.path.join(wfdir, "index.yaml"), "w") as f:
        yaml.safe_dump(entries, f, sort_keys=False)
    return entries, warnings


def cmd_reindex(args):
    entries, warnings = do_reindex(root(args))
    out({"status": "ok", "indexed": len(entries), "warnings": warnings})


# ----------------------------- init ---------------------------------------

def plugin_dirs():
    """Locate the plugin's bundled templates and assets relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))          # .../scripts
    skill_dir = os.path.dirname(script_dir)                          # .../anchor-orchestrator
    plugin_root = os.path.dirname(os.path.dirname(skill_dir))        # plugin root
    return {"templates": os.path.join(skill_dir, "templates"),
            "assets": os.path.join(plugin_root, "assets")}


def cmd_init(args):
    """Scaffold a project: dirs, chosen templates, config, CLAUDE.md contract, index."""
    r = root(args)
    pd = plugin_dirs()
    created = []
    for d in ("workflows", "runs", "out", ".cache"):
        p = os.path.join(r, d)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True); created.append(d + "/")

    available = sorted(os.path.basename(f)[:-3] for f in glob.glob(os.path.join(pd["templates"], "*.md")))
    if args.all:
        chosen = available
    elif args.template:
        chosen = []
        for t in args.template:
            if t not in available:
                err(f"unknown template '{t}'", available=available)
            chosen.append(t)
    else:
        chosen = []
    copied = []
    for t in chosen:
        dst = os.path.join(r, "workflows", t + ".md")
        if os.path.exists(dst) and not args.force:
            continue
        shutil.copyfile(os.path.join(pd["templates"], t + ".md"), dst)
        copied.append(t)

    cfg_dst = os.path.join(r, "harness.config.yaml")
    cfg_src = os.path.join(pd["assets"], "harness.config.yaml")
    cfg_status = "already present"
    if (not os.path.exists(cfg_dst) or args.force) and os.path.exists(cfg_src):
        shutil.copyfile(cfg_src, cfg_dst); cfg_status = "created"

    contract_src = os.path.join(pd["assets"], "CLAUDE.contract.md")
    claude_dst = os.path.join(r, "CLAUDE.md")
    contract_status = "skipped (no contract asset)"
    if os.path.exists(contract_src):
        contract = open(contract_src).read()
        existing = open(claude_dst).read() if os.path.exists(claude_dst) else ""
        if "# Anchor harness" in existing:
            contract_status = "already present"
        else:
            with open(claude_dst, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(("\n" if existing else "") + contract)
            contract_status = "appended to CLAUDE.md" if existing else "created CLAUDE.md"

    entries, warnings = do_reindex(r)
    out({"status": "ok",
         "created_dirs": created,
         "templates_copied": copied,
         "templates_available": available,
         "config": cfg_status,
         "contract": contract_status,
         "indexed": len(entries),
         "index_warnings": warnings,
         "next": "Ask the agent to run a workflow, e.g. 'refresh the dashboard for Q2'."})


# ----------------------------- match --------------------------------------

def cmd_match(args):
    r = root(args)
    idx_path = os.path.join(r, "workflows", "index.yaml")
    if not os.path.exists(idx_path):
        do_reindex(r)
    with open(idx_path) as f:
        entries = yaml.safe_load(f) or []
    q = args.query.lower()
    scored = []
    for e in entries:
        score = 0
        for t in e.get("triggers", []):
            if t.lower() in q or q in t.lower():
                score += 3
        for tag in e.get("tags", []):
            if tag.lower() in q:
                score += 1
        if score:
            scored.append((score, e["name"]))
    scored.sort(reverse=True)
    out({"status": "ok", "candidates": [{"name": n, "score": s} for s, n in scored]})


# ----------------------------- start --------------------------------------

def cmd_start(args):
    r = root(args)
    try:
        fm, _ = parse_workflow(wf_path(r, args.workflow))
    except Exception as e:
        err(str(e))
    errors = validate_fm(fm, read_config(r))
    if errors:
        err("validation failed", errors=errors)
    # params
    params = {}
    declared = fm.get("params") or {}
    for k, spec in declared.items():
        if isinstance(spec, dict) and "default" in spec:
            params[k] = spec["default"]
    for kv in (args.param or []):
        if "=" not in kv:
            err(f"bad --param '{kv}', expected k=v")
        k, v = kv.split("=", 1)
        params[k] = v
    for k, spec in declared.items():
        if isinstance(spec, dict) and spec.get("required") and k not in params:
            err(f"missing required param '{k}'")
    # lock
    lockdir = os.path.join(r, "runs", ".lock")
    os.makedirs(lockdir, exist_ok=True)
    lock = os.path.join(lockdir, f"{args.workflow}.lock")
    if os.path.exists(lock) and args.scope != "resume":
        with open(lock) as f:
            holder = f.read().strip()
        err(f"workflow already running (run {holder})")
    run_id = now_iso()
    with open(lock, "w") as f:
        f.write(run_id)
    run_dir = os.path.join(r, "runs", run_id)
    for sub in ("", "meta", "quarantine", "out"):
        os.makedirs(os.path.join(run_dir, sub), exist_ok=True)
    cfg = read_config(r)
    audit = resolve_audit(cfg, fm, args.audit)
    limits = resolve_limits(cfg, fm)
    state = {
        "workflow": args.workflow, "run_id": run_id, "scope": args.scope or "full",
        "as_of": datetime.datetime.now().isoformat(), "started_epoch": time.time(),
        "params": params, "audit": audit, "limits": limits,
        "harness_version": fm.get("harness_version"), "version": fm.get("version"),
        "nodes": {n["id"]: {"status": "pending"} for n in fm.get("nodes", [])},
    }
    _write_state(run_dir, state)
    if audit != "none":
        _append_record(run_dir, {"event": "start", "run_id": run_id, "params": params,
                                 "audit": audit, "ts": now_iso()})
    out({"status": "ok", "run_id": run_id, "params": params, "audit": audit,
         "limits": limits, "as_of": state["as_of"]})


def _run_dir(r, run_id):
    return os.path.join(r, "runs", run_id)


def _write_state(run_dir, state):
    with open(os.path.join(run_dir, "state.json"), "w") as f:
        json.dump(state, f, indent=2, default=str)


def _read_state(run_dir):
    with open(os.path.join(run_dir, "state.json")) as f:
        return json.load(f)


def _append_record(run_dir, entry):
    with open(os.path.join(run_dir, "record.json"), "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_wf_for_run(r, state):
    fm, _ = parse_workflow(wf_path(r, state["workflow"]))
    return fm


# ----------------------------- verify DSL ---------------------------------

def eval_expr(expr, ctx):
    """Evaluate the closed verify/when DSL. ctx has meta{}, params{}, nodes_meta{}.
    Grammar: term ((AND|OR) term)* ; left-to-right, no parens except exists()/schema()."""
    tokens = re.split(r"\s+(AND|OR)\s+", expr.strip())
    result = _eval_term(tokens[0], ctx)
    i = 1
    while i < len(tokens):
        op, term = tokens[i], tokens[i + 1]
        val = _eval_term(term, ctx)
        result = (result and val) if op == "AND" else (result or val)
        i += 2
    return result


def _eval_term(term, ctx):
    term = term.strip()
    m = re.match(r"exists\((.+)\)$", term)
    if m:
        ref = m.group(1).strip()
        return os.path.exists(ctx["resolve_output"](ref))
    m = re.match(r"schema\((.+)\)$", term)
    if m:
        return ctx["meta"].get("schema_name") == m.group(1).strip()
    if term == "non_empty":
        return bool(ctx["meta"].get("non_empty"))
    m = re.match(r"(.+?)\s*(>=|<=|==|!=|>|<)\s*(.+)$", term)
    if m:
        field, op, num = m.group(1).strip(), m.group(2), m.group(3).strip()
        left = _resolve_field(field, ctx)
        try:
            right = float(num)
            left = float(left)
        except (TypeError, ValueError):
            right = num.strip('"\'')
        return _cmp(left, op, right)
    raise ValueError(f"cannot parse verify term: '{term}'")


def _resolve_field(field, ctx):
    if field.startswith("params."):
        return ctx["params"].get(field[7:])
    if field in ("row_count", "col_count"):
        return ctx["meta"].get(field)
    if field.startswith("metrics."):
        return (ctx["meta"].get("metrics") or {}).get(field[8:])
    m = re.match(r"([a-zA-Z0-9_]+)\.metrics\.([a-zA-Z0-9_]+)$", field)
    if m:
        nm = ctx["nodes_meta"].get(m.group(1), {})
        return (nm.get("metrics") or {}).get(m.group(2))
    raise ValueError(f"unresolvable field: '{field}'")


def _cmp(a, op, b):
    try:
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b,
                "==": a == b, "!=": a != b}[op]
    except TypeError:
        return False


def _node_meta(run_dir, node_id):
    p = os.path.join(run_dir, "meta", f"{node_id}.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def _ctx_for(r, run_dir, state, node_id):
    all_meta = {}
    for p in glob.glob(os.path.join(run_dir, "meta", "*.json")):
        with open(p) as f:
            all_meta[os.path.basename(p)[:-5]] = json.load(f)
    return {
        "meta": all_meta.get(node_id, {}),
        "nodes_meta": all_meta,
        "params": state.get("params", {}),
        "resolve_output": lambda ref: os.path.join(r, "out", state["workflow"], ref.replace(".", "_")),
    }


# ----------------------------- ready --------------------------------------

def cmd_ready(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    # run budget backstop (config/workflow limits)
    limits = state.get("limits") or {}
    if limits:
        done_count = sum(1 for v in state["nodes"].values() if v["status"] == "done")
        if limits.get("max_nodes") and done_count >= limits["max_nodes"]:
            out({"status": "ok", "ready": [], "blocked": {},
                 "halt": f"budget: max_nodes ({limits['max_nodes']}) reached"})
        if limits.get("max_minutes"):
            elapsed_min = (time.time() - state.get("started_epoch", time.time())) / 60.0
            if elapsed_min >= limits["max_minutes"]:
                out({"status": "ok", "ready": [], "blocked": {},
                     "halt": f"budget: max_minutes ({limits['max_minutes']}) reached"})
    fm = _load_wf_for_run(r, state)
    nodes = fm.get("nodes", [])
    nmap = {n["id"]: n for n in nodes}
    prev = None
    dep_map = {}
    for n in nodes:
        dep_map[n["id"]] = effective_deps(n, nodes, prev)
        prev = n["id"]
    st = state["nodes"]
    # cascade condition skips first
    changed = True
    while changed:
        changed = False
        for n in nodes:
            nid = n["id"]
            if st[nid]["status"] != "pending":
                continue
            # upstream skipped → cascade unless fallback
            for d in dep_map[nid]:
                if st.get(d, {}).get("status", "").startswith("skipped") and not n.get("fallback"):
                    st[nid] = {"status": "skipped:upstream"}; changed = True
            if st[nid]["status"] != "pending":
                continue
            # when: predicate (only if deps done so metrics exist)
            if n.get("when") and dep_map[nid] <= _done_set(st):
                ctx = _ctx_for(r, run_dir, state, nid)
                try:
                    if not eval_expr(n["when"], ctx):
                        st[nid] = {"status": "skipped:condition"}; changed = True
                except Exception as e:
                    st[nid] = {"status": "error", "detail": f"when: {e}"}; changed = True
    _write_state(run_dir, state)
    done = _done_set(st)
    ready = [n["id"] for n in nodes
             if st[n["id"]]["status"] == "pending" and dep_map[n["id"]] <= done]
    blocked = {n["id"]: sorted(dep_map[n["id"]] - done) for n in nodes
               if st[n["id"]]["status"] == "pending" and not (dep_map[n["id"]] <= done)}
    gates = {nid: nmap[nid].get("gate") for nid in ready if nmap[nid].get("gate")}
    models = {nid: nmap[nid].get("model") for nid in ready if nmap[nid].get("model")}
    out({"status": "ok", "ready": ready, "blocked": blocked, "gates": gates,
         "models": models, "done": sorted(done)})


def _done_set(st):
    return {k for k, v in st.items() if v["status"] in ("done", "skipped:condition", "skipped:upstream")}


# ----------------------------- record -------------------------------------

def cmd_record(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    fm = _load_wf_for_run(r, state)
    node = next((n for n in fm["nodes"] if n["id"] == args.node), None)
    meta = json.loads(args.meta) if args.meta else {}
    # materialize produced outputs at out/<wf>/<ref_mangled> so exists()/verify resolve
    produced = []
    if node:
        prod = node.get("produces")
        prod = prod if isinstance(prod, list) else ([prod] if prod else [])
        audit = state.get("audit", "full")
        outdir = os.path.join(r, "out", state["workflow"])
        snapdir = os.path.join(run_dir, "out")
        os.makedirs(outdir, exist_ok=True)
        if audit == "full":                       # output snapshots only in full audit
            os.makedirs(snapdir, exist_ok=True)
        for ref in prod:
            dest = os.path.join(outdir, ref.replace(".", "_"))
            if args.output and os.path.exists(args.output):
                shutil.copyfile(args.output, dest)
            elif not os.path.exists(dest):
                open(dest, "a").close()          # touch so exists() passes
            if audit == "full":
                shutil.copyfile(dest, os.path.join(snapdir, ref.replace(".", "_")))
            produced.append(ref)
        meta.setdefault("non_empty", any(os.path.getsize(os.path.join(outdir, p.replace(".", "_"))) > 0 for p in prod) if prod else False)
    with open(os.path.join(run_dir, "meta", f"{args.node}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    state["nodes"][args.node] = {"status": "done", "output": args.output, "produced": produced, "meta": meta}
    _write_state(run_dir, state)
    if state.get("audit", "full") == "full":       # per-node record only in full audit
        _append_record(run_dir, {"event": "record", "node": args.node, "output": args.output,
                                 "produced": produced, "meta": meta, "ts": now_iso()})
    out({"status": "ok", "recorded": args.node, "produced": produced})


# ----------------------------- verify -------------------------------------

def cmd_verify(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    fm = _load_wf_for_run(r, state)
    node = next((n for n in fm["nodes"] if n["id"] == args.node), None)
    if not node:
        err(f"no node '{args.node}'")
    expr = node.get("verify", "")
    if expr.startswith("subagent:"):
        out({"status": "ok", "pass": None, "delegate": "subagent",
             "criterion": expr.split(":", 1)[1].strip()})
    ctx = _ctx_for(r, run_dir, state, args.node)
    try:
        ok = eval_expr(expr, ctx)
    except Exception as e:
        err(f"verify unresolvable: {e}")
    out({"status": "ok", "pass": bool(ok), "expr": expr})


# ----------------------------- gate ---------------------------------------

def cmd_gate(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    decision = "approved" if args.approve else ("rejected" if args.reject else f"choose:{args.choose}")
    if state.get("audit", "full") != "none":       # gate decisions logged unless audit off
        _append_record(run_dir, {"event": "gate", "node": args.node, "decision": decision,
                                 "approver": os.environ.get("USER", "user"), "ts": now_iso()})
    st = state["nodes"].get(args.node, {})
    st["gate"] = decision
    state["nodes"][args.node] = st
    _write_state(run_dir, state)
    out({"status": "ok", "node": args.node, "decision": decision})


# ----------------------------- status / list ------------------------------

def cmd_status(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    st = state["nodes"]
    view = {
        "workflow": state["workflow"], "run_id": state["run_id"],
        "scope": state["scope"], "as_of": state["as_of"], "params": state.get("params", {}),
        "done": [k for k, v in st.items() if v["status"] == "done"],
        "skipped": [k for k, v in st.items() if v["status"].startswith("skipped")],
        "pending": [k for k, v in st.items() if v["status"] == "pending"],
        "errors": [k for k, v in st.items() if v["status"] == "error"],
        "gates": {k: v.get("gate") for k, v in st.items() if v.get("gate")},
    }
    quarantine = os.path.join(run_dir, "quarantine")
    view["quarantined"] = os.listdir(quarantine) if os.path.isdir(quarantine) else []
    out({"status": "ok", "view": view})


def cmd_list(args):
    r = root(args)
    runs_dir = os.path.join(r, "runs")
    runs = []
    if os.path.isdir(runs_dir):
        for d in sorted(os.listdir(runs_dir)):
            sp = os.path.join(runs_dir, d, "state.json")
            if os.path.exists(sp):
                with open(sp) as f:
                    s = json.load(f)
                statuses = [v["status"] for v in s["nodes"].values()]
                runs.append({"run_id": d, "workflow": s["workflow"],
                             "done": statuses.count("done"), "total": len(statuses)})
    out({"status": "ok", "runs": runs})


# ----------------------------- end / restore ------------------------------

def cmd_end(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    lock = os.path.join(r, "runs", ".lock", f"{state['workflow']}.lock")
    if os.path.exists(lock):
        os.remove(lock)
    if state.get("audit", "full") == "none":       # purge the whole run — no trail
        shutil.rmtree(run_dir, ignore_errors=True)
        out({"status": "ok", "ended": state["run_id"], "audit": "none", "purged": True})
    _append_record(run_dir, {"event": "end", "ts": now_iso()})
    # retention
    cfg = read_config(r)
    retain = (cfg.get("history") or {}).get("retain", 20)
    runs_dir = os.path.join(r, "runs")
    dirs = sorted([d for d in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, d)) and d != ".lock"])
    for old in dirs[:-retain] if retain else []:
        shutil.rmtree(os.path.join(runs_dir, old), ignore_errors=True)
    out({"status": "ok", "ended": state["run_id"]})


def cmd_restore(args):
    r = root(args)
    run_dir = _run_dir(r, args.run)
    state = _read_state(run_dir)
    src = os.path.join(run_dir, "out")
    dst = os.path.join(r, "out", state["workflow"])
    if os.path.isdir(src):
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    out({"status": "ok", "restored_from": state["run_id"]})


# ----------------------------- plan ---------------------------------------

def cmd_plan(args):
    r = root(args)
    try:
        fm, _ = parse_workflow(wf_path(r, args.workflow))
    except Exception as e:
        err(str(e))
    nodes = fm.get("nodes", [])
    order = topo_order(nodes)
    nmap = {n["id"]: n for n in nodes}
    plan = []
    for nid in order:
        n = nmap[nid]
        plan.append({
            "node": nid, "autonomy": n.get("autonomy", "guided"),
            "model": n.get("model", "normal"),
            "gate": n.get("gate"), "when": n.get("when"),
            "map": bool(n.get("map")), "merge": bool(n.get("merge")),
            "produces": n.get("produces"),
        })
    out({"status": "ok", "workflow": fm.get("workflow"), "order": order, "plan": plan})


# ----------------------------- CLI ----------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="anchor", description="Anchor deterministic helper")
    p.add_argument("--root", help="project root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, args=()):
        sp = sub.add_parser(name)
        for a, kw in args:
            sp.add_argument(a, **kw)
        sp.set_defaults(func=fn)
        return sp

    add("init", cmd_init, [("--template", {"action": "append"}),
                           ("--all", {"action": "store_true"}),
                           ("--force", {"action": "store_true"})])
    add("validate", cmd_validate, [("workflow", {})])
    add("reindex", cmd_reindex)
    add("match", cmd_match, [("query", {})])
    add("plan", cmd_plan, [("workflow", {}), ("--scope", {"default": "full"})])
    add("start", cmd_start, [("workflow", {}), ("--scope", {"default": "full"}),
                             ("--param", {"action": "append"}), ("--audit", {})])
    add("ready", cmd_ready, [("run", {})])
    add("verify", cmd_verify, [("run", {}), ("--node", {"required": True})])
    add("record", cmd_record, [("run", {}), ("--node", {"required": True}),
                               ("--output", {}), ("--meta", {})])
    add("gate", cmd_gate, [("run", {}), ("--node", {"required": True}),
                           ("--approve", {"action": "store_true"}),
                           ("--reject", {"action": "store_true"}),
                           ("--choose", {})])
    add("status", cmd_status, [("run", {})])
    add("list", cmd_list)
    add("end", cmd_end, [("run", {})])
    add("restore", cmd_restore, [("run", {})])
    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except FileNotFoundError as e:
        err(str(e))
    except Exception as e:
        err(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
