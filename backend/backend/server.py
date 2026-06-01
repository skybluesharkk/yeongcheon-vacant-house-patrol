#!/usr/bin/env python3
import argparse
import base64
import cgi
import hashlib
import json
import math
import os
import re
import shutil
import socket
import struct
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"
UPLOAD_DIR = ROOT_DIR / "uploads"
DB_PATH = DATA_DIR / "db.json"
DB_LOCK = threading.RLock()
SAFE182_AMBER_URL = "https://www.safe182.go.kr/api/lcm/amberList.do"
SAFE182_FORM = {
    "authKey": "3358c82e1c6548ad",
    "rowSize": "100",
    "esntlId": "10000936",
    "occrAdres": "경상"
}
MISSING_PERSON_PROFILES = {
    "P-1": {
        "rnum": 1,
        "occrde": "20260513",
        "alldressingDscd": None,
        "ageNow": "24",
        "age": 24,
        "writngTrgetDscd": "010",
        "sexdstnDscd": "남자",
        "occrAdres": "경상북도 영천시 완산동",
        "nm": "이민호",
        "nltyDscd": "내국인",
        "height": 178,
        "bdwgh": 68,
        "frmDscd": "보통",
        "faceshpeDscd": "계란형",
        "hairshpeDscd": "짧은머리(생머리)",
        "haircolrDscd": "흑색",
    }
}

# 에이전트(LangGraph + Gemini) 베이스 URL. 환경변수 AGENT_URL 로 오버라이드 가능.
# 기본값: 같은 호스트의 8001 포트 (README 규약)
AGENT_URL = os.environ.get("AGENT_URL", "http://127.0.0.1:8001").rstrip("/")

# 로봇이 보내는 houseId(H1~H6) ↔ 에이전트 mapping.csv 의 house_id(YC-001~006).
# 에이전트는 YC-xxx 로 정의돼 있고, mapping.csv 에 simulated_house 컬럼으로 H1~H6 가 매핑되어 있음.
# 우리(backend/frontend) 는 H1~H6 를 canonical 로 사용하므로,
# 에이전트 호출 직전에만 YC-xxx 로 변환하고, 응답에서는 다시 H 로 되돌린다.
HOUSE_ID_TO_AGENT_ID = {
    "H1": "YC-001",
    "H2": "YC-002",
    "H3": "YC-003",
    "H4": "YC-004",
    "H5": "YC-005",
    "H6": "YC-006",
}
AGENT_ID_TO_HOUSE_ID = {agent_id: house_id for house_id, agent_id in HOUSE_ID_TO_AGENT_ID.items()}


def to_agent_house_id(house_id):
    """H1 → YC-001. 매핑 없는 값은 그대로 반환 (예: 외부 주소 기반)."""
    return HOUSE_ID_TO_AGENT_ID.get(house_id or "", house_id)


def from_agent_house_id(agent_house_id):
    """YC-001 → H1. 매핑 없는 값은 그대로 반환."""
    return AGENT_ID_TO_HOUSE_ID.get(agent_house_id or "", agent_house_id)


def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def missing_person_age_label(profile):
    if not profile:
        return None
    age_now = profile.get("ageNow") or profile.get("age")
    return f"{age_now}세" if age_now not in (None, "") else None


def missing_person_profile_item(profile_id, profile):
    item = dict(profile)
    item["msspsnIdntfccd"] = profile_id
    return item


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def initial_db():
    return {
        "robots": {},
        "images": [],
        "lidarScans": [],
        "missingDetections": [],
        "events": [],
        "maintenancePriorities": [],
        "maintenanceJobs": [],
        "reconstructionResults": [],
        # 사용자가 대시보드 "직접 추천받기" 모달에서 보낸 수동 요청들.
        # 각 항목 구조: {requestId, address, userNote, photoUrl, status, createdAt,
        #              respondedAt, recommendation, errorMessage}
        "manualReconstructionRequests": [],
        "agentJobs": [],
        "graph": {"type": "graph", "nodes": {}, "edges": {}, "houses": {}},
        "wsLatest": {"graph": None, "plan": None, "pos": None, "status": None},
    }


def load_db():
    ensure_dirs()
    if not DB_PATH.exists():
        return initial_db()
    with DB_LOCK:
        with DB_PATH.open("r", encoding="utf-8") as f:
            text = f.read()
        try:
            db = json.loads(text)
        except json.JSONDecodeError as exc:
            decoder = json.JSONDecoder()
            try:
                db, end = decoder.raw_decode(text)
            except json.JSONDecodeError:
                raise exc
            if text[end:].strip():
                sys.stderr.write(
                    f"Recovered db.json with trailing invalid data: {exc}\n"
                )
    for key, value in initial_db().items():
        db.setdefault(key, value)
    return db


def save_db(db):
    ensure_dirs()
    with DB_LOCK:
        tmp_path = DB_PATH.with_name(
            f"{DB_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp_path.replace(DB_PATH)


def public_path(path):
    return "/" + path.relative_to(ROOT_DIR).as_posix()


def safe_name(filename):
    base = Path(filename or "upload.bin").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned or "upload.bin"


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON body: {exc}") from exc


def require_fields(payload, fields):
    missing = [field for field in fields if field not in payload]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))


def parse_multipart(handler):
    ctype, pdict = cgi.parse_header(handler.headers.get("Content-Type", ""))
    if ctype != "multipart/form-data":
        raise ValueError("Content-Type must be multipart/form-data")
    pdict["boundary"] = bytes(pdict["boundary"], "utf-8")
    pdict["CONTENT-LENGTH"] = int(handler.headers.get("Content-Length", "0"))
    return cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": handler.headers.get("Content-Type"),
        },
        keep_blank_values=True,
    )


def form_text(form, field, required=True):
    item = form[field] if field in form else None
    if item is None or getattr(item, "filename", None):
        if required:
            raise ValueError(f"missing form field: {field}")
        return None
    return item.value


def form_float(form, field, required=True):
    value = form_text(form, field, required=required)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc


def form_file(form, field):
    item = form[field] if field in form else None
    if item is None or not getattr(item, "filename", None):
        raise ValueError(f"missing file field: {field}")
    return item


