# github-slack-selfbot
셀프봇중독상담 1342
## 준비
**1. Slack 앱** — [api.slack.com/apps](https://api.slack.com/apps) → **OAuth &
Permissions** → **User Token Scopes**에 `chat:write` 추가 → 설치.

> Bot Token Scopes가 아니라 **User** Token Scopes입니다. 헷갈리면 봇으로 올라갑니다
> (실행 시 `auth.test`로 잡아냅니다). `xoxp-`로 시작하는 토큰이 나옵니다.

**2. GitHub 토큰** — [Classic PAT](https://github.com/settings/tokens),
`repo` + `read:org`. 조직이 SAML SSO를 강제하면 **Configure SSO**로 인가하세요.

**3. Anthropic 키** — [console.anthropic.com](https://console.anthropic.com)에서
API 키 발급

**4. 설정**

```sh
cp .env.example .env            # GITHUB_TOKEN, GITHUB_ORG, ANTHROPIC_API_KEY, SLACK_CHANNEL
$EDITOR .env
cp members.example.json members.json  
$EDITOR members.json
```

`members.json` 예시:

```json
{
  "channel": "C01234ABCDE",
  "members": [
    {"name": "은진",  "slack_token": "xoxp-...", "github_logins": ["eunjinkosilver"], "emails": ["eunjin@dd.com"]},
  ]
}
```

`members.json`이 없으면 `.env`의 `SLACK_USER_TOKEN` 한 명 모드로 돕니다.
`members.json`은 토큰이 들어 있어 `.gitignore`에 있습니다.

## 실행

의존성 없습니다. Python 3.9+면 바로 돌아갑니다 (Claude·Slack·GitHub 모두 표준
라이브러리로 호출).

```sh
python3 selfbot.py --dry-run --once --since today   # 오늘 커밋으로 미리보기
python3 selfbot.py --once                            # 한 번 폴링하고 종료
python3 selfbot.py                                   # 60초마다 폴링 (Ctrl+C)
python3 selfbot.py --no-llm                          # Claude 없이 커밋 목록만
```

**첫 실행은 아무것도 보내지 않습니다.** 현재 시각을 기준선으로 `.state.json`에
기록만 하고, 그 이후 푸시된 커밋부터 알립니다.

`--dry-run`은 Slack에 보내지도, `.state.json`을 건드리지도 않습니다. 몇 번이든
반복해서 돌려도 됩니다. `--since`(`today` / `24h` / `7d` / 절대 시각)로 과거 구간을
지정해 뭐가 나갈지 미리 볼 수 있습니다.

## 동작

`GET /orgs/{org}/repos?sort=pushed`로 저장소를 마지막 푸시 순으로 받아, 기준선보다
오래된 저장소가 나오면 스캔을 멈춥니다. 새 커밋이 있는 저장소는 **모든 브랜치**를 돌며
커밋을 모으고(SHA로 중복 제거), 각 커밋을 **주인(멤버)에게 배정**한 뒤, 멤버별로
상세(변경 파일·줄 수)를 조회해 Claude에 넘기고, **그 멤버의 Slack 토큰으로** 올립니다.
커밋 링크는 코드에서 직접 붙입니다.

- **모든 브랜치를 봅니다**(`SCAN_BRANCHES=true`, 기본). 피처 브랜치 작업이 대부분이라
  이게 켜져 있어야 실제 커밋이 잡힙니다. 끄면 기본 브랜치만 봅니다.
- **커밋 주인 판별**은 각 멤버의 GitHub 로그인(`github_logins`)과 이메일(`emails`)로
  합니다. 커밋을 회사 이메일로 했는데 그게 GitHub 계정에 연결돼 있지 않으면 로그인
  매칭이 놓치므로, 그 이메일을 `emails`에 넣어주세요.
- 어느 멤버에도 안 맞는 커밋이 있는 저장소는 `"N new commit(s) matched no member"`로
  로그만 남깁니다. 뭔가 빠진 것 같으면 이 줄을 보고 이메일을 추가하면 됩니다.
- 봇 커밋 제외(기본), 보관된 저장소·`GITHUB_EXCLUDE_REPOS` 제외.
- 알린 커밋 SHA는 `.state.json`에 남아 재시작해도 중복 발송되지 않습니다.
- 한 멤버가 한 번에 저장소 여러 개(`MAX_MESSAGES_PER_POLL` 초과)에 커밋하면, 그 멤버
  것은 요약 한 줄로 묶어 채널 도배와 API 비용을 막습니다.
- Claude가 거절(refusal)하거나 실패하면 그 커밋은 평문 목록으로라도 발송됩니다.

### 글 스타일

기본 글은 **길고 자세하게, 항목별로 나눠서** 씁니다: 요약 → 주요 변경 불릿 →
개선 효과 + 정량 수치(바꾼 파일 수, +/- 줄 수, 커밋 수). 수치는 diff에 실제로 있는
숫자만 인용하게 해서, 지어낸 성과가 아니라 진짜 데이터로 어필합니다.

- `EMPHASIZE_AI=true`(기본): AI 도구로 작업했다는 점을 자연스럽게 드러냅니다.
- `SEND_DIFF=false`: diff까지 Claude에 보내 더 구체적으로 씁니다(비공개 코드가 나갑니다). 기본적으로 설정은 OFF되어 있지만, 킬 수도 있습니다. 
- 모델은 `ANTHROPIC_MODEL`(기본 `claude-opus-4-8`), 말투·형식은 `selfbot.py`의
  `SYSTEM_BASE`를 고치면 됩니다.

### 한계

**리베이스·체리픽된 커밋**은 `LOOKBACK_SECONDS`(1시간)만큼 되감아 조회하고 SHA로
중복을 거릅니다. 브랜치가 아주 많은 저장소는 처음 `MAX_BRANCHES`(40)개만 스캔하고
경고를 찍습니다 — 그런 저장소는 `GITHUB_EXCLUDE_REPOS`에 넣거나 조정하세요.

**이벤트 API는 쓰지 않습니다.** `/orgs/{org}/events`는 공개 이벤트만 반환해 비공개
조직에선 빈 배열이고, GitHub이 2025년 10월 7일부터 `PushEvent`에서 커밋 정보를
제거해 이벤트 피드로는 커밋 메시지를 가져올 수 없습니다.

## 주의

`.env`와 `.state.json`은 `.gitignore`에 있습니다. 토큰을 커밋하지 마세요.
유출됐다면 Slack·GitHub·Anthropic 각각에서 즉시 revoke하고 재발급하세요.
