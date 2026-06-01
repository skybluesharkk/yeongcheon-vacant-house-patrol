# 백엔드 연동 사양서 (Backend Integration Spec)

영천 빈집 순찰 시스템(`patrol_planner` ROS2 노드) ↔ 백엔드 서버 ↔ 대시보드 사이의 통신 규격.

이 문서는 **백엔드 / 프론트엔드 작업자와 공유**하기 위한 것이며, 각 단계마다 어떤 메시지가 어떤 형식으로 오가는지 명시한다.

---

## 베이스 URL

```
https://yc.jun0.dev
```

- HTTPS / WSS 사용
- 모든 백엔드 엔드포인트는 이 베이스 URL 하위

| 종류 | 메서드 | 전체 URL                                                  | 용도                       | 출처            |
|------|--------|-----------------------------------------------------------|----------------------------|----------------|
| HTTP | POST   | `https://yc.jun0.dev/api/robots/{robotId}/image`          | 빈집 사진 업로드 + 이상 탐지 | **백엔드 확정** |
| HTTP | POST   | `https://yc.jun0.dev/api/graph` *(예정)*                  | 도로 그래프 등록 (1회)      | 로봇 제안       |
| HTTP | GET    | `https://yc.jun0.dev/api/graph` *(예정)*                  | 도로 그래프 조회            | 로봇 제안       |
| WS   | —      | `wss://yc.jun0.dev/ws/robot` *(예정)*                     | 로봇 → 백엔드 스트림        | 로봇 제안       |
| WS   | —      | `wss://yc.jun0.dev/ws/dashboard` *(예정)*                 | 백엔드 → 대시보드 재방송    | 로봇 제안       |

- *(예정)* 표시는 아직 백엔드와 합의 전. 로봇 쪽 제안이며 변경 가능.
- Phase 1 엔드포인트는 **백엔드 친구분이 정의한 스펙대로** 사용.

---

## 시스템 구성

```
┌────────────────────┐                            ┌──────────────────┐
│ patrol_node        │  ── POST /arrival ──────>  │  백엔드 서버     │
│ (ROS2, Unity sim)  │  ── WS /ws/robot ───────>  │                  │
│                    │  (위치/계획/상태)         │  - DB 저장       │
│ - TF 위치          │                            │  - WS 허브       │
│ - 카메라 캡처      │                            │  - 사진 저장소   │
│ - 미션 진행        │                            └────────┬─────────┘
└────────────────────┘                                     │
                                                       WS 재방송
                                                  + REST 이력 조회
                                                           ▼
                                                  ┌──────────────────┐
                                                  │ 대시보드 (Web)   │
                                                  │ - 맵 + 차량 마커 │
                                                  │ - 사진 갤러리    │
                                                  │ - 진행 상태      │
                                                  └──────────────────┘
```

| 컴포넌트       | 가동 시점                          | 비고                              |
|---------------|-----------------------------------|-----------------------------------|
| patrol_node   | 순찰 중에만                        | 영찬님 PC + Unity                 |
| 백엔드        | 항상 ON                            | 친구분 서버                       |
| 대시보드      | 보고 싶을 때만                     | 브라우저, 백엔드에 fetch/WS       |

---

## Phase 구분과 진행 순서

전체 기능을 **세 페이즈**로 나눠 진행한다. Phase 1 만으로도 데모는 가능. Phase 2 는 실시간 모니터링을 원할 때 추가. Phase 3 는 실종자 탐지 기능.

### Phase 1 — 빈집 사진 전송 (필수)

- 차량이 빈집 도착 → 사진 캡처 → HTTP POST 로 백엔드 업로드 - 빈집 위치와 경로등은 조금 더 수정해서 매끄럽게 해야할 여지가 있음.
- 백엔드는 사진 + 메타데이터를 DB 에 저장
- 대시보드는 (필요 시) REST 로 이력 조회

### Phase 2 — 실시간 모니터링 (선택)

