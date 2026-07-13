#!/usr/bin/env python3
"""Poll GitHub for team members' new commits (across all branches) and post a
detailed, human-sounding achievement update to Slack — each member's own commits
posted as that member, written by Claude from what they actually committed."""

import argparse
import json
import os
import random
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
MEMBERS_PATH = ROOT / "members.json"

GITHUB_API = "https://api.github.com"
SLACK_API = "https://slack.com/api"
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-4-8"

# GitHub keeps commit dates, so a commit can be pushed with a date older than our
# watermark (rebases, cherry-picks). Rewind the `since` window to catch those.
LOOKBACK_SECONDS = 3600
SEEN_LIMIT = 5000
MAX_BRANCHES = 40           # branches scanned per repo before we truncate + warn
MAX_FILES_IN_PROMPT = 40
MAX_DIFF_CHARS = 6000
MAX_OUTPUT_TOKENS = 2048    # room for a long, detailed post

SYSTEM_BASE = """\
당신은 소프트웨어 엔지니어 본인입니다. 방금 GitHub에 푸시한 커밋들을 팀 단체 Slack \
채널에 직접 공유하는 글을 1인칭 존댓말로 씁니다. 봇이 아니라 당신이 직접 쓴 것처럼 \
읽혀야 합니다.

목표: 방금 푸시한 이 작업의 성과와 기대효과가 확실히 드러나게, 자신감 있게 어필하는 \
글을 길고 자세하게 쓰세요. 겸손하게 축소하지 말고, 내가 무엇을 이뤘고 그게 왜 의미 \
있는지를 분명히 말하세요. 단, 근거 없는 과장이나 허위는 금지입니다.

이 글은 하루 요약이 아니라, 방금 올린 커밋을 그때그때 공유하는 글입니다. 하루에도 \
여러 번 올라가므로, "오늘은", "오늘 하루" 같은 하루 단위 도입부로 시작하지 마세요. \
바로 이번 작업 내용으로 들어가세요.

글의 흐름:
- 라벨이나 소제목("한 일", "성과" 같은)으로 나누지 마세요. 하나의 이야기처럼 자연스럽게 \
이어지는 글로 쓰세요. 어떤 문제·필요에서 출발했는지 → 그래서 무엇을 어떻게 했는지 → \
그 결과 무엇이 나아지는지가 자연스럽게 연결되게.
- 매번 같은 방식으로 시작하지 마세요. 특히 "방금 ~했습니다", "방금 ~를 완료했습니다" 같은 \
정형화된 도입을 쓰지 말고, 글마다 도입을 다르게(문제 상황·결과·작업 대상·배경 등 다양한 \
지점에서) 시작하세요. 첫 문장이 매번 비슷해지지 않게 하세요.
- 여러 문단으로 나눠 읽기 좋게 쓰되, 문단과 문단, 문장과 문장이 매끄럽게 이어지도록 \
하세요. 기술적으로 까다롭거나 신경 쓴 지점은 흐름 속에서 자연스럽게 녹여 설명하세요.
- 정량 수치(바꾼 파일 수, 추가/삭제된 줄 수, 커밋 수)는 별도 나열이 아니라 문장 속에 \
자연스럽게 녹여 규모를 보여주세요.
- 불릿은 정말 필요할 때만 최소한으로. 기본은 이어지는 산문입니다.

내용 규칙:
- 어필은 세게 하되, 근거는 제공된 커밋 정보(메시지, 변경 파일, 줄 수, 있으면 diff)에 \
실제로 드러난 사실에 둡니다. 없는 기능·성과·영향을 지어내지 마세요.
- 정량 수치는 제공된 실제 숫자만 인용하세요. 숫자를 임의로 만들지 마세요.
- 기대효과는 커밋 내용에서 합리적으로 이어지는 범위에서 서술하세요(예: 버그 수정 → \
오류 감소·안정성 향상). 검증 안 된 성능 수치나 매출 같은 건 지어내지 마세요.
- 실제 사람이 자기 성과를 설명하듯 자연스럽게. 뻔한 홍보 문구 톤은 피하세요.
- 이모지는 매번 같은 자리에 같은 걸 붙이지 말고, 사람이 쓰듯 그때그때 다르게 쓰세요. \
특히 항상 웃는 얼굴이나 :) 로 글을 끝맺지 마세요. (구체적 지침은 아래 사용자 메시지 끝에 있습니다.)
- 해시태그·마크다운 제목(#)은 쓰지 마세요.
- 커밋 링크나 커밋 해시는 넣지 마세요."""

