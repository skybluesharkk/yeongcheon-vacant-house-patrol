# 영천 빈집 순찰 통합 시스템 — 로컬 실행 가이드

영천시 빈집을 자율 순찰 차량(Unity + ROS2)이 돌면서 사진을 찍고, 백엔드에 업로드하면
AI 에이전트가 이상 징후/재활용 용도를 분석하고, 대시보드에서 한 번에 보는 시스템.

## 구성 요소

| 컴포넌트 | 폴더 | 기술 | 기본 포트 |
|---------|------|------|-----------|
| **Patrol** (자율 순찰 로봇) | [ros2_docker/](ros2_docker/), [Nav2SLAMExampleProject/](Nav2SLAMExampleProject/) | Unity 2022 + ROS2 Galactic (도커) | — |
| **Backend** (수집/허브) | [backend/](backend/) | Python `http.server` | **12303** |
| **Agent** (AI 분석) | [yeongcheon-vacant-house-agent/](yeongcheon-vacant-house-agent/) | FastAPI + LangGraph + Gemini | **8001** |
| **Frontend** (대시보드) | [yeongcheon-frontend-main/](yeongcheon-frontend-main/) | React + Vite + Tailwind | **5173** |

> Agent 의 README 는 기본 8000 이라 적혀있지만, 8000 은 더미 서버가 점유 중이라
> **Agent 는 8001 로** 실행하는 걸 표준으로 잡았다. 충돌 회피 + 명세 분리 모두 깔끔.

## 데이터 흐름

```
┌─────────────┐  POST /api/robots/{id}/image    ┌──────────────┐
│   Patrol    │ ────────────────────────────►   │  Backend     │  (12303)
│ (Unity+ROS) │  WS /ws/robot (위치, 계획, 상태)│              │
└─────────────┘                                 │ - 사진/이벤트  │
                                                │ - DB(json)   │
                              GET /api/...      │ - WS 허브     │
┌─────────────┐ ◄─────────────────────────────  │              │
│  Frontend   │  WS /ws/dashboard               └──────┬───────┘
│ (대시보드)  │                                        │  POST
└─────────────┘                                 (아직 mock — 통합 예정)
                                                       ▼
                                                ┌──────────────┐
                                                │   Agent      │  (8001)
                                                │ Gemini/공공  │
                                                │ 데이터 분석   │
                                                └──────────────┘
```

---

## 사전 준비 (1회)

### 1) 시스템 도구
- Python `3.10+` (백엔드)
- Python `3.12+` (agent — `pyproject.toml` 요구)
- `uv` (agent 빌드/실행)
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- Node.js `20+` + npm (프론트)
- Docker (ROS2 Galactic 컨테이너용, 패트롤 돌릴 때만)

### 2) Python 의존성 (백엔드) - 별개의 가상환경 사용하기 (yc-backend)
```bash
cd backend
pip3 install -r requirements.txt   # numpy, Pillow, requests
```

### 3) 환경 변수 (agent 만 필요)

[env_updated.txt](env_updated.txt) 의 키를 [yeongcheon-vacant-house-agent/.env](yeongcheon-vacant-house-agent/.env) 에 채워 넣는다.

```bash
cp env_updated.txt yeongcheon-vacant-house-agent/.env
```

필요한 키:

| 변수 | 용도 |
|------|------|
| `GEMINI_API_KEY` (또는 `GOOGLE_API_KEY`) | Gemini Vision 호출 (이미지 이상 판정/사진 해석) |
| `GEO_CODING_API_KEY` | VWorld 주소 → 좌표 변환 |
| `BUILDING_OPEN_API_KEY_DECODING` | 건축물대장 API (Decoding key 우선) |
| `BUILDING_OPEN_API_KEY_ENCODING` | 건축물대장 API (대체) |

> Backend/Frontend 는 별도 키 불필요. Patrol(차량) 도 환경변수 없음.

### 4) 프론트 .env

