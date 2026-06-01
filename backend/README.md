# Yeongcheon Robot Backend

API 명세 Markdown/CSV를 기준으로 만든 로컬 검증용 백엔드 서버입니다.

## 실행

```bash
python3 backend/server.py --host 127.0.0.1 --port 8000
```

## API 범위

- `POST /api/robots/{robotId}/status`
- `POST /api/robots/{robotId}/image`
- `POST /api/robots/{robotId}/lidar`
- `POST /agent/maintenance/analyze`
- `POST /agent/anomaly/analyze-image`
- `POST /agent/reconstruction/recommend`
- `POST /api/agent/reconstruction-results`
- `POST /api/agent/anomaly-results`
- `POST /api/agent/maintenance-results`
- `GET /api/dashboard`
- `GET /api/dashboard/robots/{robotId}`
- `GET /api/maintenance`
- `POST /api/maintenance/analyze`
- `GET /api/events`
- `POST /api/events/{eventId}/resolve`
- `GET /api/missing/amber`

`GET /api/missing/amber`는 클라이언트 입력 없이 서버 내부에 정의된 Safe182 form 값으로 원본 API를 호출한 뒤, 날짜/필드명/base64 이미지를 정리한 JSON을 반환합니다.

업로드 데이터는 `uploads/`에 저장되고, 간단한 상태 DB는 `data/db.json`에 저장됩니다.

## 업로드 확인 대시보드

서버 실행 후 브라우저에서 아래 주소를 열면 최근 로봇 상태, 이미지, 라이다 preview, 이벤트를 확인할 수 있습니다.

```text
http://127.0.0.1:8000/
```

## 검증

```bash
python3 tests/e2e_robot_backend.py
```

테스트는 실제 서버 프로세스를 띄운 뒤 명세상 모든 엔드포인트와 dashboard 정적 파일 응답까지 확인합니다.