- 차량의 위치/yaw 를 5Hz 로 WebSocket 송신
- 미션 시작 시 전체 계획 (waypoints, 방문 순서) 1회 송신
- 빈집 도착/회전/캡처 등 상태 변경 시마다 이벤트 송신
- 백엔드는 WS 허브 역할 (로봇 → 대시보드 재방송)
- 대시보드는 도로 그래프 위에 차량 마커 + 경로를 실시간 표시

### Phase 3 — 실종자 탐지 (선택)

- 차량이 이동 중 카메라 화면 내 지정된 실종자를 YOLO로 탐지
- 탐지 즉시 해당 실종자 ID(클래스명)와 현재 위치(x, y), 증거 사진을 백엔드로 POST 전송
- 중복 알림 방지를 위해 같은 대상은 1회만 발송

---

## 작업 분담 / 체크리스트

### Phase 1 작업

| | 담당 | 작업 | 상태 |
|--|------|------|------|
| 1.1 | 로봇 | POST 송신 코드 (멀티파트 + JPEG + house_id) | ✅ 완료 |
| 1.2 | 로봇 | 카메라 토픽 구독 + 최신 이미지 캐싱 | ✅ 완료 |
| 1.3 | 백엔드 | `POST /arrival` 엔드포인트 구현 | ⬜ |
| 1.4 | 백엔드 | 사진 저장 + 메타데이터 DB | ⬜ |
| 1.5 | 백엔드 | `GET /arrival` 이력 조회 API | ⬜ (대시보드 시점) |
| 1.6 | 프론트 | 단순 갤러리 페이지 (선택) | ⬜ |

### Phase 2 작업

| | 담당 | 작업 | 상태 |
|--|------|------|------|
| 2.1 | 로봇 | WebSocket 클라이언트 (`websocket-client`) | ⬜ |
| 2.2 | 로봇 | `graph_to_json()` export 함수 | ⬜ |
| 2.3 | 로봇 | 미션 시작 시 plan 메시지 송신 | ⬜ |
| 2.4 | 로봇 | 제어 루프에서 position 5Hz 송신 | ⬜ |
| 2.5 | 로봇 | 상태 전이 시 status 메시지 송신 | ⬜ |
| 2.6 | 백엔드 | WebSocket 허브 (`/ws/robot`, `/ws/dashboard`) | ⬜ |
| 2.7 | 백엔드 | `GET /graph` 도로 그래프 제공 | ⬜ |
| 2.8 | 프론트 | 맵 렌더링 (SVG/Canvas) | ⬜ |
| 2.9 | 프론트 | WS 구독 + 차량 마커 실시간 갱신 | ⬜ |
| 2.10 | 프론트 | 계획 경로 폴리라인 + 진행 상태 패널 | ⬜ |

### Phase 3 작업 (실종자 탐지)

| | 담당 | 작업 | 상태 |
|--|------|------|------|
| 3.1 | 백엔드 | `POST /missing-person` 엔드포인트 구현 | ⬜ |
| 3.2 | 로봇 | `yolo_detector_node` 구현 (YOLO 추론 + POST) | ✅ 완료 |
| 3.3 | 프론트 | 대시보드 내 실종자 발견 알림 표시 | ⬜ |

---

# Phase 1: 빈집 사진 전송 — 상세 스펙

## 1.A 엔드포인트

```
POST https://yc.jun0.dev/api/robots/{robotId}/image
```

`{robotId}` 는 로봇 식별자 (예: `robot-01`).

로봇 노드는 베이스 URL + robot_id 두 파라미터로 분리해서 주입:

```bash
ros2 run patrol_planner patrol_node --ros-args \
    -p backend_url:=https://yc.jun0.dev \
    -p robot_id:=robot-01
```

코드는 `{backend_url}/api/robots/{robot_id}/image` 로 엔드포인트를 조립한다.
`backend_url` 이 비어있으면 POST 단계만 스킵 (개발 편의용).

## 1.B 요청

- Method: `POST`
- Content-Type: `multipart/form-data`