def normalize_house_id(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if upper in AGENT_ID_TO_HOUSE_ID:
        return AGENT_ID_TO_HOUSE_ID[upper]
    match = re.fullmatch(r"H-?0*([1-9][0-9]*)", upper)
    if match:
        return f"H{int(match.group(1))}"
    return text


def infer_house_id_from_upload(item):
    filename = getattr(item, "filename", "") or ""
    stem = Path(filename).stem
    match = re.search(r"(YC-\d{3}|H-?0*[1-9][0-9]*)", stem, re.IGNORECASE)
    if not match:
        return None
    return normalize_house_id(match.group(1))


def write_upload(robot_id, group, item):
    filename = f"{int(time.time() * 1000)}_{safe_name(item.filename)}"
    target_dir = UPLOAD_DIR / "robots" / robot_id / group
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    with target.open("wb") as f:
        shutil.copyfileobj(item.file, f)
    return target


def lidar_points(array):
    arr = np.asarray(array, dtype=float)
    arr = np.squeeze(arr)
    if arr.ndim == 1:
        angles = np.linspace(-math.pi, math.pi, arr.shape[0], endpoint=False)
        points = np.column_stack((arr * np.cos(angles), arr * np.sin(angles)))
        distances = arr
    elif arr.ndim == 2 and arr.shape[1] >= 2:
        points = arr[:, :2]
        distances = np.linalg.norm(points, axis=1)
    else:
        flat = arr.reshape(-1)
        angles = np.linspace(-math.pi, math.pi, flat.shape[0], endpoint=False)
        points = np.column_stack((flat * np.cos(angles), flat * np.sin(angles)))
        distances = flat
    finite = np.isfinite(distances)
    return points[finite], distances[finite]


def load_lidar_file(path):
    if path.suffix == ".npz":
        loaded = np.load(path)
        if not loaded.files:
            raise ValueError("npz file has no arrays")
        return loaded[loaded.files[0]]
    return np.load(path)


def summarize_lidar(distances, points):
    if distances.size == 0:
        raise ValueError("lidar array has no finite points")
    angles = np.arctan2(points[:, 1], points[:, 0])
    close = distances < 1.5
    obstacle_count = int(np.count_nonzero(close))
    return {
        "pointCount": int(distances.size),
        "minDistance": round(float(np.min(distances)), 3),
        "maxDistance": round(float(np.max(distances)), 3),
        "avgDistance": round(float(np.mean(distances)), 3),
        "obstacleDetected": obstacle_count > 0,
        "obstacleCount": obstacle_count,
        "frontBlocked": bool(np.any(close & (np.abs(angles) <= math.pi / 6))),
        "leftBlocked": bool(np.any(close & (angles > math.pi / 6) & (angles < 5 * math.pi / 6))),
        "rightBlocked": bool(np.any(close & (angles < -math.pi / 6) & (angles > -5 * math.pi / 6))),
    }


def render_lidar_preview(points, target):
    size = 512
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    center = size // 2
    draw.line((center, 0, center, size), fill=(230, 230, 230))
    draw.line((0, center, size, center), fill=(230, 230, 230))
    max_abs = float(np.max(np.abs(points))) if points.size else 1.0
    scale = (size * 0.44) / max(max_abs, 1e-6)
    for x, y in points:
        px = int(center + x * scale)
        py = int(center - y * scale)
        draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=(27, 107, 207))
    draw.ellipse((center - 5, center - 5, center + 5, center + 5), fill=(220, 60, 60))
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)


def robot_dashboard(robot_id, robot):
    return {
        "robotId": robot_id,
        "status": robot.get("status"),
        "battery": robot.get("battery"),
        "x": robot.get("x"),
        "y": robot.get("y"),
        "address": robot.get("address"),
        "nextDestination": robot.get("nextDestination"),
        "velocity": robot.get("velocity"),
        "timestamp": robot.get("timestamp"),
        "latestImage": robot.get("latestImage"),
        "latestLidar": robot.get("latestLidar"),
        "patrolPath": robot.get("patrolPath", []),
        "updatedAt": robot.get("updatedAt"),
    }


def dashboard_payload(db, selected_robot_id=None):
    robots = [robot_dashboard(robot_id, robot) for robot_id, robot in db["robots"].items()]
    selected = selected_robot_id or (robots[0]["robotId"] if robots else None)
    unresolved_events = [event for event in db["events"] if not event.get("resolved")]
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    urgent_event = None
    if unresolved_events:
        urgent_event = sorted(unresolved_events, key=lambda event: severity_order.get(event.get("severity"), 9))[0]
    return {
        "weather": {"condition": "CLEAR", "temperature": 22, "updatedAt": now_iso()},
        "robots": robots,
        "selectedRobotId": selected,
        "vacantHouseMap": [],
        "maintenancePriorities": db["maintenancePriorities"],
        "reconstruction": db["reconstructionResults"],
        "urgentEvent": urgent_event,
        "stats": {
            "activeRobotCount": sum(1 for robot in robots if robot.get("status") not in {None, "IDLE", "OFFLINE"}),
            "totalRobotCount": len(robots),
            "eventCount": len(db["events"]),
            "unresolvedEventCount": len(unresolved_events),
            "imageCount": len(db["images"]),
            "lidarScanCount": len(db["lidarScans"]),
        },
    }


def next_job_id(prefix, count):
    return f"{prefix}-{count + 1:04d}"


def format_yyyymmdd(value):
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text or None


def compact_photo(raw):
    compact = re.sub(r"\s+", "", raw or "")
    if not compact:
        return None
    return {
        "mimeType": "image/jpeg",
        "byteLength": len(compact),
        "dataUrl": f"data:image/jpeg;base64,{compact}",
    }


def clean_amber_payload(payload):
    items = []
    for item in payload.get("list", []):
        items.append(
            {
                "rowNumber": item.get("rnum"),
                "missingPersonId": item.get("msspsnIdntfccd"),
                "name": item.get("nm"),
                "gender": item.get("sexdstnDscd"),
                "nationality": item.get("nltyDscd"),
                "missingDate": format_yyyymmdd(item.get("occrde")),
                "missingAddress": (item.get("occrAdres") or "").strip(),
                "ageNow": item.get("ageNow"),
                "ageAtMissing": item.get("age"),
                "heightCm": item.get("height"),
                "weightKg": item.get("bdwgh"),
                "bodyType": item.get("frmDscd"),
                "faceShape": item.get("faceshpeDscd"),
                "hairShape": item.get("hairshpeDscd"),
                "hairColor": item.get("haircolrDscd"),
                "clothing": item.get("alldressingDscd"),
                "targetCode": item.get("writngTrgetDscd"),
                "photo": compact_photo(item.get("tknphotoFile")),
            }
        )
    return {
        "success": payload.get("result") == "00",
        "result": payload.get("result"),
        "message": payload.get("msg"),
        "totalCount": payload.get("totalCount", len(items)),
        "count": len(items),
        "items": items,
    }


