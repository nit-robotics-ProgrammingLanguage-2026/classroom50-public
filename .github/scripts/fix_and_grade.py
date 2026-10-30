#!/usr/bin/env python3
"""
Automated workflow to fix autograde.yaml references and trigger grading
for student repositories in nit-robotics-ProgrammingLanguage-2026.
"""

import os
import sys
import json
import base64
import datetime
import time
import urllib.request
import urllib.error

ORG = "nit-robotics-ProgrammingLanguage-2026"
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("BOT_PAT")

if not TOKEN:
    print("Error: No GitHub token provided in environment (GITHUB_TOKEN / BOT_PAT).", file=sys.stderr, flush=True)
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "classroom50-autofix-bot",
    "X-GitHub-Api-Version": "2022-11-28",
}

def gh_api(path, method="GET", data=None):
    url = f"https://api.github.com/{path.lstrip('/')}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            if content:
                return json.loads(content.decode("utf-8"))
            return None
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} on {method} {url}: {err_msg}")

def get_repos(check_all=False):
    repos = []
    page = 1
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Fetching repository list from org {ORG} (check_all={check_all})...", flush=True)
    while True:
        data = gh_api(f"orgs/{ORG}/repos?type=all&per_page=100&page={page}&sort=pushed&direction=desc")
        if not data:
            break
        for r in data:
            name = r["name"]
            if not name.startswith("nit-programming-language-2026-"):
                continue
            pushed_at = r.get("pushed_at") or ""
            if not check_all and pushed_at < cutoff_iso:
                return repos
            repos.append(r)
        if len(data) < 100:
            break
        page += 1
    return repos

def process_repo(repo_name):
    full_repo = f"{ORG}/{repo_name}"
    wf_path = f"repos/{full_repo}/contents/.github/workflows/autograde.yaml"

    try:
        wf_file = gh_api(wf_path)
    except Exception:
        return False, "no_workflow"

    content = base64.b64decode(wf_file["content"]).decode("utf-8", errors="replace")
    sha = wf_file["sha"]

    need_fix = ("nit-robotics-ProgrammingLanguage-2026/classroom50/" in content and 
                "nit-robotics-ProgrammingLanguage-2026/classroom50-public/" not in content)

    fixed = False
    if need_fix:
        print(f"[{repo_name}] Fixing autograde.yaml reference to classroom50-public...", flush=True)
        new_content = content.replace(
            "nit-robotics-ProgrammingLanguage-2026/classroom50/",
            "nit-robotics-ProgrammingLanguage-2026/classroom50-public/"
        )
        gh_api(
            wf_path,
            method="PUT",
            data={
                "message": "[skip ci] Switch to public autograde runner",
                "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
                "sha": sha
            }
        )
        fixed = True
        time.sleep(1)

    # Check student commits
    try:
        commits = gh_api(f"repos/{full_repo}/commits?per_page=20")
    except Exception as e:
        return fixed, f"error_commits: {e}"

    student_commits = []
    latest_sha = commits[0]["sha"] if commits else None

    for c in commits:
        msg = c["commit"]["message"]
        if not (msg.startswith("[Classroom 50]") or 
                msg.startswith("Initial commit") or 
                "public autograde runner" in msg):
            student_commits.append(c)

    if not student_commits:
        return fixed, "no_student_commits"

    # Check releases count
    try:
        releases = gh_api(f"repos/{full_repo}/releases?per_page=1")
    except Exception as e:
        return fixed, f"error_releases: {e}"

    if not releases:
        print(f"[{repo_name}] Student has {len(student_commits)} commits but 0 releases. Tagging to trigger grading...", flush=True)
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        tag_ref = f"refs/tags/submit/{now_str}-{latest_sha[:7]}"
        try:
            gh_api(
                f"repos/{full_repo}/git/refs",
                method="POST",
                data={
                    "ref": tag_ref,
                    "sha": latest_sha
                }
            )
            print(f"[{repo_name}] Created tag {tag_ref} -> Autograde triggered.", flush=True)
            return True, "tagged_and_graded"
        except Exception as e:
            print(f"[{repo_name}] Failed to create tag: {e}", file=sys.stderr, flush=True)
            return fixed, f"tag_failed: {e}"

    return fixed, "ok"

def main():
    check_all = "--all" in sys.argv
    repos = get_repos(check_all=check_all)
    print(f"Found {len(repos)} candidate repositories to inspect.", flush=True)

    actions_taken = 0
    for i, r in enumerate(repos):
        name = r["name"]
        try:
            changed, status = process_repo(name)
            if changed or status not in ("ok", "no_student_commits", "no_workflow"):
                print(f"[{i+1}/{len(repos)}] {name}: {status}", flush=True)
            if changed:
                actions_taken += 1
        except Exception as e:
            print(f"[{i+1}/{len(repos)}] {name} Error: {e}", file=sys.stderr, flush=True)

    print(f"\nScan complete. Actions taken on {actions_taken} repositories.", flush=True)

if __name__ == "__main__":
    main()