| 필드명     | 타입       | 필수 | 설명                                                         | 예시                       |
|-----------|------------|------|--------------------------------------------------------------|---------------------------|
| `image`   | file       | ✓    | JPEG 바이너리. MIME: `image/jpeg`. 파일명: `<house_id>.jpg`    | `H1.jpg`                  |
| `x`       | form text  | ✓    | 사진 찍는 시점 차량의 odom x 좌표 (m, 소수 2자리)              | `"8.65"`                  |
| `y`       | form text  | ✓    | 사진 찍는 시점 차량의 odom y 좌표 (m, 소수 2자리)              | `"-0.42"`                 |
| `timestamp`| form text | ✓    | ISO 8601 (`YYYY-MM-DDTHH:MM:SS`, UTC)                         | `"2026-05-20T14:35:22"`   |

> **`address` 필드는 송신 안 함**: Unity 시뮬레이션이라 실제 한국어 주소가 없어서.
> 백엔드가 필수로 요구할 경우 `house_id` 또는 좌표 문자열을 채워 보내도록 변경 가능.

## 1.C 응답

백엔드는 JSON 으로 처리 결과를 돌려준다:

```json
{
  "success": true,
  "robotId": "robot-01",
  "imageId": 15,
  "imageUrl": "/uploads/robots/robot-01/images/frame_001.jpg",
  "analysisJobId": "AN-JOB-0001"
}
```

| 필드            | 의미                                          |
|----------------|----------------------------------------------|
| `success`      | 처리 성공 여부                                |
| `robotId`      | 요청한 로봇 ID 그대로 반환                    |
| `imageId`      | DB 에 저장된 이미지의 PK (정수)               |
| `imageUrl`     | 저장된 이미지 다운로드 URL (베이스 URL 기준)  |
| `analysisJobId`| 이상 탐지 비동기 작업 ID (`/agent/anomaly/analyze-image` 결과 콜백 추적용) |

로봇 측은 이 값을 로그로만 남기고 미션 계속 진행. 실패 응답은 그냥 로그.

## 1.D 호출 특성

- 빈도: **미션당 빈집 수만큼** (보통 1~10건)
- 동시성: 백그라운드 스레드로 발송. 드물게 동시 호출 가능 (이전 POST 응답이 늦어지면)
- Timeout: 5초 (`post_timeout` 파라미터)
- 재시도: **없음** (단순화). 실패해도 다음 빈집으로 진행

## 1.E 보안 / 인증

현재 미구현. 필요 시 헤더로 API 키 추가 예정:
```
X-API-Key: <token>
```

## 1.F 백엔드 구현 예시 (FastAPI + SQLite)

```python
from fastapi import FastAPI, UploadFile, Form
from pathlib import Path
import sqlite3, time

app = FastAPI()
PHOTOS_DIR = Path("./photos")
PHOTOS_DIR.mkdir(exist_ok=True)
db = sqlite3.connect("patrol.db", check_same_thread=False)
db.execute("""
    CREATE TABLE IF NOT EXISTS arrivals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id TEXT,
        timestamp REAL,
        photo_path TEXT,
        received_at REAL
    )""")
db.commit()

@app.post("/arrival")
async def arrival(image: UploadFile, house_id: str = Form(...),
                  timestamp: str = Form(...)):
    photo_bytes = await image.read()
    fname = f"{house_id}_{int(float(timestamp)*1000)}.jpg"
    (PHOTOS_DIR / fname).write_bytes(photo_bytes)
    db.execute(
        "INSERT INTO arrivals(house_id, timestamp, photo_path, received_at)"
        " VALUES (?, ?, ?, ?)",
        (house_id, float(timestamp), str(fname), time.time()),
    )
    db.commit()
    return {"ok": True, "saved": fname}

@app.get("/arrival/{house_id}")
def list_arrivals(house_id: str):
    rows = db.execute(
        "SELECT id, timestamp, photo_path, received_at FROM arrivals"
        " WHERE house_id=? ORDER BY timestamp DESC", (house_id,)
    ).fetchall()
    return [{"id": r[0], "timestamp": r[1], "photo": r[2], "received_at": r[3]} for r in rows]
```

## 1.G 테스트 명령

로봇 없이 curl 로 검증:

