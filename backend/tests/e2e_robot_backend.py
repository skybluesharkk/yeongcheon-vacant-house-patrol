#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import requests
import websocket
from PIL import Image
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[1]
SERVER = ROOT_DIR / "backend" / "server.py"


def wait_for_health(base_url, process):
    deadline = time.time() + 10
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with code {process.returncode}")
        try:
            response = requests.get(f"{base_url}/health", timeout=0.5)
            if response.status_code == 200 and response.json().get("ok"):
                return
        except requests.RequestException:
            time.sleep(0.1)
    raise TimeoutError("server did not become healthy")


def assert_success(response):
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise AssertionError(response.text) from exc
    assert response.status_code == 200, payload
    assert payload.get("success", True) is not False, payload
    return payload


def run(base_url):
    robot_id = "robot-e2e-01"
    timestamp = "2026-05-20T14:35:22"
    parsed = urlparse(base_url)
    ws_base_url = f"ws://{parsed.netloc}"

    status = assert_success(
        requests.post(
            f"{base_url}/api/robots/{robot_id}/status",
            json={
                "status": "PATROLLING",
                "battery": 78,
                "x": 12.4,
                "y": 8.7,
                "address": "완산동 123-4",
                "nextDestination": "완산동 125-6",
                "velocity": 0.8,
                "timestamp": timestamp,
            },
            timeout=3,
        )
    )
    assert status["robotId"] == robot_id

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        image_path = tmp_dir / "frame_001.jpg"
        Image.new("RGB", (64, 48), (20, 120, 180)).save(image_path)
        with image_path.open("rb") as image_file:
            image = assert_success(
                requests.post(
                    f"{base_url}/api/robots/{robot_id}/image",
                    files={"image": ("frame_001.jpg", image_file, "image/jpeg")},
                    data={"x": "12.4", "y": "8.7", "timestamp": timestamp},
                    timeout=3,
                )
            )
        assert image["robotId"] == robot_id
        assert image["imageUrl"].endswith(".jpg")
        assert image["analysisJobId"].startswith("AN-JOB-")

        angles = np.linspace(-np.pi, np.pi, 256, endpoint=False)
        distances = 4.0 + np.sin(angles) * 0.5
        distances[120:124] = 0.9
        lidar_path = tmp_dir / "scan_001.npy"
        np.save(lidar_path, distances)
        with lidar_path.open("rb") as lidar_file:
            lidar = assert_success(
                requests.post(
                    f"{base_url}/api/robots/{robot_id}/lidar",
                    files={"lidarFile": ("scan_001.npy", lidar_file, "application/octet-stream")},
                    data={"x": "12.4", "y": "8.7", "timestamp": timestamp},
                    timeout=3,
                )
            )
        assert lidar["robotId"] == robot_id
        assert lidar["summary"]["pointCount"] == 256
        assert lidar["summary"]["obstacleDetected"] is True
        assert lidar["previewImageUrl"].endswith(".png")

        missing_path = tmp_dir / "P-001_detected.jpg"
        Image.new("RGB", (80, 60), (180, 80, 40)).save(missing_path)
        with missing_path.open("rb") as missing_file:
            missing = assert_success(
                requests.post(
                    f"{base_url}/api/robots/{robot_id}/missing-person",
                    files={"image": ("P-001_detected.jpg", missing_file, "image/jpeg")},
                    data={"missing_person_id": "P-001", "x": "5.12", "y": "-3.45", "timestamp": timestamp},
                    timeout=3,
                )
            )
        assert missing["robotId"] == robot_id
        assert missing["missingPersonId"] == "P-001"
        assert missing["eventId"].startswith("EV-")

    detail = requests.get(f"{base_url}/api/dashboard/robots/{robot_id}", timeout=3)
    assert detail.status_code == 200, detail.text
    detail_json = detail.json()
    assert detail_json["robotId"] == robot_id
    assert detail_json["latestImage"]["imageUrl"] == image["imageUrl"]
    assert detail_json["latestLidar"]["previewImageUrl"] == lidar["previewImageUrl"]
    assert len(detail_json["patrolPath"]) >= 3

    dashboard = requests.get(f"{base_url}/api/dashboard?selectedRobotId={robot_id}", timeout=3)
    assert dashboard.status_code == 200, dashboard.text
    dashboard_json = dashboard.json()
    assert dashboard_json["selectedRobotId"] == robot_id
    assert any(robot["robotId"] == robot_id for robot in dashboard_json["robots"])
    assert "stats" in dashboard_json

    graph_payload = {
        "type": "graph",
        "nodes": {"N0": [8.9, 8.5], "N1": [8.3, 8.2]},
        "edges": {"N0": ["N1"], "N1": ["N0"]},
        "houses": {"H1": {"pos": [8.65, -0.42], "yaw": -1.184}},
    }
    graph_result = assert_success(requests.post(f"{base_url}/api/graph", json=graph_payload, timeout=3))
    assert graph_result["nodeCount"] == 2
    graph = requests.get(f"{base_url}/api/graph", timeout=3)
    assert graph.status_code == 200, graph.text
    assert graph.json()["houses"]["H1"]["yaw"] == -1.184

    robot_ws = websocket.create_connection(f"{ws_base_url}/ws/robot", timeout=3)
    try:
        robot_ws.send(
            json.dumps(
                {
                    "type": "plan",
                    "mission_id": "1715485800.123",
                    "start": [9.07, 7.66],
                    "house_order": ["H1"],
                    "waypoints": [[9.07, 7.66], [8.65, -0.42]],
                    "arrival_indices": [1],
                    "arrival_yaws": [-1.184],
                }
            )
        )
        robot_ws.send(
            json.dumps(
                {
                    "type": "pos",
                    "mission_id": "1715485800.123",
                    "x": 8.92,
                    "y": 7.1,
                    "yaw": -1.823,
                    "t": 1715485812.045,
                }
            )
        )
    finally:
        robot_ws.close()

    dashboard_ws = websocket.create_connection(f"{ws_base_url}/ws/dashboard", timeout=3)
    try:
        ws_messages = []
        deadline = time.time() + 3
        while time.time() < deadline and not {"plan", "pos"}.issubset({message.get("type") for message in ws_messages}):
            ws_messages.append(json.loads(dashboard_ws.recv()))
        assert any(message["type"] == "plan" for message in ws_messages)
        assert any(message["type"] == "pos" for message in ws_messages)
    finally:
        dashboard_ws.close()

    anomaly = assert_success(
        requests.post(
            f"{base_url}/api/agent/anomaly-results",
            json={
                "jobId": image["analysisJobId"],
                "analysisId": "AN-0001",
                "robotId": robot_id,
                "imageId": image["imageId"],
                "eventType": "BROKEN_WINDOW",
                "severity": "HIGH",
                "confidence": 0.92,
                "summary": "창문 파손 의심",
                "detectedObjects": ["window", "glass"],
            },
            timeout=3,
        )
    )
    assert anomaly["eventId"].startswith("EV-")

    events = requests.get(f"{base_url}/api/events?robotId={robot_id}&resolved=false&limit=10", timeout=3)
    assert events.status_code == 200, events.text
    events_json = events.json()
    assert any(event["eventId"] == anomaly["eventId"] for event in events_json)

    resolved = assert_success(
        requests.post(
            f"{base_url}/api/events/{anomaly['eventId']}/resolve",
            json={"memo": "현장 확인 완료"},
            timeout=3,
        )
    )
    assert resolved["resolved"] is True

    maintenance_run = assert_success(
        requests.post(f"{base_url}/api/maintenance/analyze", json={"area": "완산동"}, timeout=3)
    )
    assert maintenance_run["jobId"].startswith("MA-JOB-")

    maintenance_result = assert_success(
        requests.post(
            f"{base_url}/api/agent/maintenance-results",
            json={
                "jobId": maintenance_run["jobId"],
                "analysisId": "MA-AN-0001",
                "recommendations": [
                    {
                        "houseId": "VH-001",
                        "address": "완산동 123-4",
                        "riskLevel": "HIGH",
                        "agingRate": 0.82,
                        "accessibility": "GOOD",
                        "score": 91.5,
                        "recommendedUse": "공공임대",
                        "reason": "위험도와 접근성 기준 우선 정비 대상",
                    }
                ],
            },
            timeout=3,
        )
    )
    assert maintenance_result["savedCount"] == 1

    maintenance = requests.get(f"{base_url}/api/maintenance", timeout=3)
    assert maintenance.status_code == 200, maintenance.text
    assert maintenance.json()[0]["houseId"] == "VH-001"

    reconstruction = assert_success(
        requests.post(
            f"{base_url}/api/agent/reconstruction-results",
            json={
                "jobId": "RC-JOB-0001",
                "houseId": "VH-001",
                "recommendedUse": "청년 창업 공간",
                "buildingScale": "2F",
                "style": "modern",
                "estimatedCost": 250000000,
                "expectedReturn": "medium",
                "feasibility": "HIGH",
                "reason": "상권 접근성 양호",
                "images": [image["imageUrl"]],
            },
            timeout=3,
        )
    )
    assert reconstruction["houseId"] == "VH-001"

    for path, payload, key in [
        (
            "/agent/maintenance/analyze",
            {
                "jobId": "EXT-MA-0001",
                "callbackUrl": "/api/agent/maintenance-results",
                "area": "완산동",
                "vacantHouses": [],
                "populationData": {},
                "recentEvents": [],
            },
            "EXT-MA-0001",
        ),
        (
            "/agent/anomaly/analyze-image",
            {
                "jobId": "EXT-AN-0001",
                "callbackUrl": "/api/agent/anomaly-results",
                "robotId": robot_id,
                "imageId": image["imageId"],
                "imageUrl": image["imageUrl"],
                "x": 12.4,
                "y": 8.7,
                "address": "완산동 123-4",
                "timestamp": timestamp,
            },
            "EXT-AN-0001",
        ),
        (
            "/agent/reconstruction/recommend",
            {
                "jobId": "EXT-RC-0001",
                "callbackUrl": "/api/agent/reconstruction-results",
                "houseId": "VH-001",
                "address": "완산동 123-4",
                "riskLevel": "HIGH",
                "agingRate": 0.82,
                "accessibility": "GOOD",
                "populationContext": {},
                "beforeImageUrl": image["imageUrl"],
            },
            "EXT-RC-0001",
        ),
    ]:
        accepted = requests.post(f"{base_url}{path}", json=payload, timeout=3)
        assert accepted.status_code == 200, accepted.text
        accepted_json = accepted.json()
        assert accepted_json["accepted"] is True
        assert accepted_json["jobId"] == key

    page = requests.get(f"{base_url}/", timeout=3)
    assert page.status_code == 200, page.text
    assert "Robot Upload Dashboard" in page.text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    if args.base_url:
        run(args.base_url.rstrip("/"))
        print("Robot->Backend e2e passed")
        return

    base_url = f"http://127.0.0.1:{args.port}"
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for_health(base_url, process)
        run(base_url)
        print("Robot->Backend e2e passed")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
