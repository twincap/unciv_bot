# Unciv Discord Bot

Unciv 멀티플레이 상태를 디스코드에서 조회하고, 턴 변경을 자동 알림으로 받아보는 봇입니다.

이 프로젝트는 가상환경(venv) 없이 시스템 Python으로 운영하는 기준으로 정리되어 있습니다.

## 1. 핵심 기능

- 게임 상태 조회: 현재 턴, 현재 플레이어, 리더보드
- 서버 점검: APIv1/APIv2 감지 및 헬스 체크
- 게임 추적: 채널별 등록, 자동 폴링, 턴 변경 알림
- 명령 지원: 슬래시 명령 중심, 텍스트 명령은 선택

## 2. 프로젝트 구조

```text
.
├─ bot.py
├─ requirements.txt
├─ .env.example
├─ deploy/
│  ├─ setup_debian_no_venv.sh
│  └─ systemd/
│     └─ unciv-bot.service
└─ README.md
```

## 3. 처음부터 설치 (Debian VM, venv 없음)

### 3-1. 시스템 패키지 설치

```bash
sudo apt update
sudo apt install -y git python3 python3-pip
```

### 3-2. 저장소 가져오기

```bash
git clone https://github.com/twincap/unciv_bot.git ~/unciv_bot
cd ~/unciv_bot
```

### 3-3. Python 라이브러리 전역 설치

Debian Bookworm 계열은 Python 패키지 보호 정책이 있어서 아래 옵션이 필요합니다.

```bash
sudo python3 -m pip install --break-system-packages -r requirements.txt
```

### 3-4. 환경변수 파일 생성

```bash
cp .env.example .env
nano .env
```

최소 설정 항목:

```env
DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
UNCIV_SERVER_BASE_URL=https://uncivserver.xyz
UNCIV_GAME_URL_TEMPLATE=
UNCIV_REQUEST_TIMEOUT=15
UNCIV_TRACK_POLL_INTERVAL_SEC=90
UNCIV_TRACK_FILE=tracked_games.json
ENABLE_MESSAGE_CONTENT_INTENT=false
```

### 3-5. 수동 실행 테스트

```bash
python3 bot.py
```

정상 로그 예시:

- logging in using static token
- Shard connected
- Synced 2 slash command(s)

## 4. 24시간 상시 실행 (systemd)

### 4-1. 서비스 파일 생성

템플릿 파일을 현재 사용자/경로로 치환해 등록합니다.

```bash
BOT_USER="$USER"
PROJECT_DIR="/home/$USER/unciv_bot"

sudo sed \
  -e "s|__BOT_USER__|$BOT_USER|g" \
  -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
  deploy/systemd/unciv-bot.service | sudo tee /etc/systemd/system/unciv-bot.service > /dev/null
```

### 4-2. 활성화 및 시작

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now unciv-bot
```

### 4-3. 상태 확인

```bash
sudo systemctl is-enabled unciv-bot
sudo systemctl is-active unciv-bot
sudo systemctl status unciv-bot --no-pager -l
```

### 4-4. 로그 확인

```bash
journalctl -u unciv-bot -f
```

enabled + active면 SSH를 끊어도 계속 실행됩니다.

## 5. 빠른 자동 설정 스크립트

아래 스크립트로 의존성 설치 + 서비스 등록을 한 번에 처리할 수 있습니다.

```bash
bash deploy/setup_debian_no_venv.sh
```

기본값:

- 사용자: 현재 로그인 사용자
- 경로: /home/<사용자>/unciv_bot

사용자/경로 직접 지정:

```bash
bash deploy/setup_debian_no_venv.sh ysy20081115 /home/ysy20081115/unciv_bot
```

## 6. 사용 가능한 명령

### 슬래시 명령 (권장)

- /ping
- /unciv game <game_id>
- /unciv status <game_id>
- /unciv health
- /unciv track add <game_id> [alias]
- /unciv track remove <game_id>
- /unciv track list

### 텍스트 명령

- !ping
- !unciv game <game_id>
- !unciv status <game_id>
- !unciv health
- !unciv track add <game_id> [alias]
- !unciv track remove <game_id>
- !unciv track list

텍스트 명령을 쓰려면 Discord Developer Portal에서 Message Content Intent를 켜고,
.env에서 ENABLE_MESSAGE_CONTENT_INTENT=true로 설정해야 합니다.

## 7. 운영 중 업데이트

```bash
cd ~/unciv_bot
git pull
sudo python3 -m pip install --break-system-packages -r requirements.txt
sudo systemctl restart unciv-bot
sudo systemctl status unciv-bot --no-pager -l
```

## 8. 문제 해결

### ModuleNotFoundError가 발생함

```bash
sudo python3 -m pip install --break-system-packages -r requirements.txt
```

### externally-managed-environment 오류

Debian 정책으로 생기는 정상 동작입니다. 아래처럼 설치해야 합니다.

```bash
sudo python3 -m pip install --break-system-packages -r requirements.txt
```

### 슬래시 명령이 안 보임

- 봇 초대 시 applications.commands 스코프 포함 확인
- 봇 실행 로그에서 Synced ... slash command 확인
- 반영까지 수 분 지연될 수 있음

## 9. 보안 주의

- .env는 절대 커밋하지 마세요.
- 토큰이 외부에 노출되었으면 Discord Developer Portal에서 즉시 재발급하세요.
- 재발급 후 .env의 DISCORD_BOT_TOKEN 값을 새 토큰으로 바꿔야 합니다.