[yeongcheon-frontend-main/.env](yeongcheon-frontend-main/.env):
```env
VITE_API_BASE_URL=http://localhost:12303
VITE_NAVER_MAP_CLIENT_ID=<네이버 지도 키>
```

기본값이 `http://localhost:8000` 으로 들어있으므로 **12303 으로 수정 필요**:
```bash
sed -i 's|VITE_API_BASE_URL=.*|VITE_API_BASE_URL=http://localhost:12303|' yeongcheon-frontend-main/.env
```

---

## 실행 (터미널 4개)

### 터미널 1 — Backend (포트 12303)

```bash
cd /home/shim/yeongcheon-vacant-house-patrol/backend
python3 backend/server.py --host 127.0.0.1 --port 12303
```

확인:
```bash
curl http://127.0.0.1:12303/health
# → {"ok": true, "service": "robot-backend"}
```

브라우저:
- `http://localhost:12303/` — 백엔드 자체 정적 대시보드 (디버깅용)
- `http://localhost:12303/api/dashboard` — JSON

### 터미널 2 — Agent (포트 8001)

```bash
cd /home/shim/yeongcheon-vacant-house-patrol/yeongcheon-vacant-house-agent
uv sync                                    # 처음 1회
uv run yeongcheon-agent serve --host 127.0.0.1 --port 8001
```

확인:
```bash
curl http://127.0.0.1:8001/health
# → {"status": "ok"}
```

브라우저:
- `http://localhost:8001/docs` — Swagger UI (Gemini 호출 등 직접 테스트 가능)

> Agent README 가 기본 8000 으로 안내하지만, **이 통합 환경에서는 8001 로 띄움.**
> 더미 서버가 8000 을 쓰고 있어서 충돌 방지.

### 터미널 3 — Frontend (포트 5173)

```bash
cd /home/shim/yeongcheon-vacant-house-patrol/yeongcheon-frontend-main
npm install         # 처음 1회
npm run dev
```

브라우저: `http://localhost:5173`

### 터미널 4 — Patrol 로봇 (도커 컨테이너 안, 옵션)

Unity 시뮬레이션 + ROS-TCP-Endpoint 가 동작 중이라는 가정.

```bash
# 호스트 → 도커 컨테이너 접속 (root)
docker exec -u root -it <컨테이너명> bash

# 컨테이너 안에서
cd /root/colcon_ws
source /opt/ros/galactic/setup.bash
source install/setup.bash

ros2 run patrol_planner patrol_node --ros-args \
    -p backend_url:=http://172.17.0.1:12303 \
    -p robot_id:=robot-01 \
    -p linear_speed:=0.6 \
    -p angular_speed:=0.4
```

> 컨테이너 안에서 호스트 백엔드 접근: `127.0.0.1` 이 아니라 **호스트 IP**(보통 `172.17.0.1`) 또는
> `host.docker.internal` 을 써야 함. `docker run` 시 `--add-host=host.docker.internal:host-gateway`
> 가 걸려있으면 후자 사용 가능.

---

## 종단 검증

### 1) 백엔드 → 사진 업로드만 (Patrol 안 돌리고도 테스트)

```bash
# 작은 더미 JPEG 만들고
python3 -c "from PIL import Image; Image.new('RGB',(320,240),(80,120,180)).save('/tmp/t.jpg')"

# 로봇 형식 그대로 POST
curl -X POST http://127.0.0.1:12303/api/robots/robot-01/image \
  -F "image=@/tmp/t.jpg;type=image/jpeg" \
  -F "x=8.65" -F "y=-0.42" \
  -F "timestamp=$(date -u +%Y-%m-%dT%H:%M:%S)" \
  -F "houseId=H1"

# → {"success": true, "imageId": N, "imageUrl": "/uploads/...", "analysisJobId": "AN-JOB-..."}
```

