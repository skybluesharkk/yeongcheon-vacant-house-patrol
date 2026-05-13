# -*- coding: utf-8 -*-
"""
patrol_node: 영천 빈집 순찰 메인 ROS2 노드

흐름:
    1) /house_request 토픽으로 들어온 빈집 ID 리스트(쉼표 구분) 또는
       시작 시 자동으로 더미 빈집 리스트를 받는다.
    2) tf로 차량의 현재 위치(odom -> base_footprint)를 얻는다.
    3) planner.compute_full_path 로 TSP+A* 전체 경로 계산
       (각 빈집마다 도착 인덱스 + 목표 yaw 도 함께 받아둠).
    4) PathFollower 가 한 점씩 따라가며 /cmd_vel 발행.
    5) 빈집 도착 지점에 들어오면 상태머신이 다음 단계를 수행:
        FOLLOWING → ROTATING(목표 yaw로 회전) → CAPTURING(/house_arrival 발행 +
        카메라 이미지 캡처 + 백엔드 POST + pause_at_house 만큼 정지) → FOLLOWING
    6) 모든 빈집 방문 후 시작 지점으로 복귀하고 종료.

부분 탐지:
    /house_request 토픽에 ID 부분집합을 보내거나, DEFAULT_HOUSE_IDS 를 수정.
    HOUSES 카탈로그 자체를 건드릴 필요 없음.
"""

import io
import math
import threading
import time
import json
try:
    import websocket
except ImportError:
    websocket = None
try:
    import requests
except ImportError:
    requests = None
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Image
from std_msgs.msg import String

import tf2_ros
from tf2_ros import LookupException, ConnectivityException, ExtrapolationException

from .planner import compute_full_path
from .graph import HOUSES, get_graph_json
from .path_follower import PathFollower


# 차량이 시작 시 자동으로 방문할 더미 빈집 ID (테스트용).
# 부분 탐지 시 이 리스트만 수정하거나, /house_request 토픽을 사용한다.
DEFAULT_HOUSE_IDS = ["H1", "H3", "H5", "H2"]


# 상태머신 상태값
ST_FOLLOWING = "FOLLOWING"   # 일반 웨이포인트 추종 중
ST_ROTATING  = "ROTATING"    # 빈집 도착 후 목표 yaw 로 회전 중
ST_CAPTURING = "CAPTURING"   # /house_arrival 발행 + 사진 + POST + 정지 대기