AI_CLAUSE = """\
- 이 작업은 AI 개발 도구(예: Claude Code)를 적극 활용해 진행했습니다. 초점은 "업무 자체를 \
어떻게 AI로 풀었는가"에 두세요 — 어떤 문제·작업에 AI를 어떻게 활용해 더 빠르고 정확하게, \
더 나은 방식으로 해냈는지를 이야기의 흐름 속에 녹이세요. 커밋에 Claude가 공동 작성자로 \
찍혀 있더라도 그건 언급하지 마세요. "동료 Claude와 함께 했다" 같은 공동작성 크레딧이나 \
사인오프로 끝맺지 말고, AI를 도구로 활용한 작업 방식과 그 성과가 드러나게 하세요. 실제 \
한 작업의 범위를 벗어난 과장은 하지 마세요."""

SYSTEM_TAIL = "\n\n출력은 Slack에 그대로 올릴 본문 텍스트만. 다른 설명은 하지 마세요."


def build_system_prompt(emphasize_ai, work_context):
    parts = [SYSTEM_BASE]
    if emphasize_ai:
        parts.append(AI_CLAUSE)
    if work_context:
        parts.append(f"추가 배경(사실로 취급하되 벗어나지 마세요): {work_context}")
    return "\n".join(parts) + SYSTEM_TAIL


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
        with urllib.request.urlopen(req, timeout=60) as resp:
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