업로드 후 [http://localhost:5173](http://localhost:5173) (프론트) 또는
[http://localhost:12303](http://localhost:12303) (백엔드 정적 페이지) 에서 사진이 보여야 정상.

### 2) Agent 단독 호출 (Gemini 동작 검증)

```bash
curl -X POST http://127.0.0.1:8001/agents/patrol-image \
  -H 'Content-Type: application/json' \
  -d '{"house_id":"YC-001","captured_image_base64":"<base64>"}'
```

`YC-001` 은 [agent 의 data/house/mapping.csv](yeongcheon-vacant-house-agent/data/) 에 정의된 fixture 키.

### 3) E2E 백엔드 자가 테스트

```bash
cd backend
python3 tests/e2e_robot_backend.py
```

명세상 모든 엔드포인트가 응답하는지 자동 검증.

---

## Backend ↔ Agent 통합 (완료)

이제 차량 → 백엔드 → **에이전트 자동 분석** → 대시보드 까지 한 번에 흐른다.

```
patrol_node POST /api/robots/{id}/image
            ↓
backend handle_image (DB 저장, photo_captured WS 발송)
            ↓ (백그라운드 스레드)
send_to_agent_async  →  POST AGENT_URL/agents/patrol-image
                        body: { house_id (YC-00x), captured_image_base64 }
            ↓ (5~30초 후)
agent 응답 { is_anomaly, risk_level, summary, evidence, recommended_actions }
            ↓
backend: images.analysisResult 갱신, 이상이면 events 추가
            ↓
broadcast_dashboard({ type: "anomaly_result", ... })
            ↓
frontend useRobotWebSocket → window 이벤트 'robot-anomaly-result' + 활동 로그
```

### 핵심 설정

- `AGENT_URL` 환경변수로 에이전트 위치 지정 (기본 `http://127.0.0.1:8001`)
  ```bash
  AGENT_URL=http://127.0.0.1:8001 python backend/server.py --host 0.0.0.0 --port 12303
  ```
- `HOUSE_ID_TO_AGENT_ID` 매핑이 [backend/server.py](backend/backend/server.py) 에 박혀있음
  (`H1 ↔ YC-001`, `H2 ↔ YC-002`, ...). mapping.csv 와 일치.
- 에이전트가 죽어있거나 매핑 없는 house_id 가 오면 → 로그만 남기고 사진 업로드는 정상 진행 (방어적).

### 동작 확인

```bash
# 차량 안 돌리고도 검증 — 한 빈집 사진 POST 후 백엔드 로그 확인
curl -X POST http://127.0.0.1:12303/api/robots/robot-01/image \
  -F "image=@/tmp/t.jpg;type=image/jpeg" \
  -F "x=8.65" -F "y=-0.42" \
  -F "timestamp=$(date -u +%Y-%m-%dT%H:%M:%S)" \
  -F "houseId=H1"

# 백엔드 로그에 다음 라인 보여야 함:
# [AGENT] 호출 시작: house_id=H1→YC-001, image_id=N, bytes=...
# [AGENT] 응답 수신: house_id=H1, is_anomaly=True, risk=high

# 결과 확인 (이상 판정이면 새 event 가 들어가 있어야 함)
curl -s http://127.0.0.1:12303/api/events | python3 -m json.tool | tail -30
```

---

## 포트 요약

```
5173   Frontend (Vite dev)
8000   더미 서버 (외부)
8001   Agent  (FastAPI + Gemini)
12303  Backend (사진 수집/허브)
```

---

## 폴더별 README

- 차량 (ROS2 + Unity): [readmes/](readmes/), [ros2_docker/colcon_ws/src/patrol_planner/README.md](ros2_docker/colcon_ws/src/patrol_planner/README.md)
- 백엔드 API 명세: [backend/API_SPEC.md](backend/API_SPEC.md)
- 백엔드 통합 스펙: [ros2_docker/colcon_ws/src/patrol_planner/BACKEND_INTEGRATION.md](ros2_docker/colcon_ws/src/patrol_planner/BACKEND_INTEGRATION.md)
- 프론트: [yeongcheon-frontend-main/README.md](yeongcheon-frontend-main/README.md)
- 에이전트: [yeongcheon-vacant-house-agent/README.md](yeongcheon-vacant-house-agent/README.md)
