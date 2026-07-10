# github-slack-selfbot

셀프봇중독상담 1342

GitHub 조직(`fnf-es`)의 모든 저장소를 폴링해서, 새 커밋이 올라오면 Slack 채널에
**내 계정으로** 알립니다. 봇 이름이 아니라 내 이름과 프로필 사진으로 올라갑니다.

Discord와 달리 Slack은 유저 토큰(`xoxp-`)을 공식 지원합니다. 브라우저 세션 토큰을
훔쳐 쓸 필요 없이 OAuth로 정식 발급받으면 됩니다.

## 알아둘 것

**조직원 전원의 커밋이 내 계정으로 나갑니다.** 채널 사람들 눈에는 내가 남의 커밋을
하나하나 본인 이름으로 중계하는 것처럼 보입니다. 의도한 게 맞다면 그대로 쓰시고,
"누가 커밋했는지"만 알리는 게 목적이라면 봇 토큰이나 GitHub 공식 Slack 앱
(`/github subscribe fnf-es/repo commits`)이 더 맞는 도구입니다.

**비공개 저장소 정보가 Slack으로 나갑니다.** 저장소명, 커밋 메시지, 작성자가
전송됩니다. Slack 워크스페이스가 회사 것인지 확인하세요.

## 준비

**1. Slack 앱**

[api.slack.com/apps](https://api.slack.com/apps)에서 앱 생성 →
**OAuth & Permissions** → **User Token Scopes**에 `chat:write` 추가 → 설치.

> Bot Token Scopes가 아니라 **User** Token Scopes입니다. 헷갈리면 메시지가 봇으로
> 올라갑니다. (`selfbot.py`가 실행 시 `auth.test`로 잡아냅니다)

`xoxp-`로 시작하는 **User OAuth Token**이 나옵니다. 유료 플랜은 관리자 승인이
필요할 수 있습니다.

**2. GitHub 토큰**

[Classic PAT](https://github.com/settings/tokens) 생성, `repo` + `read:org` 스코프.
조직이 SAML SSO를 강제하면 토큰 발급 후 **Configure SSO**로 `fnf-es`에 인가해야
합니다. 안 하면 401이 뜹니다.

**3. 설정**

```sh
cp .env.example .env
$EDITOR .env
```

`.env`에만 진짜 토큰을 넣으세요. `.env.example`은 커밋됩니다.

## 실행

의존성 없습니다. Python 3.9+면 바로 돌아갑니다.

```sh
python3 selfbot.py --once             # 한 번 폴링하고 종료
python3 selfbot.py                    # 60초마다 폴링 (Ctrl+C로 중단)
python3 selfbot.py --interval 300     # 5분마다
```

**첫 실행은 아무것도 보내지 않습니다.** 현재 시각을 기준선(`watermark`)으로
`.state.json`에 기록만 하고, 그 이후에 푸시된 커밋부터 알립니다. 처음 켤 때 조직의
과거 커밋이 전부 쏟아지는 걸 막기 위한 것이고, 계속 떠 있는 프로세스를 전제합니다.

### 미리 보기

기준선을 기다리지 않고 **과거 구간을 지정해서** 뭐가 나갈지 볼 수 있습니다.

```sh
python3 selfbot.py --dry-run --once --since today      # 오늘 자정(로컬)부터 지금까지
python3 selfbot.py --dry-run --once --since 24h        # 최근 24시간
python3 selfbot.py --dry-run --once --since 7d --max-messages 100
python3 selfbot.py --dry-run --once --since 2026-07-01T00:00:00Z
```

`--dry-run`은 **Slack에 아무것도 보내지 않고 `.state.json`도 건드리지 않습니다.**
몇 번이든 반복해서 돌려도 됩니다.

`--max-messages`를 올리면 저장소별 메시지를 전부 펼쳐서 보여줍니다. 안 그러면
저장소가 10개 넘게 걸릴 때 요약 한 줄로 접힙니다.

> `--since`는 지정한 구간을 **정확히** 조회합니다. 평상시 폴링에 들어가는 1시간
> 되감기(리베이스 대비)가 적용되지 않아서, `--since today`가 어제 커밋을 끌어오지
> 않습니다.

## 동작

`GET /orgs/{org}/repos?sort=pushed`로 저장소를 **마지막 푸시 순으로** 받습니다.
정렬돼 있으니 기준선보다 오래된 저장소가 처음 나오는 순간 스캔을 멈춥니다. 그래서
아무도 푸시 안 한 동안은 폴링 1회에 **API 요청 1개**만 나갑니다. 푸시가 있었던
저장소에 대해서만 `GET /repos/{owner}/{repo}/commits?since=`를 부릅니다.

- 인증 시 시간당 5000회 한도, 분당 1회 폴링이면 여유롭습니다.
- 알린 커밋 SHA는 `.state.json`에 남아 재시작해도 중복 발송되지 않습니다.
- 한 번에 여러 저장소가 터지면 (`MAX_MESSAGES_PER_POLL` 초과) 저장소별 메시지 대신
  요약 한 줄로 묶어서 채널이 도배되는 걸 막습니다.
- 봇 커밋(dependabot 등)은 기본으로 무시합니다. `INCLUDE_BOTS=true`로 켜세요.
- 보관(archived)된 저장소와 `GITHUB_EXCLUDE_REPOS`는 건너뜁니다.

### 한계

**기본 브랜치만 봅니다.** `/commits`는 기본 브랜치를 반환합니다. 모든 피처 브랜치의
커밋까지 알리면 채널이 감당 안 되기도 하고, 브랜치별 조회는 저장소마다 추가 요청이
필요합니다.

**리베이스·체리픽된 커밋은 놓칠 수 있습니다.** `since`는 커밋 날짜 기준이라, 날짜가
과거인 커밋이 지금 푸시되면 창 밖으로 벗어납니다. `LOOKBACK_SECONDS`(기본 1시간)만큼
되감아 조회해서 완화하고, 중복은 SHA로 거릅니다.

**이벤트 API는 쓰지 않습니다.** `/orgs/{org}/events`는 공개 이벤트만 반환해서
비공개 조직에서는 항상 빈 배열입니다. 그리고 GitHub이 2025년 10월 7일부터
`PushEvent` 페이로드에서 커밋 정보를 제거해서, 이벤트 피드로는 커밋 메시지를 아예
못 가져옵니다.

메시지 문구는 `selfbot.py`의 `format_message()`를 고치면 됩니다.

## 주의

`.env`와 `.state.json`은 `.gitignore`에 있습니다. 토큰을 커밋하지 마세요.
유출됐다면 Slack 앱 설정과 GitHub 토큰 페이지에서 즉시 revoke하고 재발급하세요.