def anthropic_message(system, user_text, api_key, model):
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
    }
    status, _, raw = request(
        ANTHROPIC_API,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    if status != 200:
        raise RuntimeError(f"Anthropic returned HTTP {status}: {raw[:200]!r}")
    body = json.loads(raw)
    if body.get("stop_reason") == "refusal":
        raise RuntimeError("Anthropic declined to write this one (refusal).")
    parts = [b["text"] for b in body.get("content", []) if b.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("Anthropic returned an empty message.")
    return text


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


def branch_names(repo, token):
    names = []
    for page in range(1, 6):
        batch = github_get(f"/repos/{repo}/branches", token, {"per_page": 100, "page": page})
        if not batch:
            break
        names.extend(b["name"] for b in batch)
        if len(batch) < 100:
            break
    return names


def commits_on_ref(repo, ref, token, since):
    params = {"since": since, "per_page": 100}
    if ref:
        params["sha"] = ref
    return github_get(f"/repos/{repo}/commits", token, params)


def gather_commits(repo, token, since, scan_branches):
    """Commits in `repo` pushed since `since`. With scan_branches, walks every branch
    (deduped by SHA) so feature-branch work is caught, not just the default branch."""
    if not scan_branches:
        return commits_on_ref(repo, None, token, since)
    names = branch_names(repo, token)
    if len(names) > MAX_BRANCHES:
        print(
            f"  ! {repo}: {len(names)} branches; scanning first {MAX_BRANCHES} only "
            f"(exclude it or set SCAN_BRANCHES=false)",
            file=sys.stderr,
        )
        names = names[:MAX_BRANCHES]
    collected = {}
    for name in names:
        for c in commits_on_ref(repo, name, token, since):
            collected[c["sha"]] = c
    return list(collected.values())


def is_bot(commit):
    author = commit.get("author")
    if author:
        return author.get("type") == "Bot" or author.get("login", "").endswith("[bot]")
    return False


def author_login(commit):
    author = commit.get("author")
    return author.get("login") if author else None


def commit_emails(commit):
    meta = commit.get("commit", {})
    return {
        (meta.get("author") or {}).get("email", "").lower(),
        (meta.get("committer") or {}).get("email", "").lower(),
    } - {""}


def owner_index(commit, members):
    """Which member authored this commit? Match by GitHub login or commit email."""
    login = (author_login(commit) or "").lower()
    emails = commit_emails(commit)
    for i, m in enumerate(members):
        if m.get("match_all"):
            return i
        if login and login in m["logins"]:
            return i
        if emails & m["emails"]:
            return i
    return None


def commit_detail(repo, sha, token):
    """Per-commit metadata: message, changed files, line counts, and (optionally used) patch."""
    data = github_get(f"/repos/{repo}/commits/{sha}", token)
    files = data.get("files", []) or []
    return {
        "sha": sha,
        "url": data["html_url"],
        "message": data["commit"]["message"],
        "additions": data.get("stats", {}).get("additions", 0),
        "deletions": data.get("stats", {}).get("deletions", 0),
        "files": [
            {
                "name": f.get("filename", "?"),
                "status": f.get("status", "?"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch", ""),
            }
            for f in files
        ],
    }


# Commit messages often carry trailers (Co-Authored-By, Signed-off-by, "Generated
# with Claude Code"). Left in, the model fixates on them and ends every post with a
# "co-authored with Claude" credit. Strip them so it writes about the work.
TRAILER_RE = re.compile(
    r"^\s*(co-authored-by:|signed-off-by:|co-committed-by:|reviewed-by:|🤖|generated with\b)",
    re.IGNORECASE,
)


def clean_message(message):
    kept = [ln for ln in message.splitlines() if not TRAILER_RE.match(ln)]
    return "\n".join(kept).strip()


def prompt_from_commits(repo, details, send_diff):
    total_files = sum(len(d["files"]) for d in details)
    total_add = sum(d["additions"] for d in details)
    total_del = sum(d["deletions"] for d in details)
    lines = [
        f"저장소: {repo}",
        f"커밋 {len(details)}개 · 변경 파일 {total_files}개 · +{total_add} / -{total_del} 줄\n",
    ]
    for detail in details:
        msg = clean_message(detail["message"])
        subject, _, rest = msg.partition("\n")
        lines.append(f"■ 커밋 {detail['sha'][:7]}: {subject}")
        if rest.strip():
            lines.append(f"  설명: {rest.strip()}")
        lines.append(f"  변경량: +{detail['additions']} / -{detail['deletions']}")
        for f in detail["files"][:MAX_FILES_IN_PROMPT]:
            lines.append(f"  - {f['status']} {f['name']} (+{f['additions']}/-{f['deletions']})")
        if len(detail["files"]) > MAX_FILES_IN_PROMPT:
            lines.append(f"  - …외 {len(detail['files']) - MAX_FILES_IN_PROMPT}개 파일")
        if send_diff:
            budget = MAX_DIFF_CHARS
            for f in detail["files"]:
                if not f["patch"] or budget <= 0:
                    continue
                chunk = f["patch"][:budget]
                budget -= len(chunk)
                lines.append(f"  diff {f['name']}:\n{chunk}")
        lines.append("")
    lines.append("위 커밋들을 바탕으로, 내가 한 이 작업을 팀 단톡방에 공유하는 글을 써줘.")
    return "\n".join(lines)


# Each post is a separate stateless LLM call, so it can't vary emoji across posts
# on its own — it converges to the same default (a trailing :) ). We randomize the
# emoji instruction per call so, over many posts, the style actually varies.
EMOJI_HINTS = [
    "이번 글에는 이모지를 아예 쓰지 마세요. 담백하게.",
    "이번 글에는 이모지를 쓰지 마세요.",
    "이모지 하나만, 글 중간의 내용에 어울리는 지점에 자연스럽게 넣으세요. 맨 끝은 피하세요.",
    "이모지 하나 정도를 내용에 맞는 걸로 골라, 문장 사이 자연스러운 곳에 넣으세요.",
    "이모지를 넣되 웃는 얼굴 말고 작업 내용에 어울리는 걸로. 위치도 끝이 아닌 곳에.",
    "이모지 한두 개를 내용에 맞게, 매번 같은 게 아니라 다른 걸로 골라 자연스럽게 배치하세요.",
]


def emoji_directive():
    return (
        "이모지 지침(이 글에만 적용): "
        + random.choice(EMOJI_HINTS)
        + " 항상 :) 나 웃는 얼굴로 끝맺지 말고, 사람이 쓰듯 매번 다르게."
    )


# Same stateless-convergence problem for the opening line — every post drifts to
# "방금 ~했습니다". Randomize how this one should start so openings vary across posts.
OPENING_HINTS = [
    "해결한 문제나 상황 설명으로 글을 시작하세요.",
    "바뀐 결과(이제 무엇이 되는지)부터 이야기를 여세요.",
    "손본 화면·기능·모듈 이름을 먼저 꺼내며 시작하세요.",
    "왜 이 작업이 필요했는지 배경에서 출발하세요.",
    "인사말이나 서론 없이 곧장 작업의 핵심 내용으로 들어가세요.",
    "가장 눈에 띄는 변화 한 가지를 먼저 던지며 시작하세요.",
]


def opening_directive():
    return (
        "도입부 지침(이 글에만 적용): "
        + random.choice(OPENING_HINTS)
        + " '방금 ~했습니다' 같은 정형 도입은 쓰지 마세요."
    )


def plain_post(repo, details):
    lines = [f"*{repo}* 에 커밋 {len(details)}개 푸시했습니다."]
    for d in details[:5]:
        subject = clean_message(d["message"]).split("\n", 1)[0]
        lines.append(f"• `{d['sha'][:7]}` {subject}")
    if len(details) > 5:
        lines.append(f"…외 {len(details) - 5}개")
    return "\n".join(lines)


def compose_post(repo, details, cfg):
    """The achievement message body for one member's commits in one repo."""
    if not cfg["use_llm"]:
        return plain_post(repo, details)
    user_text = "\n\n".join([
        prompt_from_commits(repo, details, cfg["send_diff"]),
        opening_directive(),
        emoji_directive(),
    ])
    try:
        body = anthropic_message(
            cfg["system_prompt"],
            user_text,
            cfg["anthropic_key"],
            cfg["model"],
        )
    except RuntimeError as err:
        print(f"  ! LLM failed for {repo}, falling back to plain text: {err}", file=sys.stderr)
        return plain_post(repo, details)
    return body


def poll_once(cfg, state, dry_run):
    poll_start = now_iso()
    seen_order = list(state["seen"])
    seen = set(seen_order)
    watermark = state.get("watermark")
    members = cfg["members"]

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

    # per_member[i] = list of (repo, details) owned by members[i]
    per_member = [[] for _ in members]
    for repo in repos:
        try:
            raw = gather_commits(repo, cfg["github_token"], since, cfg["scan_branches"])
        except RuntimeError as err:
            print(f"  ! {repo}: {err}", file=sys.stderr)
            continue
        fresh = [c for c in raw if c["sha"] not in seen]
        if not cfg["include_bots"]:
            fresh = [c for c in fresh if not is_bot(c)]

        owned = [[] for _ in members]
        unowned = 0
        for c in fresh:
            idx = owner_index(c, members)
            if idx is None:
                unowned += 1
            else:
                owned[idx].append(c)
        if unowned:
            print(f"  {repo}: {unowned} new commit(s) matched no member")
        for i, commits in enumerate(owned):
            if not commits:
                continue
            try:
                details = [commit_detail(repo, c["sha"], cfg["github_token"]) for c in commits]
            except RuntimeError as err:
                print(f"  ! {repo}: {err}", file=sys.stderr)
                continue
            per_member[i].append((repo, details))

    total = sum(len(d) for pm in per_member for _, d in pm)
    if total == 0:
        state["watermark"] = poll_start
        if not dry_run:
            save_state(state)
        print("  no new commits by any member")
        return 0

    announced = []
    sent = 0
    for i, batches in enumerate(per_member):
        if not batches:
            continue
        member = members[i]
        member_total = sum(len(d) for _, d in batches)
        if len(batches) > cfg["max_messages"]:
            print(f"  {member['name']}: {len(batches)} repos (> {cfg['max_messages']}); plain summary")
            summary = f"*{cfg['org']}* 에 새 커밋 {member_total}개 ({len(batches)}개 저장소)\n" + "\n".join(
                f"• {repo}: {len(d)}개" for repo, d in batches[:20]
            )
            messages = [summary]
        else:
            messages = [compose_post(repo, d, cfg) for repo, d in batches]

        for text in messages:
            if dry_run:
                print(f"\n  ┌─ would post as @{member['name']} → {cfg['channel']}")
                print("  │ " + text.replace("\n", "\n  │ "))
                print("  └─")
            else:
                slack_call(
                    member["slack_token"],
                    "chat.postMessage",
                    {"channel": cfg["channel"], "text": text, "unfurl_links": False},
                )
            sent += 1
        for _, d in batches:
            announced.extend(x["sha"] for x in d)

    verb = "would post" if dry_run else "→ posted"
    print(f"\n  {verb} {sent} message(s): {total} commit(s)")

    if dry_run:
        return total

    # Trim oldest-first, so a SHA can never fall out of the dedupe window early.
    seen_order.extend(announced)
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


def load_members(github_token):
    """Roster from members.json (multi-member) or a single member from .env.
    Returns (members, channel_override_or_None)."""
    if MEMBERS_PATH.exists():
        data = json.loads(MEMBERS_PATH.read_text(encoding="utf-8"))
        members = []
        for m in data.get("members", []):
            if not m.get("slack_token"):
                sys.exit(f"members.json: member {m.get('name', '?')} has no slack_token")
            logins = {l.lower() for l in m.get("github_logins", [])}
            emails = {e.lower() for e in m.get("emails", [])}
            if not logins and not emails:
                sys.exit(f"members.json: member {m.get('name', '?')} needs github_logins or emails")
            members.append({
                "name": m.get("name") or next(iter(logins | emails)),
                "slack_token": m["slack_token"],
                "logins": logins,
                "emails": emails,
                "match_all": False,
            })
        if not members:
            sys.exit("members.json has no members")
        return members, data.get("channel")

    # Single-member fallback from .env
    slack_token = os.environ.get("SLACK_USER_TOKEN", "")
    if not slack_token:
        sys.exit("missing config: SLACK_USER_TOKEN (or create members.json for a team)")
    match_all = os.environ.get("ANNOUNCE_ALL", "").lower() in ("1", "true", "yes")
    login = os.environ.get("GITHUB_AUTHOR", "")
    if not login and not match_all:
        login = github_get("/user", github_token)["login"]
    return [{
        "name": login or "me",
        "slack_token": slack_token,
        "logins": {login.lower()} if login else set(),
        "emails": {e.strip().lower() for e in os.environ.get("GITHUB_AUTHOR_EMAILS", "").split(",") if e.strip()},
        "match_all": match_all,
    }], None


def build_config(args):
    load_env()
    github_token = os.environ.get("GITHUB_TOKEN", "")
    org = os.environ.get("GITHUB_ORG", "")
    use_llm = os.environ.get("USE_LLM", "true").lower() not in ("0", "false", "no") and not args.no_llm
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    required = [("GITHUB_TOKEN", github_token), ("GITHUB_ORG", org)]
    if use_llm:
        required.append(("ANTHROPIC_API_KEY", anthropic_key))
    missing = [name for name, value in required if not value]
    if missing:
        sys.exit(f"missing config: {', '.join(missing)} (copy .env.example to .env)")

    members, channel_override = load_members(github_token)
    channel = channel_override or os.environ.get("SLACK_CHANNEL", "")
    if not channel:
        sys.exit("missing config: SLACK_CHANNEL (or a `channel` in members.json)")

    # Each member's token must be a user token (xoxp), not a bot token.
    for m in members:
        identity = slack_call(m["slack_token"], "auth.test", {})
        if identity.get("bot_id"):
            sys.exit(
                f"member {m['name']}: token is a bot token (auth.test returned bot_id).\n"
                "Each member needs a user token (xoxp-) with the chat:write USER scope."
            )
        m["slack_user"] = identity.get("user", m["name"])

    emphasize_ai = os.environ.get("EMPHASIZE_AI", "true").lower() not in ("0", "false", "no")
    work_context = os.environ.get("WORK_CONTEXT", "").strip()

    return {
        "github_token": github_token,
        "anthropic_key": anthropic_key,
        "channel": channel,
        "org": org,
        "members": members,
        "scan_branches": os.environ.get("SCAN_BRANCHES", "true").lower() not in ("0", "false", "no"),
        "use_llm": use_llm,
        "model": os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        "send_diff": os.environ.get("SEND_DIFF", "").lower() in ("1", "true", "yes"),
        "system_prompt": build_system_prompt(emphasize_ai, work_context),
        "emphasize_ai": emphasize_ai,
        "exclude": {r.strip() for r in os.environ.get("GITHUB_EXCLUDE_REPOS", "").split(",") if r.strip()},
        "include_bots": os.environ.get("INCLUDE_BOTS", "").lower() in ("1", "true", "yes"),
        "max_messages": args.max_messages or int(os.environ.get("MAX_MESSAGES_PER_POLL", "10")),
        "lookback": 0 if args.since else LOOKBACK_SECONDS,
        "interval": args.interval,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=60, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="poll a single time and exit")
    parser.add_argument("--dry-run", action="store_true", help="print instead of posting")
    parser.add_argument("--no-llm", action="store_true", help="skip Claude, post a plain list")
    parser.add_argument(
        "--since",
        metavar="WHEN",
        help="look back from this point instead of the saved baseline: "
        "`today`, `24h`, `7d`, or 2026-07-10T00:00:00Z",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="override MAX_MESSAGES_PER_POLL (above this, one plain summary per member)",
    )
    args = parser.parse_args()

    if args.since and not args.dry_run and not args.once:
        sys.exit("--since is for inspection; pair it with --dry-run (or at least --once)")

    cfg = build_config(args)
    state = load_state()

    roster = ", ".join(f"@{m['slack_user']}" for m in cfg["members"])
    engine = f"Claude ({cfg['model']})" if cfg["use_llm"] else "plain text"
    scope = "all branches" if cfg["scan_branches"] else "default branch only"
    print(f"posting to {cfg['channel']} as: {roster}")
    print(f"watching {cfg['org']} ({scope}); each member posts their own commits; writing with {engine}")
    if cfg["use_llm"]:
        bits = "commit messages + file names + line counts" + (" + diffs" if cfg["send_diff"] else " (no diffs)")
        print(f"sending to Claude: {bits}" + (", emphasizing AI-assisted work" if cfg["emphasize_ai"] else ""))
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
