#!/usr/bin/env python3
"""Write infra/update/status.json for the dashboard (called by sos-update.sh; stdlib only).

usage: update_status.py <repo> <branch> <status.json> <state> <message> [key=value ...]
  keys: checked=1  started=1  finished=1  request=<id>  by=<user>  action=<check|update>
        from=<sha> to=<sha> ok=<0|1>   (records the last update result)
        step=<1-6>   progress of an update: 1 fetch, 2 backup, 3 merge, 4 build, 5 health check, 6 done
Versions (current = HEAD, latest = origin/<branch>, list of new commits) are read from the local git repo,
so this never touches the network.
"""
import json, os, subprocess, sys, tempfile
from datetime import datetime, timezone

SEP = "\x1f"


def git(repo, *a):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def commit(repo, ref):
    out = git(repo, "log", "-1", f"--format=%H{SEP}%s{SEP}%cI{SEP}%an", ref)
    if not out:
        return None
    h, s, d, a = (out.split(SEP) + ["", "", "", ""])[:4]
    return {"commit": h, "short": h[:7], "subject": s, "date": d, "author": a}


def main():
    repo, branch, path, state, message, *kv = sys.argv[1:]
    opts = dict(x.split("=", 1) for x in kv if "=" in x)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    cur, lat = commit(repo, "HEAD"), commit(repo, f"origin/{branch}")
    changes = []
    if cur and lat and cur["commit"] != lat["commit"]:
        for line in git(repo, "log", "--max-count=50", f"--format=%H{SEP}%s{SEP}%cI{SEP}%an", f"HEAD..origin/{branch}").splitlines():
            h, s, d, a = (line.split(SEP) + ["", "", "", ""])[:4]
            changes.append({"short": h[:7], "subject": s, "date": d, "author": a})
    behind = git(repo, "rev-list", "--count", f"HEAD..origin/{branch}")
    st.update({"state": state, "message": message, "branch": branch, "current": cur, "latest": lat,
               "behind": int(behind) if behind.isdigit() else 0, "changes": changes, "updated_at": now})
    if opts.get("checked"): st["checked_at"] = now
    if opts.get("started"): st["started_at"] = now; st.pop("finished_at", None); st.pop("step", None)
    if opts.get("step", "").isdigit(): st["step"] = int(opts["step"]); st["step_at"] = now
    if opts.get("finished"): st["finished_at"] = now
    if "request" in opts: st["request"] = {"id": opts["request"], "by": opts.get("by", ""), "action": opts.get("action", "")}
    if "ok" in opts:
        st["last_update"] = {"from": opts.get("from", "")[:12], "to": opts.get("to", "")[:12], "ok": opts["ok"] == "1", "at": now}
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".st-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