def fetch_amber_alerts():
    combined_list = []
    success = False
    message = ""
    for region in ["경북", "경상", "경남"]:
        form_data = dict(SAFE182_FORM)
        form_data["occrAdres"] = region
        body = urlencode(form_data).encode("utf-8")
        request = Request(
            SAFE182_AMBER_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = json.loads(response.read().decode(charset))
                combined_list.extend(payload.get("list", []))
                if payload.get("result") == "00":
                    success = True
                message = payload.get("msg", message)
        except Exception as e:
            sys.stderr.write(f"Safe182 fetch failed for {region}: {e}\n")
            
    seen = set()
    unique_list = []
    for profile_id, profile in MISSING_PERSON_PROFILES.items():
        seen.add(profile_id)
        unique_list.append(missing_person_profile_item(profile_id, profile))
    for item in combined_list:
        mid = item.get("msspsnIdntfccd")
        if mid and mid not in seen:
            seen.add(mid)
            unique_list.append(item)
        elif not mid:
            unique_list.append(item)

    combined_payload = {
        "result": "00" if success else "99",
        "msg": message,
        "totalCount": len(unique_list),
        "list": unique_list
    }
    return clean_amber_payload(combined_payload)


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_DASHBOARD_CLIENTS = set()
WS_ROBOT_CLIENTS = set()
WS_LOCK = threading.Lock()


def websocket_accept(key):
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def read_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def recv_ws_text(sock):
    header = read_exact(sock, 2)
    if not header:
        return None
    first, second = header
    opcode = first & 0x0F
    masked = second & 0x80
    length = second & 0x7F
    if opcode == 0x8:
        return None
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
    payload = read_exact(sock, length) or b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if opcode != 0x1:
        return ""
    return payload.decode("utf-8")


def send_ws_text(sock, message):
    payload = message.encode("utf-8")
    if len(payload) < 126:
        header = struct.pack("!BB", 0x81, len(payload))
    elif len(payload) < 65536:
        header = struct.pack("!BBH", 0x81, 126, len(payload))
    else:
        header = struct.pack("!BBQ", 0x81, 127, len(payload))
    sock.sendall(header + payload)


def broadcast_dashboard(message):
    dead = []
    text = json.dumps(message, ensure_ascii=False)
    with WS_LOCK:
        clients = list(WS_DASHBOARD_CLIENTS)
    for client in clients:
        try:
            send_ws_text(client, text)
        except OSError:
            dead.append(client)
    if dead:
        with WS_LOCK:
            for client in dead:
                WS_DASHBOARD_CLIENTS.discard(client)


def broadcast_robot(message):
    """대시보드(프론트)에서 받은 명령을 로봇 측 WS 클라이언트들에게 전달."""
    dead = []
    text = json.dumps(message, ensure_ascii=False)
    with WS_LOCK:
        clients = list(WS_ROBOT_CLIENTS)
    for client in clients:
        try:
            send_ws_text(client, text)
        except OSError:
            dead.append(client)
    if dead:
        with WS_LOCK:
            for client in dead:
                WS_ROBOT_CLIENTS.discard(client)


# ---------------------------------------------------------------------------
# 에이전트 통합: 사진 업로드 -> 에이전트 비동기 호출 -> 결과를 events 에 저장
# ---------------------------------------------------------------------------
def call_agent_patrol_image(robot_id, image_id, image_url, house_id, jpeg_path):
    """
    별도 스레드에서 동작. 차량이 올린 JPEG 를 base64 로 인코딩해서
    에이전트(POST /agents/patrol-image)에 보내고, 응답을 events 에 반영 + WS 알림.

    에이전트 호출이 보통 5~30초 걸리므로, 차량 측 POST 응답은 막지 않음.
    """
    try:
        # 1) houseId(H1~H6) → agent house_id(YC-001~006)
        agent_house_id = to_agent_house_id(house_id)
        if not agent_house_id or agent_house_id == house_id:
            # 매핑 테이블에 없는 ID → 분석 스킵 (외부 임의 빈집은 아직 미지원)
            sys.stderr.write(
                f"[AGENT] 매핑 없는 house_id={house_id!r} → 분석 스킵\n"
            )
            return

        # 2) JPEG → base64
        try:
            jpeg_bytes = Path(jpeg_path).read_bytes()
        except OSError as exc:
            sys.stderr.write(f"[AGENT] 사진 파일 읽기 실패 {jpeg_path}: {exc}\n")
            return
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")

        # 3) agent 호출
        payload = json.dumps({
            "house_id": agent_house_id,
            "captured_image_base64": b64,
            "captured_at": now_iso(),
        }, ensure_ascii=False).encode("utf-8")
        req = Request(
            f"{AGENT_URL}/agents/patrol-image",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        sys.stderr.write(
            f"[AGENT] 호출 시작: house_id={house_id}→{agent_house_id}, "
            f"image_id={image_id}, bytes={len(jpeg_bytes)}\n"
        )
        try:
            with urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            sys.stderr.write(
                f"[AGENT] 호출 실패 (house_id={house_id}, image_id={image_id}): {exc}\n"
            )
            return

        is_anomaly = bool(result.get("is_anomaly"))
        risk_level = (result.get("risk_level") or "low").lower()
        summary = result.get("summary") or ""
        sys.stderr.write(
            f"[AGENT] 응답 수신: house_id={house_id}, is_anomaly={is_anomaly}, "
            f"risk={risk_level}\n"
        )

        # 4) DB 업데이트: images 의 analysisResult 갱신 + 이상이면 event 추가
        db = load_db()
        for img in db["images"]:
            if img.get("imageId") == image_id:
                img["analysisResult"] = "이상징후" if is_anomaly else "정상"
                img["analysisSummary"] = summary or None
                break

        event = None
        if is_anomaly:
            event_id = f"EV-{len(db['events']) + 1:04d}"
            severity_map = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
            event = {
                "eventId": event_id,
                "robotId": robot_id,
                "imageId": image_id,
                "houseId": house_id,
                "eventType": "PATROL_ANOMALY",
                "severity": severity_map.get(risk_level, "MEDIUM"),
                "summary": summary or "순찰 이상 징후 감지",
                "detectedObjects": result.get("evidence", []) or [],
                "recommendedActions": result.get("recommended_actions", []) or [],
                "imageUrl": image_url,
                "resolved": False,
                "createdAt": now_iso(),
            }
            db["events"].append(event)
        save_db(db)

        # 5) 대시보드 실시간 알림
        broadcast_dashboard({
            "type": "anomaly_result",
            "robot_id": robot_id,
            "house_id": house_id,
            "image_id": image_id,
            "image_url": image_url,
            "is_anomaly": is_anomaly,
            "risk_level": risk_level,
            "summary": summary,
            "evidence": result.get("evidence", []) or [],
            "recommended_actions": result.get("recommended_actions", []) or [],
            "event_id": event["eventId"] if event else None,
        })
    except Exception as exc:
        sys.stderr.write(
            f"[AGENT] 예기치 못한 오류 (house_id={house_id}, image_id={image_id}): {exc}\n"
        )


def send_to_agent_async(robot_id, image_id, image_url, house_id, jpeg_path):
    """call_agent_patrol_image 를 백그라운드 스레드로 실행."""
    threading.Thread(
        target=call_agent_patrol_image,
        args=(robot_id, image_id, image_url, house_id, jpeg_path),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# 에이전트 통합: 재건축 추천 (동기 호출 — 프론트가 결과를 직접 받음)
# ---------------------------------------------------------------------------
AGENT_FIXTURE_DIR = ROOT_DIR.parent / "yeongcheon-vacant-house-agent" / "data" / "house"


def resolve_photo_b64_for_house(house_id):
    """
    해당 빈집에 사용할 사진의 base64 를 반환.
      1순위: DB 의 images 중 houseId 가 일치하는 가장 최근 업로드 (실제 차량 사진)
      2순위: agent fixture 의 <Hx>_without_roof.txt
      3순위: None (사진 못 찾음)
    """
    if not house_id:
        return None
    # 1순위
    try:
        db = load_db()
        for img in reversed(db.get("images", [])):
            if img.get("houseId") == house_id and img.get("imageUrl"):
                local = ROOT_DIR / img["imageUrl"].lstrip("/")
                if local.exists():
                    return base64.b64encode(local.read_bytes()).decode("ascii")
    except Exception as exc:
        sys.stderr.write(f"[AGENT] DB photo lookup failed: {exc}\n")
    # 2순위
    fixture = AGENT_FIXTURE_DIR / f"{house_id}_without_roof.txt"
    if fixture.exists():
        try:
            return fixture.read_text(encoding="utf-8").strip()
        except Exception as exc:
            sys.stderr.write(f"[AGENT] fixture read failed for {fixture}: {exc}\n")
    return None


def call_agent_redevelopment(house_id, address, photo_b64):
    """
    동기 호출. agent /agents/redevelopment-recommendation 로 POST 후 응답을 그대로 dict 반환.
    실패 시 None.
    """
    if not address:
        return None, "address 가 비어 있음"
    if not photo_b64:
        return None, "사진을 찾을 수 없음 (DB / fixture 모두 없음)"

    payload = json.dumps({
        "house_id": to_agent_house_id(house_id),
        "address": address,
        "photo_image_base64": photo_b64,
        "photo_image_mime_type": "image/jpeg",
    }, ensure_ascii=False).encode("utf-8")

    req = Request(
        f"{AGENT_URL}/agents/redevelopment-recommendation",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8")), None
    except Exception as exc:
        return None, f"agent 호출 실패: {exc}"


def map_redevelopment_to_frontend(house_id, agent_result):
    """
    agent 응답 (recommended_use, explanation, rationale, required_data) →
    프론트 ReconstructionRecommendation (houseId, recommendedUse, buildingScale,
    expectedCost, expectedReturn, feasibility, reason) 형식으로 매핑.

    agent 응답에 없는 buildingScale/expectedCost/expectedReturn/feasibility 는
    합리적인 placeholder 로 채움 (요청 시 향후 별도 모델/룰 추가 가능).
    """
    explanation = agent_result.get("explanation") or ""
    rationale = agent_result.get("rationale") or []
    required = agent_result.get("required_data") or []

    # reason 은 explanation + 주요 근거 1줄로 합쳐서 풍부하게
    reason_lines = [explanation]
    if rationale:
        reason_lines.append("근거: " + " · ".join(str(x) for x in rationale[:3]))
    if required:
        reason_lines.append("추가 확인 필요: " + ", ".join(str(x) for x in required[:3]))

    return {
        "houseId": house_id,
        "recommendedUse": agent_result.get("recommended_use") or "분석 결과 참고",
        "buildingScale": "기존 골조 활용 (분석 결과 참고)",
        "expectedCost": "별도 산정 필요",
        "expectedReturn": "주민 편의 / 공공 활용",
        "feasibility": "보통",
        "reason": "\n".join(line for line in reason_lines if line),
        "rationale": [str(item) for item in rationale],
        "requiredData": [str(item) for item in required],
        "generatedImageUrl": agent_result.get("generated_image_url") or agent_result.get("image_url"),
        "source": "agent",
    }


class RobotBackendHandler(SimpleHTTPRequestHandler):
    server_version = "YeongcheonRobotBackend/0.1"

    def translate_path(self, path):
        parsed = urlparse(path)
        if parsed.path.startswith("/uploads/"):
            return str(ROOT_DIR / parsed.path.lstrip("/"))
        if parsed.path == "/":
            return str(STATIC_DIR / "dashboard.html")
        if parsed.path.startswith("/static/"):
            return str(ROOT_DIR / parsed.path.lstrip("/"))
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json(status, {"success": False, "error": message})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if self.headers.get("Upgrade", "").lower() == "websocket":
            if path == "/ws/robot":
                self.handle_robot_ws()
                return
            if path == "/ws/dashboard":
                self.handle_dashboard_ws()
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "websocket endpoint not found")
            return
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True, "service": "robot-backend"})
            return
        if path == "/" or path.startswith("/static/") or path.startswith("/uploads/"):
            return super().do_GET()
        if path == "/api/dashboard":
            db = load_db()
            query = parse_qs(parsed.query)
            selected = query.get("selectedRobotId", [None])[0]
            self.send_json(HTTPStatus.OK, dashboard_payload(db, selected))
            return
        if path == "/api/maintenance":
            db = load_db()
            self.send_json(HTTPStatus.OK, db["maintenancePriorities"])
            return
        if path == "/api/events":
            db = load_db()
            query = parse_qs(parsed.query)
            events = list(db["events"])
            for key in ("robotId", "eventType", "severity"):
                if key in query:
                    events = [event for event in events if str(event.get(key)) == query[key][0]]
            if "resolved" in query:
                expected = query["resolved"][0].lower() == "true"
                events = [event for event in events if bool(event.get("resolved")) is expected]
            limit = int(query.get("limit", [len(events)])[0] or len(events))
            self.send_json(HTTPStatus.OK, events[:limit])
            return
        if path == "/api/missing/amber":
            self.send_json(HTTPStatus.OK, fetch_amber_alerts())
            return
        if path == "/api/images":
            db = load_db()
            query = parse_qs(parsed.query)
            images = list(db["images"])
            house_id_filter = query.get("houseId", [None])[0]
            if house_id_filter:
                images = [img for img in images if img.get("houseId") == house_id_filter]
            self.send_json(HTTPStatus.OK, images)
            return
        if path == "/api/missing/detections":
            db = load_db()
            self.send_json(HTTPStatus.OK, db["missingDetections"])
            return
        if path == "/api/reconstruction/manual-requests":
            # 사용자 수동 추천 요청 기록 (최신순)
            db = load_db()
            items = list(reversed(db.get("manualReconstructionRequests", [])))
            self.send_json(HTTPStatus.OK, items)
            return
        if path == "/api/graph":
            db = load_db()
            self.send_json(HTTPStatus.OK, db["graph"])
            return
        if path == "/api/reconstruction/manual-requests":
            db = load_db()
            # 최신 요청이 위로 오도록 역순 반환
            items = list(reversed(db.get("manualReconstructionRequests", [])))
            self.send_json(HTTPStatus.OK, items)
            return
        match = re.fullmatch(r"/api/dashboard/robots/([^/]+)", path)
        if match:
            robot_id = match.group(1)
            db = load_db()
            robot = db["robots"].get(robot_id)
            if robot is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "robot not found")
                return
            self.send_json(HTTPStatus.OK, robot_dashboard(robot_id, robot))
            return
        # 빈집 기준(베이스라인) 이미지 — agent fixture 의 with_roof 사진을 그대로 서빙.
        # 프론트 "AI 이상 탐지 요약" 에서 순찰 사진 옆에 비교용으로 표시.
        match = re.fullmatch(r"/api/houses/([^/]+)/baseline-image", path)
        if match:
            self.handle_baseline_image(match.group(1))
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def handle_baseline_image(self, house_id):
        fixture = AGENT_FIXTURE_DIR / f"{house_id}_with_roof.jpg"
        if not fixture.exists():
            self.send_error_json(
                HTTPStatus.NOT_FOUND,
                f"no baseline image for house_id={house_id}",
            )
            return
        try:
            data = fixture.read_bytes()
        except OSError as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            match = re.fullmatch(r"/api/robots/([^/]+)/status", path)
            if match:
                self.handle_status(match.group(1))
                return
            match = re.fullmatch(r"/api/robots/([^/]+)/image", path)
            if match:
                self.handle_image(match.group(1))
                return
            match = re.fullmatch(r"/api/robots/([^/]+)/lidar", path)
            if match:
                self.handle_lidar(match.group(1))
                return
            match = re.fullmatch(r"/api/robots/([^/]+)/missing-person", path)
            if match:
                self.handle_missing_person(match.group(1))
                return
            match = re.fullmatch(r"/api/events/([^/]+)/resolve", path)
            if match:
                self.handle_event_resolve(match.group(1))
                return
            match = re.fullmatch(r"/api/missing/detections/([^/]+)/acknowledge", path)
            if match:
                self.handle_detection_acknowledge(match.group(1))
                return
            if path == "/api/graph":
                self.handle_graph()
                return
            if path == "/api/maintenance/analyze":
                self.handle_maintenance_analyze()
                return
            if path == "/api/reconstruction/manual-request":
                self.handle_manual_reconstruction_request()
                return
            if path == "/api/agent/reconstruction-results":
                self.handle_reconstruction_results()
                return
            if path == "/api/agent/anomaly-results":
                self.handle_anomaly_results()
                return
            if path == "/api/agent/maintenance-results":
                self.handle_maintenance_results()
                return
            if path == "/agent/maintenance/analyze":
                self.handle_agent_job("MA-JOB", ["jobId", "callbackUrl", "area", "vacantHouses", "populationData", "recentEvents"])
                return
            if path == "/agent/anomaly/analyze-image":
                self.handle_agent_job("AN-JOB", ["jobId", "callbackUrl", "robotId", "imageId", "imageUrl", "x", "y", "address", "timestamp"])
                return
            if path == "/agent/reconstruction/recommend":
                self.handle_agent_job(
                    "RC-JOB",
                    [
                        "jobId",
                        "callbackUrl",
                        "houseId",
                        "address",
                        "riskLevel",
                        "agingRate",
                        "accessibility",
                        "populationContext",
                        "beforeImageUrl",
                    ],
                )
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def robot_record(self, db, robot_id):
        return db["robots"].setdefault(robot_id, {"patrolPath": []})

    def ws_handshake(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing Sec-WebSocket-Key")
            return False
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", websocket_accept(key))
        self.end_headers()
        return True

    def handle_robot_ws(self):
        if not self.ws_handshake():
            return
        sock = self.request
        # 대시보드(프론트)에서 /api/missions/start 또는 WS 명령이 들어왔을 때
        # 이 로봇 소켓으로 중계할 수 있도록 등록.
        with WS_LOCK:
            WS_ROBOT_CLIENTS.add(sock)
        try:
            while True:
                try:
                    text = recv_ws_text(sock)
                except (OSError, UnicodeDecodeError):
                    return
                if text is None:
                    return
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                mtype = message.get("type")
                db = load_db()
                if mtype in {"graph", "plan", "pos", "status"}:
                    db["wsLatest"][mtype] = message
                    if mtype == "graph":
                        db["graph"] = message
                save_db(db)
                broadcast_dashboard(message)
        finally:
            with WS_LOCK:
                WS_ROBOT_CLIENTS.discard(sock)

    def handle_dashboard_ws(self):
        if not self.ws_handshake():
            return
        sock = self.request
        with WS_LOCK:
            WS_DASHBOARD_CLIENTS.add(sock)
        try:
            db = load_db()
            latest = db["wsLatest"]
            for key in ("graph", "plan", "status", "pos"):
                if latest.get(key):
                    try:
                        send_ws_text(sock, json.dumps(latest[key], ensure_ascii=False))
                    except OSError:
                        return
            while True:
                try:
                    text = recv_ws_text(sock)
                except (OSError, UnicodeDecodeError):
                    return
                if text is None:
                    return
                # 대시보드에서 온 메시지 처리: 현재는 start_mission 만 로봇으로 중계.
                # 다른 타입 메시지가 필요해지면 여기에 화이트리스트로 추가.
                try:
                    msg = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if msg.get("type") == "start_mission":
                    broadcast_robot(msg)
        finally:
            with WS_LOCK:
                WS_DASHBOARD_CLIENTS.discard(sock)

    def handle_status(self, robot_id):
        payload = read_json(self)
        required = ["status", "battery", "x", "y", "address", "nextDestination", "velocity", "timestamp"]
        require_fields(payload, required)
        db = load_db()
        robot = self.robot_record(db, robot_id)
        robot.update({field: payload[field] for field in required})
        robot["updatedAt"] = now_iso()
        robot.setdefault("patrolPath", []).append(
            {"x": payload["x"], "y": payload["y"], "timestamp": payload["timestamp"]}
        )
        save_db(db)
        self.send_json(HTTPStatus.OK, {"success": True, "robotId": robot_id})

    def handle_image(self, robot_id):
        form = parse_multipart(self)
        image = form_file(form, "image")
        x = form_float(form, "x")
        y = form_float(form, "y")
        address = form_text(form, "address", required=False)
        house_id = normalize_house_id(
            form_text(form, "houseId", required=False)
            or form_text(form, "house_id", required=False)
            or form_text(form, "house", required=False)
            or infer_house_id_from_upload(image)
        )
        timestamp = form_text(form, "timestamp")
        path = write_upload(robot_id, "images", image)
        db = load_db()
        image_id = len(db["images"]) + 1
        image_url = public_path(path)
        record = {
            "imageId": image_id,
            "robotId": robot_id,
            "imageUrl": image_url,
            "houseId": house_id,
            "x": x,
            "y": y,
            "address": address,
            "timestamp": timestamp,
            "analysisResult": "분석중",
            "analysisSummary": None,
            "createdAt": now_iso(),
        }
        db["images"].append(record)
        robot = self.robot_record(db, robot_id)
        robot["latestImage"] = record
        robot.setdefault("patrolPath", []).append({"x": x, "y": y, "timestamp": timestamp})
        save_db(db)
        # 대시보드 실시간 갱신용 broadcast (frontend useRobotWebSocket 가 구독)
        broadcast_dashboard({
            "type": "photo_captured",
            "robot_id": robot_id,
            "url": image_url,
            "house_id": house_id,
            "x": x,
            "y": y,
            "timestamp": timestamp,
        })
        # 에이전트(Gemini)로 이상 분석 위임 (백그라운드, 제어 응답 막지 않음).
        # 결과는 events 에 누적되고 'anomaly_result' WS 로 대시보드에 푸시됨.
        send_to_agent_async(robot_id, image_id, image_url, house_id, path)
        self.send_json(
            HTTPStatus.OK,
            {
                "success": True,
                "robotId": robot_id,
                "imageId": image_id,
                "imageUrl": image_url,
                "analysisJobId": f"AN-JOB-{image_id:04d}",
            },
        )

    def handle_missing_person(self, robot_id):
        form = parse_multipart(self)
        image = form_file(form, "image")
        missing_person_id = form_text(form, "missing_person_id")
        x = form_float(form, "x")
        y = form_float(form, "y")
        timestamp = form_text(form, "timestamp")
        profile = MISSING_PERSON_PROFILES.get(missing_person_id, {})
        candidate_name = form_text(form, "candidate_name", required=False) or profile.get("nm")
        candidate_age_label = (
            form_text(form, "candidate_age_label", required=False)
            or missing_person_age_label(profile)
        )
        candidate_gender = form_text(form, "candidate_gender", required=False) or profile.get("sexdstnDscd")
        similarity = form_float(form, "similarity", required=False)
        camera_label = form_text(form, "camera_label", required=False)
        location = form_text(form, "location", required=False)
        path = write_upload(robot_id, "missing-person", image)
        db = load_db()
        detection_id = f"MP-{len(db['missingDetections']) + 1:04d}"
        image_url = public_path(path)
        location_text = location or profile.get("occrAdres") or f"좌표 ({x}, {y})"
        record = {
            "detectionId": detection_id,
            "robotId": robot_id,
            "missingPersonId": missing_person_id,
            "imageUrl": image_url,
            "x": x,
            "y": y,
            "timestamp": timestamp,
            "candidateName": candidate_name,
            "candidateAgeLabel": candidate_age_label,
            "candidateGender": candidate_gender,
            "similarity": similarity,
            "cameraLabel": camera_label,
            "location": location_text,
            "description": (
                f"순찰 중인 로봇이 {candidate_name}님으로 추정되는 사람을 카메라로 잡았어요."
                if candidate_name
                else f"순찰 중인 로봇이 {missing_person_id} 후보를 카메라로 잡았어요."
            ),
            "evidenceSummary": (
                f"{profile.get('height')}cm, {profile.get('bdwgh')}kg, "
                f"{profile.get('frmDscd')}, {profile.get('hairshpeDscd')}, {profile.get('haircolrDscd')}"
                if profile
                else None
            ),
            "createdAt": now_iso(),
        }
        db["missingDetections"].append(record)
        event_id = f"EV-{len(db['events']) + 1:04d}"
        db["events"].append(
            {
                "eventId": event_id,
                "robotId": robot_id,
                "eventType": "MISSING_PERSON",
                "severity": "HIGH",
                "title": f"실종자 후보 발견: {missing_person_id}",
                "summary": f"실종자 탐지: {missing_person_id}",
                "location": location_text,
                "confidence": similarity,
                "imageUrl": image_url,
                "resolved": False,
                "missingPersonId": missing_person_id,
                "detectionId": detection_id,
                "createdAt": now_iso(),
            }
        )
        save_db(db)
        # 대시보드 실시간 알림 broadcast (frontend useRobotWebSocket 가 구독)
        broadcast_dashboard({
            "type": "missing_person_detected",
            "robot_id": robot_id,
            "missing_person_id": missing_person_id,
            "candidate_name": candidate_name,
            "candidate_age_label": candidate_age_label,
            "candidate_gender": candidate_gender,
            "location": location_text,
            "similarity": similarity,
            "camera_label": camera_label,
            "detection_id": detection_id,
            "event_id": event_id,
            "url": image_url,
            "x": x,
            "y": y,
            "timestamp": timestamp,
        })
        self.send_json(
            HTTPStatus.OK,
            {
                "success": True,
                "robotId": robot_id,
                "detectionId": detection_id,
                "missingPersonId": missing_person_id,
                "imageUrl": image_url,
                "eventId": event_id,
            },
        )

    def handle_lidar(self, robot_id):
        form = parse_multipart(self)
        lidar_file = form_file(form, "lidarFile")
        x = form_float(form, "x")
        y = form_float(form, "y")
        timestamp = form_text(form, "timestamp")
        path = write_upload(robot_id, "lidar", lidar_file)
        if path.suffix not in {".npy", ".npz"}:
            raise ValueError("lidarFile must be .npy or .npz")
        array = load_lidar_file(path)
        points, distances = lidar_points(array)
        summary = summarize_lidar(distances, points)
        preview_name = path.with_suffix(".png").name
        preview_path = UPLOAD_DIR / "robots" / robot_id / "lidar-preview" / preview_name
        render_lidar_preview(points, preview_path)
        db = load_db()
        lidar_id = len(db["lidarScans"]) + 1
        lidar_url = public_path(path)
        preview_url = public_path(preview_path)
        record = {
            "lidarId": lidar_id,
            "robotId": robot_id,
            "lidarFileUrl": lidar_url,
            "previewImageUrl": preview_url,
            "summary": summary,
            "x": x,
            "y": y,
            "timestamp": timestamp,
            "createdAt": now_iso(),
        }
        db["lidarScans"].append(record)
        robot = self.robot_record(db, robot_id)
        robot["latestLidar"] = record
        robot.setdefault("patrolPath", []).append({"x": x, "y": y, "timestamp": timestamp})
        save_db(db)
        self.send_json(
            HTTPStatus.OK,
            {
                "success": True,
                "robotId": robot_id,
                "lidarId": lidar_id,
                "lidarFileUrl": lidar_url,
                "previewImageUrl": preview_url,
                "summary": summary,
            },
        )

    def handle_graph(self):
        payload = read_json(self)
        require_fields(payload, ["type", "nodes", "edges", "houses"])
        if payload["type"] != "graph":
            raise ValueError("type must be graph")
        db = load_db()
        db["graph"] = payload
        db["wsLatest"]["graph"] = payload
        save_db(db)
        broadcast_dashboard(payload)
        self.send_json(
            HTTPStatus.OK,
            {
                "success": True,
                "nodeCount": len(payload["nodes"]),
                "edgeSourceCount": len(payload["edges"]),
                "houseCount": len(payload["houses"]),
            },
        )

    def handle_agent_job(self, prefix, required):
        payload = read_json(self)
        require_fields(payload, required)
        db = load_db()
        job_id = payload["jobId"] or next_job_id(prefix, len(db["agentJobs"]))
        record = {"jobId": job_id, "type": prefix, "payload": payload, "acceptedAt": now_iso()}
        db["agentJobs"].append(record)
        save_db(db)
        self.send_json(HTTPStatus.OK, {"accepted": True, "jobId": job_id})

    def handle_maintenance_analyze(self):
        """
        프론트의 '재건축 추천' 분석 요청.

        프론트는 ReconstructionAnalyzeRequest 형태로 보내고
        ReconstructionRecommendation 형태의 응답을 기대한다.
            요청: { houseId, address, coordinate, parcelCode }
            응답: { houseId, recommendedUse, buildingScale, expectedCost,
                    expectedReturn, feasibility, reason }

        구버전(area 만 보내는 mock 요청) 호환을 위해 area 필드만 있는 경우에는
        기존 job-id 응답 그대로 돌려준다.
        """
        payload = read_json(self)

        # 구버전 fallback: area 만 있고 houseId 가 없으면 기존 job-id 응답
        if "houseId" not in payload and "address" not in payload:
            require_fields(payload, ["area"])
            db = load_db()
            job_id = next_job_id("MA-JOB", len(db["maintenanceJobs"]))
            record = {
                "jobId": job_id,
                "area": payload["area"],
                "status": "REQUESTED",
                "createdAt": now_iso(),
            }
            db["maintenanceJobs"].append(record)
            save_db(db)
            self.send_json(
                HTTPStatus.OK,
                {"success": True, "jobId": job_id,
                 "message": "정비 우선순위 분석을 요청했습니다."},
            )
            return

        # 신버전: 실제 agent 호출
        require_fields(payload, ["houseId", "address"])
        house_id = payload.get("houseId")
        address = payload.get("address")

        photo_b64 = resolve_photo_b64_for_house(house_id)
        sys.stderr.write(
            f"[AGENT/redevelopment] 요청: houseId={house_id}, addr={address}, "
            f"photo={'있음' if photo_b64 else '없음'}\n"
        )

        result, error = call_agent_redevelopment(house_id, address, photo_b64)
        if error:
            sys.stderr.write(f"[AGENT/redevelopment] 실패: {error}\n")
            self.send_error_json(HTTPStatus.BAD_GATEWAY, error)
            return

        mapped = map_redevelopment_to_frontend(house_id, result)
        sys.stderr.write(
            f"[AGENT/redevelopment] 응답 매핑 완료: houseId={house_id}, "
            f"recommendedUse={mapped['recommendedUse'][:40]}...\n"
        )
        self.send_json(HTTPStatus.OK, mapped)

    def handle_manual_reconstruction_request(self):
        """
        사용자가 대시보드 "직접 추천받기" 모달에서 보낸 수동 요청.

        Multipart fields:
          - address (text, required)  : 지번 주소
          - userNote (text, optional) : 사용자가 직접 적은 부가 메시지 (DB 메모로만)
          - photo (file, required)    : JPEG 등 이미지 파일

        흐름:
          1) 파일 저장 + DB 에 status=PENDING 으로 기록 → 응답으로 requestId 돌려줌
          2) 백그라운드 스레드에서 agent 호출
          3) 결과 도착하면 DB record 갱신 + WS broadcast(manual_reconstruction_result)
        """
        form = parse_multipart(self)
        address = form_text(form, "address")
        user_note = form_text(form, "userNote", required=False) or ""
        photo_item = form_file(form, "photo")

        # 파일은 uploads/manual_reconstruction/ 아래에 저장
        ts_ms = int(time.time() * 1000)
        safe_filename = safe_name(photo_item.filename)
        rel_dir = Path("uploads") / "manual_reconstruction"
        (ROOT_DIR / rel_dir).mkdir(parents=True, exist_ok=True)
        target_path = ROOT_DIR / rel_dir / f"{ts_ms}_{safe_filename}"
        with target_path.open("wb") as f:
            shutil.copyfileobj(photo_item.file, f)
        photo_url = public_path(target_path)

        # DB 저장
        db = load_db()
        request_id = f"MRR-{ts_ms}"
        record = {
            "requestId": request_id,
            "address": address,
            "userNote": user_note,
            "photoUrl": photo_url,
            "status": "PENDING",
            "createdAt": now_iso(),
            "respondedAt": None,
            "recommendation": None,
            "errorMessage": None,
        }
        db["manualReconstructionRequests"].append(record)
        save_db(db)

        # 응답: 요청 받았다는 것만 즉시 돌려주고, agent 처리는 백그라운드에서.
        self.send_json(
            HTTPStatus.OK,
            {
                "success": True,
                "requestId": request_id,
                "status": "PENDING",
                "message": "추천 요청을 받았습니다. 결과가 나오면 알려드릴게요.",
            },
        )

        # 백그라운드에서 agent 호출 (이미 응답 보낸 뒤라 시간 오래 걸려도 OK)
        threading.Thread(
            target=self._run_manual_reconstruction_agent,
            args=(request_id, address, str(target_path), photo_url),
            daemon=True,
        ).start()

    def _run_manual_reconstruction_agent(self, request_id, address, file_path, photo_url):
        """백그라운드 스레드: agent 호출 후 DB 갱신 + WS broadcast."""
        try:
            with open(file_path, "rb") as f:
                photo_b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as exc:
            self._finish_manual_reconstruction(request_id, None, f"사진 파일 읽기 실패: {exc}")
            return

        sys.stderr.write(
            f"[MANUAL_RECONSTRUCTION] 요청 {request_id}: agent 호출 시작 "
            f"(addr={address}, photo={photo_url})\n"
        )
        result, error = call_agent_redevelopment(
            house_id="",          # houseId 없음 (외부 주소 기반 요청)
            address=address,
            photo_b64=photo_b64,
        )
        if error:
            sys.stderr.write(f"[MANUAL_RECONSTRUCTION] {request_id} 실패: {error}\n")
            self._finish_manual_reconstruction(request_id, None, error)
            return

        mapped = map_redevelopment_to_frontend("", result)
        sys.stderr.write(
            f"[MANUAL_RECONSTRUCTION] {request_id} 응답 완료: "
            f"recommendedUse={mapped.get('recommendedUse', '')[:40]}\n"
        )
        self._finish_manual_reconstruction(request_id, mapped, None)

    def _finish_manual_reconstruction(self, request_id, recommendation, error_message):
        """DB 의 manualReconstructionRequests 항목 갱신 + WS 로 결과 broadcast."""
        db = load_db()
        record = None
        for item in db["manualReconstructionRequests"]:
            if item.get("requestId") == request_id:
                record = item
                break
        if record is None:
            sys.stderr.write(f"[MANUAL_RECONSTRUCTION] {request_id} 레코드 사라짐, 갱신 스킵\n")
            return

        record["status"] = "FAILED" if error_message else "DONE"
        record["respondedAt"] = now_iso()
        record["recommendation"] = recommendation
        record["errorMessage"] = error_message
        save_db(db)

        broadcast_dashboard({
            "type": "manual_reconstruction_result",
            "request_id": request_id,
            "status": record["status"],
            "address": record.get("address"),
            "user_note": record.get("userNote"),
            "photo_url": record.get("photoUrl"),
            "created_at": record.get("createdAt"),
            "responded_at": record.get("respondedAt"),
            "recommendation": recommendation,
            "error_message": error_message,
        })

    def handle_reconstruction_results(self):
        payload = read_json(self)
        required = [
            "jobId",
            "houseId",
            "recommendedUse",
            "buildingScale",
            "style",
            "estimatedCost",
            "expectedReturn",
            "feasibility",
            "reason",
            "images",
        ]
        require_fields(payload, required)
        db = load_db()
        record = dict(payload)
        record["createdAt"] = now_iso()
        db["reconstructionResults"].append(record)
        save_db(db)
        self.send_json(HTTPStatus.OK, {"success": True, "houseId": payload["houseId"]})

    def handle_anomaly_results(self):
        payload = read_json(self)
        required = [
            "jobId",
            "analysisId",
            "robotId",
            "imageId",
            "eventType",
            "severity",
            "confidence",
            "summary",
            "detectedObjects",
        ]
        require_fields(payload, required)
        db = load_db()
        image = next((item for item in db["images"] if item["imageId"] == payload["imageId"]), None)
        if image:
            image["analysisResult"] = "이상징후" if payload["severity"] in ("HIGH", "CRITICAL") else "정상"
            image["analysisSummary"] = payload["summary"]
        event_id = f"EV-{len(db['events']) + 1:04d}"
        event = {
            "eventId": event_id,
            "jobId": payload["jobId"],
            "analysisId": payload["analysisId"],
            "robotId": payload["robotId"],
            "imageId": payload["imageId"],
            "eventType": payload["eventType"],
            "severity": payload["severity"],
            "title": payload["summary"],
            "location": image.get("address") if image else None,
            "confidence": payload["confidence"],
            "summary": payload["summary"],
            "detectedObjects": payload["detectedObjects"],
            "recommendedActions": (
                payload.get("recommended_actions")
                or payload.get("recommendedActions")
                or []
            ),
            "imageUrl": image["imageUrl"] if image else None,
            "resolved": False,
            "createdAt": now_iso(),
        }
        db["events"].append(event)
        save_db(db)
        severity = str(payload["severity"]).lower()
        broadcast_dashboard({
            "type": "anomaly_result",
            "robot_id": payload["robotId"],
            "house_id": image.get("houseId") if image else None,
            "image_id": payload["imageId"],
            "image_url": image["imageUrl"] if image else None,
            "is_anomaly": payload["severity"] in ("HIGH", "CRITICAL", "MEDIUM"),
            "risk_level": "high" if severity == "critical" else severity,
            "summary": payload["summary"],
            "evidence": payload["detectedObjects"],
            "recommended_actions": event["recommendedActions"],
            "event_id": event_id,
        })
        self.send_json(HTTPStatus.OK, {"success": True, "eventId": event_id})

    def handle_maintenance_results(self):
        payload = read_json(self)
        require_fields(payload, ["jobId", "analysisId", "recommendations"])
        if not isinstance(payload["recommendations"], list):
            raise ValueError("recommendations must be an array")
        db = load_db()
        saved = []
        for index, item in enumerate(payload["recommendations"], start=1):
            recommendation = dict(item)
            recommendation.setdefault("rank", index)
            recommendation["analysisId"] = payload["analysisId"]
            recommendation["jobId"] = payload["jobId"]
            recommendation["createdAt"] = now_iso()
            saved.append(recommendation)
        db["maintenancePriorities"] = saved
        save_db(db)
        self.send_json(
            HTTPStatus.OK,
            {"success": True, "analysisId": payload["analysisId"], "savedCount": len(saved)},
        )

    def handle_event_resolve(self, event_id):
        payload = read_json(self)
        require_fields(payload, ["memo"])
        db = load_db()
        event = next((item for item in db["events"] if item["eventId"] == event_id), None)
        if event is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "event not found")
            return
        event["resolved"] = True
        event["resolvedMemo"] = payload["memo"]
        event["resolvedAt"] = now_iso()
        save_db(db)
        self.send_json(HTTPStatus.OK, {"success": True, "eventId": event_id, "resolved": True})

    def handle_detection_acknowledge(self, detection_id):
        db = load_db()
        detection = next(
            (item for item in db["missingDetections"] if item["detectionId"] == detection_id), None
        )
        if detection is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "detection not found")
            return
        detection["reviewRequested"] = True
        detection["reviewRequestedAt"] = now_iso()
        save_db(db)
        self.send_json(
            HTTPStatus.OK,
            {"success": True, "detectionId": detection_id, "reviewRequested": True},
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    ensure_dirs()
    server = ThreadingHTTPServer((args.host, args.port), RobotBackendHandler)
    print(f"Robot backend listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
