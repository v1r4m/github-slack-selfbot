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