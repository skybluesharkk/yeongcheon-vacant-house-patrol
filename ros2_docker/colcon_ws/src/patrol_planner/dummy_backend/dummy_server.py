import asyncio
import os
import json
from pathlib import Path
from typing import Dict, Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# 정적 파일 마운트 (업로드된 이미지 제공)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# 메모리 상의 최신 상태 캐시
latest_state: Dict[str, Any] = {
    "graph": None,
    "plan": None,
    "pos": None,
    "status": None
}

dashboard_clients: Set[WebSocket] = set()
robot_clients: Set[WebSocket] = set()

async def broadcast_to_dashboards(msg: dict):
    """대시보드에 연결된 모든 클라이언트에게 메시지 전송"""
    dead_clients = set()
    for client in dashboard_clients:
        try:
            await client.send_json(msg)
        except Exception:
            dead_clients.add(client)
    for c in dead_clients:
        dashboard_clients.discard(c)


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = BASE_DIR / "index.html"
    if index_file.exists():
        return index_file.read_text(encoding="utf-8")
    return "<h1>index.html not found!</h1>"


@app.get("/api/graph")
async def get_graph():
    return latest_state["graph"] or {"type": "graph", "nodes": {}, "edges": {}, "houses": {}}


@app.post("/api/graph")
async def post_graph(data: dict):
    latest_state["graph"] = data
    print(f"✅ 도로 그래프 업데이트 (노드: {len(data.get('nodes', {}))}개)")
    await broadcast_to_dashboards(data)
    return {"success": True}


@app.post("/api/robots/{robot_id}/image")
async def receive_vacant_house_image(
    robot_id: str,
    image: UploadFile,
    x: str = Form(...),
    y: str = Form(...),
    timestamp: str = Form(...)
):
    photo_bytes = await image.read()
    file_name = image.filename or f"house_{timestamp}.jpg"
    file_path = UPLOADS_DIR / file_name
    file_path.write_bytes(photo_bytes)

    print(f"📸 빈집 사진 수신 [{robot_id}]: {file_name} (x={x}, y={y})")
    
    # 대시보드에 알림 브로드캐스트
    msg = {
        "type": "photo_captured",
        "robot_id": robot_id,
        "url": f"/uploads/{file_name}",
        "x": x,
        "y": y,
        "timestamp": timestamp
    }
    await broadcast_to_dashboards(msg)

    return {
        "success": True,
        "robotId": robot_id,
        "imageUrl": f"/uploads/{file_name}"
    }


@app.post("/api/robots/{robot_id}/missing-person")
async def receive_missing_person(
    robot_id: str,
    image: UploadFile,
    missing_person_id: str = Form(...),
    x: str = Form(...),
    y: str = Form(...),
    timestamp: str = Form(...)
):
    photo_bytes = await image.read()
    file_name = f"{missing_person_id}_{timestamp.replace(':', '-')}.jpg"
    file_path = UPLOADS_DIR / file_name
    file_path.write_bytes(photo_bytes)

    print(f"🚨 실종자 탐지 [{robot_id}]: {missing_person_id} (x={x}, y={y})")
    
    # 대시보드에 알림 브로드캐스트
    msg = {
        "type": "missing_person_detected",
        "robot_id": robot_id,
        "missing_person_id": missing_person_id,
        "url": f"/uploads/{file_name}",
        "x": x,
        "y": y,
        "timestamp": timestamp
    }
    await broadcast_to_dashboards(msg)

    return {"success": True}


@app.websocket("/ws/robot")
async def ws_robot(ws: WebSocket):
    await ws.accept()
    robot_clients.add(ws)
    print("🤖 로봇 웹소켓 연결됨!")
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            
            # 비디오 프레임은 상태 캐싱에서 제외 (메모리 최적화)
            if mtype != "video_frame" and mtype in latest_state:
                latest_state[mtype] = msg
            
            # 로봇에서 온 메시지를 그대로 대시보드로 중계
            await broadcast_to_dashboards(msg)

    except WebSocketDisconnect:
        robot_clients.discard(ws)
        print("🤖 로봇 웹소켓 연결 끊어짐.")


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    dashboard_clients.add(ws)
    print("💻 대시보드 웹소켓 접속됨.")
    
    # 접속 즉시 캐싱된 최신 상태 전송
    try:
        for k in ("graph", "plan", "pos", "status"):
            if latest_state[k]:
                await ws.send_json(latest_state[k])
                
        while True:
            # 대시보드에서 보낸 메시지(ex. start_mission)를 로봇으로 전달
            data = await ws.receive_text()
            try:
                cmd = json.loads(data)
                dead_robots = set()
                for r_ws in robot_clients:
                    try:
                        await r_ws.send_text(data)
                    except Exception:
                        dead_robots.add(r_ws)
                for r in dead_robots:
                    robot_clients.discard(r)
                print(f"📡 대시보드 명령 전달됨: {cmd.get('type')}")
            except Exception as e:
                print(f"명령 파싱 에러: {e}")
                
    except WebSocketDisconnect:
        dashboard_clients.discard(ws)
        print("💻 대시보드 연결 끊어짐.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dummy_server:app", host="0.0.0.0", port=8000, reload=True)