```bash
# 로컬 개발 (백엔드를 로컬에서 띄울 때)
curl -X POST http://localhost:8000/api/arrival \
  -F "image=@test.jpg;type=image/jpeg" \
  -F "house_id=H1" \
  -F "timestamp=1715485800.123"

# 실 서버
curl -X POST https://yc.jun0.dev/api/arrival \
  -F "image=@test.jpg;type=image/jpeg" \
  -F "house_id=H1" \
  -F "timestamp=1715485800.123"
```

---

# Phase 2: 실시간 모니터링 — 상세 스펙

## 2.A 엔드포인트 전체

| 종류 | 전체 URL                                  | 방향                | 용도                          |
|------|-------------------------------------------|---------------------|-------------------------------|
| WS   | `wss://yc.jun0.dev/ws/robot`              | 로봇 → 백엔드       | 위치 / 계획 / 상태 업로드     |
| WS   | `wss://yc.jun0.dev/ws/dashboard`          | 백엔드 → 대시보드   | 위 메시지 재방송              |
| GET  | `https://yc.jun0.dev/api/graph`           | 대시보드 ← 백엔드   | 도로 그래프 (최초 1회 fetch) |
| POST | `https://yc.jun0.dev/api/graph`           | 로봇 → 백엔드       | 도로 그래프 등록 (시작 시 1회) |

## 2.B 메시지 종류

모든 WS 메시지는 **JSON 텍스트 프레임**. 공통적으로 `type` 필드로 구분.

### B.1 `graph` — 도로 그래프 (정적)

로봇이 시작될 때 1회 POST. 백엔드는 저장 후 `GET /graph` 로 제공.

```json
{
  "type": "graph",
  "nodes": {
    "N0": [8.90, 8.50],
    "N1": [8.30, 8.20],
    "N2": [8.80, 2.40],
    "N3": [8.00, -0.90],
    "N4": [6.60, -7.20]
  },
  "edges": {
    "N0": ["N1"],
    "N1": ["N0", "N2"],
    "N2": ["N1", "N3"]
  },
  "houses": {
    "H1": {"pos": [8.65, -0.42], "yaw": -1.184},
    "H2": {"pos": [-3.73, -8.54], "yaw": -0.847}
  }
}
```

- 단위: 좌표 m (ROS odom 프레임), yaw rad
- 변경되지 않음. 그래프가 바뀌면 로봇 재시작 시 다시 POST

### B.2 `plan` — 미션 계획 (미션 시작 시 1회)

```json
{
  "type": "plan",
  "mission_id": "1715485800.123",
  "start": [9.07, 7.66],
  "house_order": ["H1", "H2", "H3", "H4", "H5", "H6"],
  "waypoints": [
    [9.07, 7.66],
    [8.90, 8.50],
    [8.65, -0.42]
  ],
  "arrival_indices": [3, 8, 15, 21, 25, 28],
  "arrival_yaws": [-1.184, -0.847, -2.494, 3.015, 0.173, 0.653]
}
```

| 필드 | 설명 |
|------|------|
| `mission_id` | 미션 식별자 (시작 시점 유닉스 타임) |
| `start` | 차량 출발 위치 (odom) |
| `house_order` | TSP 결과 방문 순서 |
| `waypoints` | 모든 웨이포인트 (시작점 포함) |
| `arrival_indices` | `waypoints[i]` 가 빈집 도착 지점인지 표시. `house_order[k]` 의 도착 인덱스는 `arrival_indices[k]` |
| `arrival_yaws` | 빈집별 카메라가 향할 yaw (rad). 인덱스는 `house_order` 와 동일 |

### B.3 `pos` — 차량 실시간 위치 (5Hz 스트리밍)

```json
{
  "type": "pos",
  "mission_id": "1715485800.123",
  "x": 8.92,
  "y": 7.10,
  "yaw": -1.823,
  "t": 1715485812.045
}
```

| 필드 | 단위 | 설명 |
|------|------|------|
| `x`, `y` | m | odom 프레임 좌표 |
| `yaw` | rad | -π ~ +π |
| `t` | s | 유닉스 시간 (소수 ms) |

