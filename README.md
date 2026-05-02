## Redconnect Checker Telegram Bot

사용자가 원하는 날짜, 헌혈방식, 사이트를 등록하면 30분마다 조회해서 조건이 맞는 시간대가 생길 때 텔레그램으로 1회 알림을 보냅니다.

## Setup

1. 의존성 설치

```bash
uv sync
```

2. 환경변수 준비

```bash
cp .env.example .env
```

`.env`에 아래 값을 설정합니다.

- `TELEGRAM_BOT_TOKEN`: BotFather에서 발급받은 토큰
- `ENC_PASSPHRASE`: bloodinfo 계정 암호화용 패스프레이즈
- `DB_PATH`: sqlite 파일 경로 (기본 `telegram_bot.sqlite3`)
- `POLL_INTERVAL_MINUTES`: 주기 검사 간격 (기본 30)

## Run

```bash
uv run python bloodinfo_telegram_bot.py
```

## Telegram Commands

- `/start`: 사용법 안내
- `/register_account bloodinfo_id bloodinfo_password`: bloodinfo 계정 등록 (암호화 저장)
- `/add_subscription YYYY-MM-DD donation_types_csv site_codes_csv`: 알림 조건 등록
- `/list_subscriptions`: 내 구독 목록 조회
- `/cancel_subscription subscription_id`: 특정 구독 비활성화
- `/sites`: 사이트 코드 목록 조회
- `/check_now`: 즉시 1회 검사 실행

헌혈방식 `donation_types_csv`는 아래 중 복수 선택 가능합니다.

- `전혈` (`whole_blood`)
- `혈장` (`plasma`)
- `혈소판` (`platelet`)

알림은 같은 구독에 대해 1회만 발송되며, 발송 후 해당 구독은 자동 비활성화됩니다.
