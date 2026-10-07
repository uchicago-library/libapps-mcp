#!/usr/bin/env python3
"""Drive uc-libapps-mcp over stdio like an MCP client and check it against the live LibApps API.

Credentials are read from an env file into the child process environment only; they are never
printed. Artifacts (tool outputs and a summary) go to --out, outside the repo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "search_guides",
    "get_guide",
    "get_guide_content",
    "list_subjects",
    "search_databases",
    "get_database",
    "find_subject_librarians",
}
FORBIDDEN_KEYS = {"email", "internal_note", "library_review", "customer_id", "account_id"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


class Run:
    def __init__(self, out: Path, secrets: list[str]) -> None:
        self.out = out
        self.secrets = [s for s in secrets if s and len(s) >= 8]
        self.results: list[dict[str, Any]] = []
        self.outputs: list[Any] = []
        self.calls = len(list(out.glob("[0-9][0-9]*-*.json")))

    def check(self, name: str, passed: bool, evidence: str = "") -> None:
        self.results.append({"check": name, "passed": bool(passed), "evidence": evidence})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" -- {evidence}" if evidence else ""))

    def save(self, label: str, data: Any) -> None:
        self.calls += 1
        self.outputs.append(data)
        (self.out / f"{self.calls:02d}-{label}.json").write_text(json.dumps(data, indent=2))


async def call(session: ClientSession, run: Run, tool: str, label: str, **args: Any) -> dict[str, Any]:
    result = await session.call_tool(tool, args)
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if not isinstance(data, dict):
        data = json.loads(result.content[0].text)
    run.save(label, data)
    return data


def walk(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}.{key}", key, item
            yield from walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk(item, f"{path}[{i}]")


def privacy_findings(outputs: list[Any], secrets: list[str]) -> list[str]:
    findings = []
    for data in outputs:
        for path, key, value in walk(data):
            if key in FORBIDDEN_KEYS:
                findings.append(f"forbidden key at {path}")
            if isinstance(value, str):
                if EMAIL_RE.search(value):
                    findings.append(f"email-like string at {path}")
                if any(s in value for s in secrets):
                    findings.append(f"secret value at {path}")
    return findings


class RawApi:
    """Independent read-only view of the API used only to cross-check what the server excludes."""

    def __init__(self, env: dict[str, str]) -> None:
        self.base = env.get("LIBAPPS_API_BASE", "https://lgapi-us.libapps.com").rstrip("/")
        self.http = httpx.Client(timeout=60, headers={"Accept-Encoding": "gzip"})
        response = self.http.post(
            f"{self.base}/1.2/oauth/token",
            data={
                "client_id": env["LIBAPPS_CLIENT_ID"],
                "client_secret": env["LIBAPPS_CLIENT_SECRET"],
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        self.token = response.json()["access_token"]

    def subpage_guides(self) -> list[str]:
        """Published guides ordered by how many visible sub-pages sit under one visible parent."""
        ranked = []
        for g in self.get("guides", {"expand": "pages", "status": "1"}):
            visible = {str(p["id"]) for p in g.get("pages") or [] if str(p.get("enable_display")) == "1"}
            children: dict[str, int] = {}
            for p in g.get("pages") or []:
                parent = str(p.get("parent_id"))
                if str(p["id"]) in visible and parent in visible and not p.get("redirect_url"):
                    children[parent] = children.get(parent, 0) + 1
            if children and int(g["type_id"]) not in (5, 6):
                ranked.append((-max(children.values()), str(g["id"])))
        return [gid for _, gid in sorted(ranked)]

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        response = self.http.get(
            f"{self.base}/1.2/{path}", params=params, headers={"Authorization": f"Bearer {self.token}"}
        )
        response.raise_for_status()
        return response.json()


def server_params(repo: Path, env: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(repo), "python", "-m", "uc_libapps_mcp"],
        env=env,
    )


async def drive_live(repo: Path, env: dict[str, str], run: Run, raw: RawApi | None, guide_id: str | None) -> None:
    stderr_path = run.out / "server-stderr.log"
    with stderr_path.open("w") as errlog:
        async with stdio_client(server_params(repo, env), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                info = init.server_info
                run.check("initialize", info.name == "uc-libapps-mcp", f"server {info.name} {info.version}")
                tools = {t.name for t in (await session.list_tools()).tools}
                run.check("tools/list has exactly the 7 tools", tools == EXPECTED_TOOLS, f"{len(tools)} tools: {sorted(tools)}")

                published: set[str] = set()
                excluded: dict[str, set[str]] = {"unpublished": set(), "private": set(), "internal/template": set()}
                if raw:
                    for g in raw.get("guides"):
                        gid, status, type_id = str(g["id"]), int(g["status"]), int(g["type_id"])
                        if type_id in (5, 6):
                            excluded["internal/template"].add(gid)
                        elif status == 0:
                            excluded["unpublished"].add(gid)
                        elif status == 2:
                            excluded["private"].add(gid)
                        else:
                            published.add(gid)
                all_excluded = set().union(*excluded.values())

                # search_guides: relevance search
                res = await call(session, run, "search_guides", "search_guides-chemistry", query="chemistry", limit=50)
                ids = [g["id"] for g in res.get("guides", [])]
                run.check(
                    "search_guides relevance search (server, ranked)",
                    res.get("ok") and res.get("source") == "server" and len(ids) > 0,
                    f"total={res.get('total')} first={[g['name'] for g in res.get('guides', [])[:3]]}",
                )
                if raw:
                    run.check(
                        "search_guides results are published only",
                        set(ids) <= published and not set(ids) & all_excluded,
                        f"{len(ids)} ids checked against {len(published)} published / {len(all_excluded)} excluded",
                    )

                # search_guides: local listing covers exactly the published set
                res = await call(session, run, "search_guides", "search_guides-all", limit=50)
                if raw:
                    expected = len(published)
                    run.check(
                        "search_guides listing total == published non-internal guides",
                        res.get("ok") and res.get("total") == expected and res.get("source") == "local",
                        f"total={res.get('total')} expected={expected}",
                    )
                    for label, pool in excluded.items():
                        if pool:
                            gid = sorted(pool)[0]
                            res = await call(session, run, "get_guide", f"get_guide-{label.replace('/', '-')}", guide_id=gid)
                            run.check(
                                f"get_guide refuses {label} guide",
                                not res.get("ok") and res.get("code") == "not_public" and "name" not in json.dumps(res),
                                f"code={res.get('code')} error={res.get('error')!r}",
                            )

                # get_guide + get_guide_content on a multi-page guide with sub-pages
                candidates = [guide_id] if guide_id else (raw.subpage_guides() if raw else ids)[:15]
                chosen = parent = None
                for gid in candidates:
                    res = await call(session, run, "get_guide", f"get_guide-{gid}", guide_id=gid)
                    if not res.get("ok"):
                        continue
                    for page in res["guide"]["pages"]:
                        subs = [s for s in page["subpages"] if s["box_count"] and not s.get("redirect_url")]
                        if subs:
                            chosen, parent = res["guide"], page
                            break
                    if chosen:
                        break
                if chosen is None:
                    run.check("found a guide with sub-pages", False, f"tried {candidates}")
                else:
                    def count(pages):
                        return sum(1 + count(p["subpages"]) for p in pages)

                    run.check(
                        "get_guide returns page tree with sub-pages",
                        True,
                        f"guide {chosen['id']} '{chosen['name']}': {count(chosen['pages'])} visible pages; "
                        f"parent '{parent['name']}' has {len(parent['subpages'])} sub-pages",
                    )
                    res = await call(
                        session, run, "get_guide_content", f"get_guide_content-{chosen['id']}",
                        guide_id=chosen["id"], page_id=parent["page_id"], include_subpages=True, max_chars=60000,
                    )
                    sub_pages = [p for p in res.get("pages", []) if p.get("parent_id") == parent["page_id"]]
                    with_text = [
                        (p, b) for p in sub_pages for b in p.get("boxes", [])
                        if not b.get("missing") and len(b.get("markdown", "")) > 40
                    ]
                    missing = sum(1 for p in res.get("pages", []) for b in p.get("boxes", []) if b.get("missing"))
                    snippet = ""
                    if with_text:
                        p, b = with_text[0]
                        snippet = f"sub-page '{p['name']}' box '{b['title']}': {b['markdown'][:120]!r}"
                    run.check(
                        "get_guide_content returns real box text for sub-pages",
                        res.get("ok") and res.get("source") == "html" and len(with_text) > 0,
                        f"{len(res.get('pages', []))} pages, {len(sub_pages)} sub-pages, {len(with_text)} sub-page boxes with text, "
                        f"{missing} missing; {snippet}",
                    )
                    res = await call(session, run, "get_guide_content", f"get_guide_content-api-{chosen['id']}",
                                     guide_id=chosen["id"], source="api", max_chars=4000)
                    run.check(
                        "get_guide_content source=api returns unplaced text blocks",
                        res.get("ok") and res.get("placement") == "unknown" and len(res.get("blocks", [])) > 0,
                        f"{len(res.get('blocks', []))} blocks in window, next_offset={res.get('next_offset')}",
                    )

                # list_subjects
                res = await call(session, run, "list_subjects", "list_subjects")
                subjects = res.get("subjects", [])
                run.check("list_subjects", res.get("ok") and len(subjects) > 0,
                          f"total={res.get('total')} first={[s['name'] for s in subjects[:3]]}")

                # search_databases + get_database
                hidden_az: set[str] = set()
                if raw:
                    hidden_az = {str(a["id"]) for a in raw.get("az") if str(a.get("enable_hidden")) == "1"}
                res = await call(session, run, "search_databases", "search_databases-jstor", query="jstor")
                dbs = res.get("databases", [])
                run.check("search_databases", res.get("ok") and len(dbs) > 0,
                          f"total={res.get('total')} first={[d['name'] for d in dbs[:3]]}")
                res_all = await call(session, run, "search_databases", "search_databases-all", limit=50)
                if raw:
                    visible_total = len(raw.get("az")) - len(hidden_az)
                    run.check(
                        "search_databases excludes hidden A-Z items",
                        res_all.get("total") == visible_total and not {d["id"] for d in dbs} & hidden_az,
                        f"total={res_all.get('total')} expected={visible_total} ({len(hidden_az)} hidden)",
                    )
                if dbs:
                    res = await call(session, run, "get_database", "get_database", database_id=dbs[0]["id"])
                    run.check("get_database from cached list", res.get("ok") and res["database"]["id"] == dbs[0]["id"],
                              f"{res.get('database', {}).get('name')!r} url={res.get('database', {}).get('url')}")
                if hidden_az:
                    hid = sorted(hidden_az)[0]
                    res = await call(session, run, "get_database", "get_database-hidden", database_id=hid)
                    run.check("get_database refuses hidden item", res.get("code") == "not_found", f"code={res.get('code')}")

                # find_subject_librarians
                private_names: set[str] = set()
                public_names: set[str] = set()
                if raw:
                    for a in raw.get("accounts", {"expand": "profile"}):
                        name = " ".join(p for p in (a.get("first_name"), a.get("last_name")) if p)
                        enabled = str((a.get("profile") or {}).get("en_page")) == "1"
                        (public_names if enabled else private_names).add(name)
                found = None
                for s in subjects[:40]:
                    res = await call(session, run, "find_subject_librarians", f"find_subject_librarians-{s['id']}", subject=s["name"])
                    if res.get("ok") and res.get("librarians"):
                        found = res
                        break
                if found is None:
                    run.check("find_subject_librarians", False, "no subject with librarians among the first 40")
                else:
                    libs = found["librarians"]
                    names = {l["name"] for l in libs}
                    run.check(
                        "find_subject_librarians returns public profiles",
                        all(l.get("profile_url") for l in libs),
                        f"subject '{found['subject']['name']}': {len(libs)} librarians; fields={sorted(set().union(*[set(l) for l in libs]))}",
                    )
                    if raw:
                        run.check(
                            "find_subject_librarians excludes non-public profiles",
                            not (names & (private_names - public_names)),
                            f"{len(names)} names checked against {len(private_names)} non-public accounts",
                        )

                # bad input shapes
                res = await call(session, run, "get_guide", "get_guide-invalid", guide_id="abc")
                run.check("invalid input error shape", res == {"ok": False, "error": res.get("error"), "code": "invalid_input"},
                          f"{res}")

    findings = privacy_findings(run.outputs, run.secrets)
    run.check("privacy: no email/internal_note/library_review/secret in any output", not findings,
              f"{len(run.outputs)} outputs scanned; findings={findings[:5]}")
    stderr_text = stderr_path.read_text()
    run.check("server stderr has no secret or token", not any(s in stderr_text for s in run.secrets),
              f"{len(stderr_text)} bytes of stderr")


async def drive_errors(repo: Path, base_env: dict[str, str], run: Run, good: dict[str, str]) -> None:
    calls = {
        "search_guides": {"query": "history"},
        "get_guide": {"guide_id": "1"},
        "get_guide_content": {"guide_id": "1"},
        "list_subjects": {},
        "search_databases": {"query": "jstor"},
        "get_database": {"database_id": "1"},
        "find_subject_librarians": {"subject": "history"},
    }
    scenarios = {
        "missing-creds": (dict(base_env), "config_missing"),
        "bad-secret": (
            {**base_env, "LIBAPPS_CLIENT_ID": good.get("LIBAPPS_CLIENT_ID", "1"),
             "LIBAPPS_CLIENT_SECRET": "not-a-real-secret-0000"},
            "auth_failed",
        ),
    }
    for label, (env, expected) in scenarios.items():
        stderr_path = run.out / f"server-stderr-{label}.log"
        with stderr_path.open("w") as errlog:
            async with stdio_client(server_params(repo, env), errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    codes = {}
                    for tool, args in calls.items():
                        res = await call(session, run, tool, f"{label}-{tool}", **args)
                        codes[tool] = res.get("code")
        sample = run.outputs[-1]
        leaked = any(s in json.dumps(run.outputs[-7:]) + stderr_path.read_text() for s in run.secrets + ["not-a-real-secret-0000"])
        run.check(f"error shape with {label}: every tool returns {expected}, nothing leaks",
                  set(codes.values()) == {expected} and not leaked, f"sample={sample}")


async def drive_call(repo: Path, env: dict[str, str], run: Run, tool: str, args: dict[str, Any]) -> None:
    with (run.out / "server-stderr-call.log").open("w") as errlog:
        async with stdio_client(server_params(repo, env), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                data = await call(session, run, tool, tool, **args)
    print(json.dumps(data, indent=2))
    findings = privacy_findings([data], run.secrets)
    run.check("privacy scan of this output", not findings, f"findings={findings[:5]}")


async def drive_doctor(repo: Path, env: dict[str, str], run: Run) -> None:
    rev = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    with (run.out / "server-stderr-doctor.log").open("w") as errlog:
        async with stdio_client(server_params(repo, env), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                run.check("server starts over stdio", True,
                          f"{init.server_info.name} {init.server_info.version} at {repo} ({rev.stdout.strip() or 'no git'})")
                tools = {t.name for t in (await session.list_tools()).tools}
                run.check("tools/list has exactly the 7 tools", tools == EXPECTED_TOOLS, f"{len(tools)} tools")
                res = await call(session, run, "list_subjects", "doctor-list_subjects")
                run.check("credentials and scopes work (list_subjects)", bool(res.get("ok")),
                          f"total={res.get('total')}" if res.get("ok") else f"code={res.get('code')} error={res.get('error')!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "check", "call"])
    parser.add_argument("tool", nargs="?", help="tool name for 'call'")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments for 'call'")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--env-file", type=Path, default=Path(os.environ.get(
        "LIBAPPS_ENV_FILE", "~/.config/uc-libapps-mcp/env")).expanduser())
    parser.add_argument("--out", type=Path, default=Path(f"/tmp/uc-libapps-mcp-verify/{time.strftime('%Y%m%d-%H%M%S')}"))
    parser.add_argument("--guide-id", help="guide to use for the sub-page content check")
    parser.add_argument("--no-cross-check", action="store_true", help="skip the independent raw API comparison")
    parser.add_argument("--only", choices=["live", "errors"], help="run one part of 'check'")
    args = parser.parse_args()
    if args.command == "call" and args.tool not in EXPECTED_TOOLS:
        parser.error(f"call needs one of: {', '.join(sorted(EXPECTED_TOOLS))}")

    args.out.mkdir(parents=True, exist_ok=True)
    file_env = load_env_file(args.env_file)
    base_env = {k: v for k, v in os.environ.items() if not k.startswith(("LIBAPPS_", "LIBGUIDES_"))}
    live_env = {**base_env, **file_env}
    raw = None
    if args.command == "check" and not args.no_cross_check and args.only != "errors":
        raw = RawApi(file_env)
    run = Run(args.out, [file_env.get("LIBAPPS_CLIENT_SECRET", "")] + ([raw.token] if raw else []))
    print(f"artifacts: {args.out}")
    if args.command == "doctor":
        asyncio.run(drive_doctor(args.repo, live_env, run))
    elif args.command == "call":
        asyncio.run(drive_call(args.repo, live_env, run, args.tool, json.loads(args.arguments)))
    else:
        if args.only in (None, "live"):
            asyncio.run(drive_live(args.repo, live_env, run, raw, args.guide_id))
        if args.only in (None, "errors"):
            asyncio.run(drive_errors(args.repo, base_env, run, file_env))
    failed = [r for r in run.results if not r["passed"]]
    summary = args.out / "summary.json"
    previous = json.loads(summary.read_text()) if summary.exists() else []
    summary.write_text(json.dumps(previous + [{"command": args.command, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                               "results": run.results}], indent=2))
    print(f"{len(run.results) - len(failed)}/{len(run.results)} checks passed; summary: {args.out / 'summary.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