- 빈도: 5Hz (200ms 간격). 더 빠르게는 안 보냄 (대시보드 부담 줄이려고)

### B.4 `status` — 상태 전이 이벤트

상태가 바뀔 때마다 1회 발송.

```json
{
  "type": "status",
  "mission_id": "1715485800.123",
  "phase": "rotating",
  "house_id": "H1",
  "house_index": 0,
  "t": 1715485815.211
}
```

| `phase` 값         | 의미 |
|--------------------|------|
| `mission_started`  | 미션 시작 (`plan` 메시지 직후) |
| `navigating`       | 일반 웨이포인트 주행 중 (선택, 너무 잦으면 생략) |
| `arrived`          | 빈집 위치 도착 (회전 직전) |
| `rotating`         | 빈집에서 목표 yaw 로 회전 중 |
| `capturing`        | yaw 정렬 완료, 사진 캡처 + POST + 정지 |
| `paused`           | pause_at_house 대기 중 |
| `mission_done`     | 모든 빈집 + 복귀 완료 |
| `mission_aborted`  | 에러로 중단 |

빈집 관련 phase 에서는 `house_id`, `house_index` 동봉.

### B.5 `video_frame` (선택, 실시간 영상)

카메라 프레임을 JPEG 압축 후 Base64로 인코딩하여 약 2Hz로 전송합니다. 대시보드의 `<img>` 태그 `src`에 바로 쓸 수 있습니다.

```json
{
  "type": "video_frame",
  "data": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAgAAZABkAAD..."
}
```

### B.6 `arrival_ack` (선택)

`POST /arrival` 결과를 WS 로도 알리고 싶을 때:

```json
{
  "type": "arrival_ack",
  "house_id": "H1",
  "ok": true,
  "photo_url": "/photos/H1_1715485800123.jpg",
  "t": 1715485800.234
}
```

## 2.C 좌표계 / 단위 약속

| 항목 | 값 |
|------|-----|
| 좌표 프레임 | ROS odom |
| 길이 단위 | m |
| 각도 단위 | rad (-π ~ +π) |
| 시간 | 유닉스 초 (소수점 ms) |
| Y 축 양의 방향 | 북쪽 (+X = 동쪽, +Z 는 사용 안 함, 평지 가정) |

## 2.D WebSocket 동작 규약

### 로봇 → 백엔드 (`/ws/robot`)

- 로봇 노드가 시작 시 연결
- 끊기면 자동 재연결 (3초 backoff)
- 메시지 누락 시 백엔드는 다음 메시지 기다림 (재요청 없음)

### 백엔드 → 대시보드 (`/ws/dashboard`)

- 대시보드 접속 직후 백엔드는 **현재 상태 스냅샷** 한 번 전송:
  - 최신 `plan` (있으면)
  - 최신 `pos`
  - 최신 `status`
- 이후 새 메시지 올 때마다 broadcast

### 메시지 보장

- 손실 허용 (특히 `pos`). 다음 메시지로 자연스럽게 갱신
- `plan`, `status` 는 중요하므로 백엔드가 마지막 값 캐시

## 2.E 백엔드 구현 예시 (FastAPI)

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio

app = FastAPI()

clients: set[WebSocket] = set()
latest = {"graph": None, "plan": None, "pos": None, "status": None}

@app.websocket("/ws/robot")
async def robot_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype in latest:
                latest[mtype] = msg
            dead = []
            for c in clients:
                try:
                    await c.send_json(msg)
                except Exception:
                    dead.append(c)
            for d in dead:
                clients.discard(d)
    except WebSocketDisconnect:
        pass

@app.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        for k in ("graph", "plan", "status", "pos"):
            if latest[k]:
                await ws.send_json(latest[k])
        while True:
            await ws.receive_text()  # ping/명령 무시
    except WebSocketDisconnect:
        clients.discard(ws)

@app.get("/graph")
def get_graph():
    return latest["graph"] or {"type": "graph", "nodes": {}, "edges": {}, "houses": {}}
