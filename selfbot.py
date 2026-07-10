#!/usr/bin/env python3
"""Poll a GitHub org for new commits and announce them in Slack as you, not as a bot."""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / ".state.json"

GITHUB_API = "https://api.github.com"
SLACK_API = "https://slack.com/api"

# GitHub keeps commit dates, so a commit can be pushed with a date older than our
# watermark (rebases, cherry-picks). Rewind the `since` window to catch those.
LOOKBACK_SECONDS = 3600
SEEN_LIMIT = 5000
COMMITS_SHOWN_PER_REPO = 5


def load_env():
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rewind(iso_ts, seconds):
    parsed = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(parsed.timestamp() - seconds, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def parse_since(value):
    """`today` (local midnight), `36h`, `7d`, or a UTC timestamp. Returns UTC ISO-8601."""
    if value == "today":
        local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    relative = re.fullmatch(r"(\d+)([hd])", value)
    if relative:
        amount, unit = int(relative[1]), relative[2]
        delta = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
        return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise SystemExit(f"--since: expected `today`, `24h`, `7d`, or 2026-07-10T00:00:00Z, got {value!r}")
    return value


def request(url, *, token=None, headers=None, data=None):
    hdrs = {"User-Agent": "github-slack-selfbot"}
    hdrs.update(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def github_get(path, token, params=None):
    url = f"{GITHUB_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, headers, raw = request(
        url,
        token=token,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if status == 200:
        return json.loads(raw)
    if status == 401:
        raise RuntimeError(
            "GitHub returned 401. If the org enforces SAML SSO, authorize the token at "
            "github.com/settings/tokens (Configure SSO)."
        )
    if status == 403 and headers.get("X-RateLimit-Remaining") == "0":
        reset = int(headers.get("X-RateLimit-Reset", "0"))
        raise RuntimeError(f"GitHub rate limit exhausted; resets at {time.ctime(reset)}")
    raise RuntimeError(f"GitHub {path} returned HTTP {status}: {raw[:200]!r}")


def slack_call(token, method, payload):
    status, _, raw = request(
        f"{SLACK_API}/{method}",
        token=token,
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=json.dumps(payload).encode("utf-8"),
    )
    if status != 200:
        raise RuntimeError(f"Slack {method} returned HTTP {status}")
    body = json.loads(raw)
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {body.get('error')}")
    return body


def repos_pushed_since(org, token, watermark, exclude):
    """Org repos with a push after `watermark`, newest first. Stops early: the API
    returns them sorted by push time, so the first stale repo ends the scan."""
    found = []
    for page in range(1, 11):
        batch = github_get(
            f"/orgs/{org}/repos",
            token,
            {"type": "all", "sort": "pushed", "direction": "desc", "per_page": 100, "page": page},
        )
        if not batch:
            break
        for repo in batch:
            if not repo.get("pushed_at") or repo["pushed_at"] <= watermark:
                return found
            if repo.get("archived") or repo["full_name"] in exclude:
                continue
            found.append(repo["full_name"])
        if len(batch) < 100:
            break
    return found


def is_bot(commit):
    author = commit.get("author")
    if author:
        return author.get("type") == "Bot" or author.get("login", "").endswith("[bot]")
    return False


def author_name(commit):
    author = commit.get("author")
    if author and author.get("login"):
        return author["login"]
    return commit["commit"]["author"]["name"]


def new_commits(repo, token, since, seen):
    commits = github_get(f"/repos/{repo}/commits", token, {"since": since, "per_page": 100})
    return [c for c in commits if c["sha"] not in seen]


def format_message(repo, commits):
    lines = [f"*{repo}* 에 새 커밋 {len(commits)}개"]
    for commit in commits[:COMMITS_SHOWN_PER_REPO]:
        subject = commit["commit"]["message"].split("\n", 1)[0]
        lines.append(f"• <{commit['html_url']}|`{commit['sha'][:7]}`> {subject} — {author_name(commit)}")
    if len(commits) > COMMITS_SHOWN_PER_REPO:
        lines.append(f"…외 {len(commits) - COMMITS_SHOWN_PER_REPO}개")
    return "\n".join(lines)


def poll_once(cfg, state, dry_run):
    poll_start = now_iso()
    seen_order = list(state["seen"])
    seen = set(seen_order)
    watermark = state.get("watermark")

    if watermark is None:
        state["watermark"] = poll_start
        state["seen"] = []
        if not dry_run:
            save_state(state)
            print("  baseline recorded: commits from now on will be announced")
        else:
            print("  no baseline yet — nothing to compare against.")
            print("  try: python3 selfbot.py --dry-run --once --since today")
        return 0

    since = rewind(watermark, cfg["lookback"]) if cfg["lookback"] else watermark
    repos = repos_pushed_since(cfg["org"], cfg["github_token"], watermark, cfg["exclude"])
    print(f"  {len(repos)} repo(s) pushed since {watermark}")

    batches = []
    for repo in repos:
        try:
            fresh = new_commits(repo, cfg["github_token"], since, seen)
        except RuntimeError as err:
            print(f"  ! {repo}: {err}", file=sys.stderr)
            continue
        if not cfg["include_bots"]:
            fresh = [c for c in fresh if not is_bot(c)]
        if fresh:
            batches.append((repo, fresh))

    total = sum(len(commits) for _, commits in batches)
    if total == 0:
        state["watermark"] = poll_start
        if not dry_run:
            save_state(state)
        print(f"  no new commits since {since} ({len(repos)} repo(s) had a push)")
        return 0

    if len(batches) > cfg["max_messages"]:
        messages = [
            f"*{cfg['org']}* 에 새 커밋 {total}개 ({len(batches)}개 저장소)\n"
            + "\n".join(f"• {repo}: {len(commits)}개" for repo, commits in batches[:20])
        ]
    else:
        messages = [format_message(repo, commits) for repo, commits in batches]

    for text in messages:
        if dry_run:
            print("\n  ┌─ would post to " + cfg["channel"])
            print("  │ " + text.replace("\n", "\n  │ "))
            print("  └─")
        else:
            slack_call(
                cfg["slack_token"],
                "chat.postMessage",
                {"channel": cfg["channel"], "text": text, "unfurl_links": False},
            )

    verb = "would post" if dry_run else "→ posted"
    print(f"\n  {verb} {len(messages)} message(s): {total} commit(s) across {len(batches)} repo(s)")

    if dry_run:
        return total

    # Trim oldest-first, so a SHA can never fall out of the dedupe window early.
    for _, commits in batches:
        seen_order.extend(c["sha"] for c in commits)
    state["seen"] = seen_order[-SEEN_LIMIT:]
    state["watermark"] = poll_start
    save_state(state)
    return total


def load_state():
    if not STATE_PATH.exists():
        return {"watermark": None, "seen": []}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state.setdefault("watermark", None)
    state.setdefault("seen", [])
    return state


def save_state(state):
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def build_config(args):
    load_env()
    slack_token = os.environ.get("SLACK_USER_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    channel = os.environ.get("SLACK_CHANNEL", "")
    org = os.environ.get("GITHUB_ORG", "")

    missing = [
        name
        for name, value in [
            ("SLACK_USER_TOKEN", slack_token),
            ("GITHUB_TOKEN", github_token),
            ("SLACK_CHANNEL", channel),
            ("GITHUB_ORG", org),
        ]
        if not value
    ]
    if missing:
        sys.exit(f"missing config: {', '.join(missing)} (copy .env.example to .env)")

    identity = slack_call(slack_token, "auth.test", {})
    if identity.get("bot_id"):
        sys.exit(
            "SLACK_USER_TOKEN looks like a bot token (auth.test returned bot_id).\n"
            "Messages would post as a bot. You need a user token (xoxp-) with the "
            "chat:write USER scope."
        )

    return {
        "slack_token": slack_token,
        "github_token": github_token,
        "channel": channel,
        "org": org,
        "exclude": {r.strip() for r in os.environ.get("GITHUB_EXCLUDE_REPOS", "").split(",") if r.strip()},
        "include_bots": os.environ.get("INCLUDE_BOTS", "").lower() in ("1", "true", "yes"),
        "max_messages": args.max_messages or int(os.environ.get("MAX_MESSAGES_PER_POLL", "10")),
        # An explicit --since window means exactly that window, no rebase padding.
        "lookback": 0 if args.since else LOOKBACK_SECONDS,
        "slack_user": identity.get("user", "?"),
        "interval": args.interval,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=60, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="poll a single time and exit")
    parser.add_argument("--dry-run", action="store_true", help="print instead of posting")
    parser.add_argument(
        "--since",
        metavar="WHEN",
        help="look back from this point instead of the saved baseline: "
        "`today`, `24h`, `7d`, or 2026-07-10T00:00:00Z",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="override MAX_MESSAGES_PER_POLL (raise it to see every repo separately)",
    )
    args = parser.parse_args()

    if args.since and not args.dry_run and not args.once:
        sys.exit("--since is for inspection; pair it with --dry-run (or at least --once)")

    cfg = build_config(args)
    state = load_state()

    print(f"posting as @{cfg['slack_user']} → {cfg['channel']}")
    print(f"watching every repo in {cfg['org']} for commits by anyone")
    if args.dry_run:
        print("dry-run: nothing will be posted, .state.json will not be touched")
    if args.since:
        state["watermark"] = parse_since(args.since)
        state["seen"] = []
        print(f"--since {args.since} → looking back to {state['watermark']}")
    elif state["watermark"] is None:
        print("first run: recording a baseline, existing commits will NOT be announced")
    else:
        print(f"baseline: announcing commits pushed after {state['watermark']}")

    while True:
        try:
            poll_once(cfg, state, args.dry_run)
        except RuntimeError as err:
            print(f"! {err}", file=sys.stderr)
        if args.once:
            return
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