def yaw_from_quaternion(qx, qy, qz, qw):
    """쿼터니언 -> yaw (rad) 변환 (z축 회전만 추출)."""
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def normalize_angle(angle):
    """[-pi, pi] 범위로 정규화."""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def ros_image_to_jpeg(img_msg, quality=85):
    """sensor_msgs/Image → JPEG bytes. PIL 사용."""
    # PIL 은 ROS 외부 라이브러리라 여기서만 import 해서 미설치 시 에러를 한 곳으로 모음.
    from PIL import Image as PILImage

    w, h = img_msg.width, img_msg.height
    enc = img_msg.encoding
    data = bytes(img_msg.data)

    if enc in ("rgb8", "bgr8"):
        img = PILImage.frombytes("RGB", (w, h), data)
        if enc == "bgr8":
            b, g, r = img.split()
            img = PILImage.merge("RGB", (r, g, b))
    elif enc in ("rgba8", "bgra8"):
        img = PILImage.frombytes("RGBA", (w, h), data)
        if enc == "bgra8":
            b, g, r, a = img.split()
            img = PILImage.merge("RGBA", (r, g, b, a))
        img = img.convert("RGB")
    elif enc == "mono8":
        img = PILImage.frombytes("L", (w, h), data).convert("RGB")
    else:
        raise ValueError(f"지원하지 않는 이미지 인코딩: {enc}")

    buf = io.BytesIO()
    # Unity RenderTexture는 OpenGL 좌표계라 Y축이 뒤집혀 있음
    from PIL import ImageOps
    img = ImageOps.flip(img)
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class PatrolNode(Node):
    def __init__(self):
        super().__init__("patrol_node")

        # ---------------- 파라미터 ----------------
        # 주의: 월드맵이 0.1배로 다운스케일돼 있어 odom 좌표가 작다.
        # 따라서 속도/도착 경계값도 그 스케일에 맞춘 기본값을 쓴다.
        self.declare_parameter("linear_speed", 1.0)
        self.declare_parameter("angular_speed", 0.8)
        self.declare_parameter("yaw_tolerance", 0.3)
        self.declare_parameter("arrival_tolerance", 0.5)
        self.declare_parameter("pause_at_house", 2.0)
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("control_rate", 20.0)
        self.declare_parameter("auto_start", True)

        # 빈집 도착 시 yaw 정렬 허용 오차 (rad). 일반 주행 yaw_tolerance 보다 빡빡.
        self.declare_parameter("house_yaw_tolerance", 0.1)

        # 카메라 이미지 토픽 (Unity 측 publisher 토픽명에 맞춰 변경 가능)
        self.declare_parameter("camera_topic", "/camera/image_raw")

        # 백엔드 베이스 URL (스킴 + 호스트). 비어있으면 POST 스킵 (개발 편의).
        # 실제 엔드포인트는 {backend_url}/api/robots/{robot_id}/image
        self.declare_parameter("backend_url", "")

        # 로봇 식별자. 백엔드는 robot_id 별로 이미지를 관리한다.
        self.declare_parameter("robot_id", "robot-01")

        # POST timeout (s)
        self.declare_parameter("post_timeout", 5.0)

        self.linear_speed = self.get_parameter("linear_speed").value
        self.angular_speed = self.get_parameter("angular_speed").value
        self.yaw_tolerance = self.get_parameter("yaw_tolerance").value
        self.arrival_tolerance = self.get_parameter("arrival_tolerance").value
        self.pause_at_house = self.get_parameter("pause_at_house").value
        self.odom_frame = self.get_parameter("odom_frame").value
        self.base_frame = self.get_parameter("base_frame").value
        self.control_rate = self.get_parameter("control_rate").value
        self.auto_start = self.get_parameter("auto_start").value
        self.house_yaw_tolerance = self.get_parameter("house_yaw_tolerance").value
        self.camera_topic = self.get_parameter("camera_topic").value
        self.backend_url = self.get_parameter("backend_url").value.rstrip("/")
        self.robot_id = self.get_parameter("robot_id").value
        self.post_timeout = float(self.get_parameter("post_timeout").value)

        # ---------------- ROS 인터페이스 ----------------
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.arrival_pub = self.create_publisher(String, "/house_arrival", 10)
        self.request_pub = self.create_publisher(String, "/house_request", 10)
        self.request_sub = self.create_subscription(
            String, "/house_request", self._on_house_request, 10
        )
        self.image_sub = self.create_subscription(
            Image, self.camera_topic, self._on_image, 1
        )

        # tf2 listener
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # ---------------- 내부 상태 ----------------
        self.follower = PathFollower(
            linear_speed=self.linear_speed,
            angular_speed=self.angular_speed,
            yaw_tolerance=self.yaw_tolerance,
            arrival_tolerance=self.arrival_tolerance,
        )

        # 현재 미션 정보
        self._house_order = []          # TSP 결과 방문 순서
        self._arrival_indices = []      # 각 빈집의 도착 waypoint index
        self._arrival_yaws = []         # 각 빈집의 목표 yaw (rad)
        self._arrival_idx_set = set()   # O(1) lookup 용
        self._next_arrival_pos = 0
        self._mission_active = False

        # 상태머신
        self._state = ST_FOLLOWING
        self._current_house = None
        self._current_target_yaw = 0.0
        self._paused_until = 0.0        # 빈집에서 일시정지 끝나는 시각

        # 기타
        self._tf_warned = False
        self._latest_image = None       # 최신 sensor_msgs/Image (캡처용 캐시)

        # 실시간 모니터링 (Phase 2) 관련
        self._ws_app = None
        self._ws_thread = None
        self._ws_connected = False
        self._pos_send_counter = 0
        self._current_mission_id = None
        
        self._init_backend_connection()

        # 컨트롤 타이머
        period = 1.0 / max(self.control_rate, 1.0)
        self.timer = self.create_timer(period, self._on_timer)

        # 실시간 영상 스트림 (Phase 3 Extension) 2Hz
        self.create_timer(0.5, self._on_video_timer)

        # 자동 시작
        if self.auto_start:
            self.create_timer(1.0, self._auto_start_once)

        self.get_logger().info(
            "patrol_node 시작됨 | "
            f"linear={self.linear_speed}, angular={self.angular_speed}, "
            f"yaw_tol={self.yaw_tolerance}, arr_tol={self.arrival_tolerance}, "
            f"pause={self.pause_at_house}s, house_yaw_tol={self.house_yaw_tolerance} | "
            f"camera={self.camera_topic} | robot_id={self.robot_id} | "
            f"backend={'(미설정)' if not self.backend_url else self.backend_url}"
        )

    # ------------------------------------------------------------------
    # 백엔드 연동 (Phase 2)
    # ------------------------------------------------------------------
    def _init_backend_connection(self):
        if not self.backend_url:
            return
        # 1. Graph POST
        threading.Thread(target=self._post_graph, daemon=True).start()
        # 2. WebSocket 연결
        if websocket is not None:
            self._connect_ws()
        else:
            self.get_logger().warn("websocket-client 라이브러리가 없어 실시간 전송을 건너뜁니다.")

    def _post_graph(self):
        if requests is None:
            return
        url = f"{self.backend_url}/api/graph"
        try:
            r = requests.post(url, json=get_graph_json(), timeout=5.0)
            self.get_logger().info(f"[GRAPH] 도로 그래프 전송 완료: {r.status_code}")
        except Exception as e:
            self.get_logger().error(f"[GRAPH] 도로 그래프 전송 실패: {e}")

    def _connect_ws(self):
        ws_url = self.backend_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/robot"

        def on_open(ws):
            self.get_logger().info(f"[WS] 백엔드 실시간 소켓 연결됨: {ws_url}")
            self._ws_connected = True

        def on_close(ws, close_status_code, close_msg):
            self.get_logger().warn("[WS] 백엔드 연결 끊어짐. 3초 후 재연결 시도...")
            self._ws_connected = False
            time.sleep(3.0)
            self._connect_ws()

        def on_error(ws, error):
            pass

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("type") == "start_mission":
                    houses = data.get("houses", [])
                    if houses:
                        msg = String()
                        msg.data = ",".join(houses)
                        self.get_logger().info(f"[WS] 백엔드로부터 순찰 명령 수신: {msg.data}")
                        self.request_pub.publish(msg)
            except Exception as e:
                self.get_logger().error(f"[WS] 메시지 파싱 에러: {e}")

        self._ws_app = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message,
        )
        self._ws_thread = threading.Thread(target=self._ws_app.run_forever, daemon=True)
        self._ws_thread.start()

    def _send_ws(self, msg_dict):
        if self._ws_connected and self._ws_app:
            try:
                self._ws_app.send(json.dumps(msg_dict))
            except Exception:
                pass

    def _send_status(self, phase, house_id=None, house_index=None):
        if not self._current_mission_id:
            return
        msg = {
            "type": "status",
            "mission_id": self._current_mission_id,
            "phase": phase,
            "t": time.time()
        }
        if house_id:
            msg["house_id"] = house_id
        if house_index is not None:
            msg["house_index"] = house_index
        self._send_ws(msg)

    # ------------------------------------------------------------------
    # 자동 시작
    # ------------------------------------------------------------------
    def _auto_start_once(self):
        """tf 가 올라온 뒤 처음 미션을 한 번만 시작."""
        if not self.auto_start or self._mission_active:
            return
        self.get_logger().info(
            f"[AUTO-START] 더미 빈집 방문 시도: {DEFAULT_HOUSE_IDS}"
        )
        self._start_mission(DEFAULT_HOUSE_IDS)
        if self._mission_active:
            self.auto_start = False

    # ------------------------------------------------------------------
    # 비디오 스트림 전송
    # ------------------------------------------------------------------
    def _on_video_timer(self):
        if not self._ws_connected or not self._latest_image:
            return
            
        try:
            from PIL import Image
            import base64
            img_msg = self._latest_image
            enc = img_msg.encoding.lower()
            
            # 지원하는 인코딩 타입으로 PIL 이미지 생성
            if enc in ["rgb8", "bgr8"]:
                img = Image.frombytes("RGB", (img_msg.width, img_msg.height), bytes(img_msg.data))
                if enc == "bgr8":
                    b, g, r = img.split()
                    img = Image.merge("RGB", (r, g, b))
            elif enc in ["rgba8", "bgra8"]:
                img = Image.frombytes("RGBA", (img_msg.width, img_msg.height), bytes(img_msg.data)).convert("RGB")
                if enc == "bgra8":
                    b, g, r = img.split()
                    img = Image.merge("RGB", (r, g, b))
            else:
                # 기타 인코딩: raw bytes로 RGB 시도
                img = Image.frombytes("RGB", (img_msg.width, img_msg.height), bytes(img_msg.data[:img_msg.width * img_msg.height * 3]))
            
            img.thumbnail((320, 240))
            # Unity RenderTexture는 Y축이 뒤집혀 있으므로 반전
            from PIL import ImageOps
            img = ImageOps.flip(img)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=40)
            b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
            
            self._send_ws({
                "type": "video_frame",
                "data": f"data:image/jpeg;base64,{b64_str}"
            })
        except Exception as e:
            self.get_logger().debug(f"[VIDEO] 프레임 인코딩 실패: {e}")

    # ------------------------------------------------------------------
    # 콜백: 빈집 방문 요청
    # ------------------------------------------------------------------
    def _on_house_request(self, msg: String):
        raw = msg.data.strip()
        if not raw:
            self.get_logger().warn("/house_request: 빈 문자열이 들어옴")
            return
        ids = [s.strip() for s in raw.split(",") if s.strip()]
        unknown = [h for h in ids if h not in HOUSES]
        if unknown:
            self.get_logger().warn(f"알 수 없는 빈집 ID 무시: {unknown}")
        ids = [h for h in ids if h in HOUSES]
        if not ids:
            self.get_logger().error("유효한 빈집 ID가 없어서 미션 시작 불가")
            return
        self.get_logger().info(f"[REQUEST] 빈집 방문 요청: {ids}")
        self._start_mission(ids)

    # ------------------------------------------------------------------
    # 콜백: 카메라 이미지 (최신 1장만 캐시)
    # ------------------------------------------------------------------
    def _on_image(self, msg: Image):
        if self._latest_image is None:
            self.get_logger().info(
                f"[CAM] 첫 번째 카메라 이미지 수신! encoding={msg.encoding}, "
                f"size={msg.width}x{msg.height}, data_len={len(msg.data)}"
            )
            # 디버그: 첫 번째 이미지를 파일로 저장해서 내용 확인
            try:
                from PIL import Image as PILImage
                enc = msg.encoding.lower()
                if enc in ["rgb8", "bgr8"]:
                    img = PILImage.frombytes("RGB", (msg.width, msg.height), bytes(msg.data))
                    if enc == "bgr8":
                        b, g, r = img.split()
                        img = PILImage.merge("RGB", (r, g, b))
                elif enc in ["rgba8", "bgra8"]:
                    img = PILImage.frombytes("RGBA", (msg.width, msg.height), bytes(msg.data)).convert("RGB")
                else:
                    img = PILImage.frombytes("RGB", (msg.width, msg.height), bytes(msg.data[:msg.width * msg.height * 3]))
                img.save("/tmp/debug_first_frame.jpg")
                self.get_logger().info("[CAM] 디버그 이미지 저장됨: /tmp/debug_first_frame.jpg")
            except Exception as e:
                self.get_logger().error(f"[CAM] 디버그 이미지 저장 실패: {e}")
        self._latest_image = msg

    # ------------------------------------------------------------------
    # 미션 시작
    # ------------------------------------------------------------------
    def _start_mission(self, house_ids):
        pose = self._get_current_pose()
        if pose is None:
            self.get_logger().warn(
                "tf 변환을 아직 얻을 수 없어 미션 시작 보류. 다음 주기에 재시도."
            )
            return

        x, y, _ = pose
        plan = compute_full_path((x, y), house_ids, return_to_start=True)
        waypoints = plan["waypoints"]
        self._house_order = plan["house_order"]
        self._arrival_indices = plan["arrival_indices"]
        self._arrival_yaws = plan["arrival_yaws"]
        self._arrival_idx_set = set(self._arrival_indices)
        self._next_arrival_pos = 0

        if len(waypoints) < 2:
            self.get_logger().error("경로 계산 결과가 비어있음. 미션 중단.")
            return

        self.follower.set_path(waypoints)
        self._mission_active = True
        self._state = ST_FOLLOWING
        self._current_house = None
        self._paused_until = 0.0

        self._current_mission_id = str(time.time())

        # [Phase 2] plan 웹소켓 전송
        plan_msg = {
            "type": "plan",
            "mission_id": self._current_mission_id,
            "start": [x, y],
            "house_order": self._house_order,
            "waypoints": waypoints,
            "arrival_indices": self._arrival_indices,
            "arrival_yaws": self._arrival_yaws
        }
        self._send_ws(plan_msg)
        
        # [Phase 2] 상태 전이 전송
        self._send_status("mission_started")

        self.get_logger().info(
            f"[PLAN] 시작좌표=({x:.2f},{y:.2f}) | "
            f"방문순서={self._house_order} | "
            f"yaws={[round(y,2) for y in self._arrival_yaws]} | "
            f"waypoints={len(waypoints)}개 | "
            f"arrival_indices={self._arrival_indices}"
        )

    # ------------------------------------------------------------------
    # tf로 현재 위치/자세 얻기
    # ------------------------------------------------------------------
    def _get_current_pose(self):
        try:
            tf = self.tf_buffer.lookup_transform(
                self.odom_frame,
                self.base_frame,
                rclpy.time.Time(),
            )
        except (LookupException, ConnectivityException, ExtrapolationException) as e:
            if not self._tf_warned:
                self.get_logger().warn(
                    f"tf 조회 실패 ({self.odom_frame} -> {self.base_frame}): {e}"
                )
                self._tf_warned = True
            return None
        except Exception as e:
            self.get_logger().error(f"tf 조회 중 예외: {e}")
            return None

        t = tf.transform.translation
        q = tf.transform.rotation
        yaw = yaw_from_quaternion(q.x, q.y, q.z, q.w)
        self._tf_warned = False
        return (t.x, t.y, yaw)

    # ------------------------------------------------------------------
    # 주기적 제어 루프 (상태머신 디스패처)
    # ------------------------------------------------------------------
    def _on_timer(self):
        if not self._mission_active:
            return

        pose = self._get_current_pose()
        if pose is None:
            self._publish_stop()
            return
        cx, cy, cyaw = pose

        if self._state == ST_CAPTURING:
            self._tick_capturing(cx, cy, cyaw)
            return
        if self._state == ST_ROTATING:
            self._tick_rotating(cx, cy, cyaw)
            return

        # 기본: FOLLOWING
        self._tick_following(cx, cy, cyaw)

    # ------------------------------------------------------------------
    # FOLLOWING: 일반 웨이포인트 추종
    # ------------------------------------------------------------------
    def _tick_following(self, cx, cy, cyaw):
        cmd = self.follower.compute_cmd(cx, cy, cyaw)

        target = cmd["target"]
        if target is not None:
            self.get_logger().debug(
                f"[CTRL] pos=({cx:.2f},{cy:.2f}) yaw={cyaw:.2f} "
                f"target=({target[0]:.2f},{target[1]:.2f}) "
                f"dist={cmd['distance']:.2f} yaw_err={cmd['yaw_error']:.2f} "
                f"lin={cmd['linear']:.2f} ang={cmd['angular']:.2f}"
            )

        # [Phase 2] pos 5Hz 웹소켓 전송
        self._pos_send_counter += 1
        ticks_per_pos = max(1, int(self.control_rate / 5.0))
        if self._pos_send_counter >= ticks_per_pos:
            self._pos_send_counter = 0
            if self._current_mission_id:
                self._send_ws({
                    "type": "pos",
                    "mission_id": self._current_mission_id,
                    "x": cx,
                    "y": cy,
                    "yaw": cyaw,
                    "t": time.time()
                })

        # 웨이포인트 도착 처리
        if cmd["arrived"]:
            arrived_idx = self.follower.current_index() - 1
            at = cmd.get("arrived_target")
            if at is not None:
                self.get_logger().info(
                    f"[WP] waypoint #{arrived_idx} 도착 ({at[0]:.2f},{at[1]:.2f})"
                )
            # 빈집 도착이면 상태머신 진입 (cmd 의 lin/ang 은 무시하고 정지)
            if arrived_idx in self._arrival_idx_set:
                self._enter_rotating_state(arrived_idx)
                self._publish_stop()
                return

        # 미션 완료
        if cmd["done"]:
            self._send_status("mission_done")
            self.get_logger().info("[DONE] 모든 빈집 방문 + 복귀 완료. 정지.")
            self._publish_stop()
            self._mission_active = False
            return

        self._publish_cmd(cmd["linear"], cmd["angular"])

    # ------------------------------------------------------------------
    # ROTATING: 빈집에서 목표 yaw 로 회전
    # ------------------------------------------------------------------
    def _enter_rotating_state(self, arrived_idx):
        pos = self._arrival_indices.index(arrived_idx)
        self._current_house = self._house_order[pos]
        self._current_target_yaw = float(self._arrival_yaws[pos])
        self._state = ST_ROTATING

        # [Phase 2] 상태 전송
        self._send_status("arrived", self._current_house, pos)
        self._send_status("rotating", self._current_house, pos)

        self.get_logger().info(
            f"[HOUSE] {self._current_house} 위치 도착 → "
            f"yaw {self._current_target_yaw:+.2f} rad 로 회전 시작"
        )

    def _tick_rotating(self, cx, cy, cyaw):
        yaw_err = normalize_angle(self._current_target_yaw - cyaw)
        if abs(yaw_err) <= self.house_yaw_tolerance:
            # 정렬 완료 → CAPTURING 진입
            self._publish_stop()
            
            # [Phase 2] 상태 전송
            pos = self._house_order.index(self._current_house)
            self._send_status("capturing", self._current_house, pos)

            self.get_logger().info(
                f"[HOUSE] {self._current_house} yaw 정렬 완료 (err={yaw_err:+.2f})"
            )
            # 사진 캡처 시점의 차량 위치(cx, cy)를 그대로 백엔드에 전달
            self._do_house_arrival(self._current_house, cx, cy)
            self._state = ST_CAPTURING
            self._paused_until = time.time() + float(self.pause_at_house)
            return

        # 비례 제어로 회전 (path_follower 회전 모드와 동일한 패턴)
        sign = 1.0 if yaw_err > 0 else -1.0
        mag = abs(yaw_err) * 1.5
        mag = min(self.angular_speed, max(0.2, mag))
        self._publish_cmd(0.0, sign * mag)

    # ------------------------------------------------------------------
    # CAPTURING: 정지 + 토픽 발행 + 사진 + POST + pause_at_house 대기
    # ------------------------------------------------------------------
    def _tick_capturing(self, cx, cy, cyaw):
        self._publish_stop()
        if time.time() >= self._paused_until:
            # 다음 빈집 진행 준비. follower 의 인덱스는 이미 도착 처리되며 +1 됨.
            self.get_logger().info(
                f"[HOUSE] {self._current_house} 처리 완료, 다음 목표로 진행."
            )
            self._current_house = None
            self._state = ST_FOLLOWING

    # ------------------------------------------------------------------
    # 도착 시 부수 작업: /house_arrival 발행 + 카메라 캡처 + 백엔드 POST
    # ------------------------------------------------------------------
    def _do_house_arrival(self, house_id, x, y):
        # 1) /house_arrival 발행
        msg = String()
        msg.data = house_id
        self.arrival_pub.publish(msg)
        self.get_logger().info(f"[ARRIVAL] /house_arrival 발행: {house_id}")

        # 2) 이미지 캡처 + POST (백그라운드 스레드로 비동기 처리)
        img = self._latest_image
        if img is None:
            self.get_logger().warn(
                f"[HOUSE] {house_id}: 캐시된 카메라 이미지 없음, POST 스킵 "
                f"(카메라 토픽 {self.camera_topic} 발행되는지 확인)"
            )
            return

        t = threading.Thread(
            target=self._send_image_to_backend,
            args=(house_id, img, x, y),
            daemon=True,
        )
        t.start()

    def _send_image_to_backend(self, house_id, img_msg, x, y):
        """
        별도 스레드에서 실행. 제어 루프를 블록하지 않음.

        엔드포인트: POST {backend_url}/api/robots/{robot_id}/image
        Form data: image (file), x, y, timestamp (ISO 8601)
        - address 필드는 Unity 시뮬레이션이라 실제 한국어 주소가 없어 생략.
          백엔드가 필수로 요구하면 house_id 또는 좌표 문자열을 채워 보낼 것.
        """
        if not self.backend_url:
            self.get_logger().info(
                f"[POST] {house_id}: backend_url 미설정 → POST 스킵"
            )
            return

        try:
            jpeg = ros_image_to_jpeg(img_msg)
        except Exception as e:
            self.get_logger().error(f"[POST] {house_id}: 이미지 변환 실패: {e}")
            return

        url = f"{self.backend_url}/api/robots/{self.robot_id}/image"
        iso_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        files = {"image": (f"{house_id}.jpg", jpeg, "image/jpeg")}
        data = {
            "x": f"{x:.2f}",
            "y": f"{y:.2f}",
            "timestamp": iso_ts,
        }

        try:
            # requests 는 외부 라이브러리. 미설치라면 여기서 import 에러.
            import requests
            r = requests.post(url, files=files, data=data, timeout=self.post_timeout)
            # 백엔드가 success/imageId/imageUrl/analysisJobId 를 돌려주면 로그
            extra = ""
            try:
                body = r.json()
                extra = (
                    f" success={body.get('success')} "
                    f"imageId={body.get('imageId')} "
                    f"job={body.get('analysisJobId')} "
                    f"url={body.get('imageUrl')}"
                )
            except Exception:
                extra = f" body[{len(r.content)}B]"
            self.get_logger().info(
                f"[POST] {house_id}: {r.status_code} ({len(jpeg)} bytes JPEG){extra}"
            )
        except Exception as e:
            self.get_logger().error(f"[POST] {house_id}: 전송 실패: {e}")

    # ------------------------------------------------------------------
    # cmd_vel 발행 유틸
    # ------------------------------------------------------------------
    def _publish_cmd(self, linear, angular):
        msg = Twist()
        msg.linear.x = float(linear)
        msg.angular.z = float(angular)
        self.cmd_pub.publish(msg)

    def _publish_stop(self):
        self._publish_cmd(0.0, 0.0)


def main(args=None):
    rclpy.init(args=args)
    node = PatrolNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node._publish_stop()
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