```

## 2.F 백엔드 → 로봇 명령 (선택적 빈집 순찰 지시)

대시보드에서 사용자가 특정 빈집들만 순찰하도록 선택했을 때, 백엔드는 웹소켓(`wss://yc.jun0.dev/ws/robot`)을 통해 로봇에게 아래 메시지를 전송하여 임무를 시작하게 할 수 있습니다.

```json
{
  "type": "start_mission",
  "houses": ["H1", "H3", "H5"]
}
```

- 로봇은 이 메시지를 받으면 즉시 기존 동작을 멈추고 전달받은 `houses` 목록에 대해서만 최적 경로를 재계산하여 순찰 미션을 새로 시작합니다.

## 2.G 대시보드 그릴 거 (참고)

```
[전체 레이아웃]

┌─────────────────────────────────┬─────────────────────┐
│                                 │  미션 진행 패널     │
│        SVG/Canvas Map           │                     │
│                                 │  ▶ H1 (사진 ✓)     │
│   ●N0──●N1──●N2──●N3──●N4      │  ▶ H2 (사진 ✓)     │
│              │                  │  ▶ H3 (현재 진행)  │
│             🚗 ←차량             │  ○ H4              │
│              │                  │  ○ H5              │
│           🏠H1🏠H2               │  ○ H6              │
│              │                  │                     │
│              ───── 계획 경로     │  [최근 사진]        │
│                                 │  [현재 위치/yaw]    │
└─────────────────────────────────┴─────────────────────┘
```

기술 스택 자유 (React/Svelte/Vue/순수 HTML). 좌표는 odom 그대로 쓰고 SVG `viewBox` 로 화면에 매핑.

---

# 코드 변경 위치 (참고)

| 파일 | 변경 | 비고 |
|------|------|------|
| [patrol_planner/patrol_node.py](patrol_planner/patrol_node.py) | WS 클라이언트, plan/pos/status 송신 추가 | Phase 2 |
| [patrol_planner/graph.py](patrol_planner/graph.py) | `graph_to_json()` export 함수 | Phase 2 |
| [package.xml](package.xml) | `websocket-client` 의존성 | Phase 2 |

---

# 변경 이력

| 날짜       | 내용 |
|-----------|------|
| 2026-05-12 | 초안 작성 (Phase 1 구현 완료, Phase 2 스펙 정의) |
| 2026-05-12 | Phase 3 (실종자 탐지) 기능 및 스펙 추가 |

---

# Phase 3: 실종자 탐지 — 상세 스펙

## 3.A 엔드포인트

```
POST https://yc.jun0.dev/api/robots/{robotId}/missing-person
```

`{robotId}` 는 로봇 식별자 (예: `robot-01`).

## 3.B 요청

- Method: `POST`
- Content-Type: `multipart/form-data`

| 필드명 | 타입 | 필수 | 설명 | 예시 |
|---|---|---|---|---|
| `image` | file | ✓ | 탐지 순간의 캡처 이미지 (JPEG). 파일명: `<missing_person_id>_detected.jpg` | `P-001_detected.jpg` |
| `missing_person_id` | form text | ✓ | YOLO에서 탐지된 클래스 라벨 (실종자 ID) | `"P-001"` |
| `x` | form text | ✓ | 탐지 시점의 로봇 odom x 좌표 | `"5.12"` |
| `y` | form text | ✓ | 탐지 시점의 로봇 odom y 좌표 | `"-3.45"` |
| `timestamp` | form text | ✓ | ISO 8601 (`YYYY-MM-DDTHH:MM:SS`, UTC) | `"2026-05-12T20:00:00"` |

## 3.C 동작 특성

- **중복 전송 방지**: 동일한 실종자에 대해 여러 번 탐지되더라도, 로봇 노드 실행 주기 동안 **단 한 번만** 백엔드로 전송한다.
- **아키텍처**: 로봇의 이동 제어(`patrol_node`)와는 완전히 독립된 `yolo_detector_node`에서 카메라 프레임을 받아 추론하며, 비동기로 백엔드에 전송하므로 주행에 지장을 주지 않는다.
