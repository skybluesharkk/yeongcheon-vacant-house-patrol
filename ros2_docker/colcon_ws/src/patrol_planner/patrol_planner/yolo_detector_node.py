# -*- coding: utf-8 -*-
"""
yolo_detector_node: 실종자 탐지 전용 노드
- /camera/image_raw 토픽 구독
- YOLO 추론 수행
- 특정 대상 발견 시 tf 로 현재 좌표 획득
- 백엔드에 POST 전송 (중복 발송 방지 적용)
"""

import io
import threading
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from sensor_msgs.msg import Image
import tf2_ros
from tf2_ros import LookupException, ConnectivityException, ExtrapolationException

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    import requests
except ImportError:
    requests = None

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None


def ros_image_to_jpeg(img_msg, quality=85):
    """sensor_msgs/Image → JPEG bytes."""
    if PILImage is None:
        raise RuntimeError("Pillow(PIL) 라이브러리가 설치되지 않았습니다.")

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

    # Unity RenderTexture 가 OpenGL Y축 뒤집힌 상태로 들어오기 때문에 vertical flip.
    # 그리고 영찬님 요청: 실종자 후보 이미지는 시계방향 90° 회전해서 정방향으로 보내기.
    # PIL.Image.transpose(ROTATE_270) 가 시계방향 90° 회전.
    from PIL import ImageOps
    img = ImageOps.flip(img)
    img = img.transpose(PILImage.ROTATE_270)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class YoloDetectorNode(Node):
    def __init__(self):
        super().__init__("yolo_detector_node")

        # --- 파라미터 ---
        self.declare_parameter("camera_topic", "/camera/image_raw")
        self.declare_parameter("backend_url", "")
        self.declare_parameter("robot_id", "robot-01")
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("yolo_model_path", "yolov8n.pt")
        self.declare_parameter("confidence_threshold", 0.5)

        self.camera_topic = self.get_parameter("camera_topic").value
        self.backend_url = self.get_parameter("backend_url").value.rstrip("/")
        self.robot_id = self.get_parameter("robot_id").value
        self.odom_frame = self.get_parameter("odom_frame").value
        self.base_frame = self.get_parameter("base_frame").value
        self.yolo_model_path = self.get_parameter("yolo_model_path").value
        self.confidence_threshold = self.get_parameter("confidence_threshold").value

        # --- 초기화 ---
        if YOLO is None:
            self.get_logger().error("ultralytics 라이브러리가 설치되지 않아 YOLO를 로드할 수 없습니다.")
            return

        self.get_logger().info(f"YOLO 모델 로드 중: {self.yolo_model_path}")
        try:
            self.model = YOLO(self.yolo_model_path)
        except Exception as e:
            self.get_logger().error(f"YOLO 모델 로드 실패: {e}")
            self.model = None

        # 중복 방지 로직 제거. 매 추론마다 발견된 ID 는 전부 backend 로 보냄.
        # (이전엔 _reported_persons 셋에 담아 같은 ID 두 번째부터 무시했지만
        #  영찬님 요청으로 매번 알림 보내도록 변경)

        # TF 리스너
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # 이미지 구독 (Throttling 적용: 1초에 약 2번만 추론하도록)
        # QoS: 센서 데이터용 (BEST_EFFORT). Unity ROS-TCP-Endpoint publisher 와 매칭됨.
        # 기본값(RELIABLE) 로 두면 publisher 와 어긋나 메시지가 전달되지 않거나
        # 변환 실패(make_tuple RuntimeError) 가 발생함.
        self.image_sub = self.create_subscription(
            Image, self.camera_topic, self._on_image, qos_profile_sensor_data
        )
        self._last_inference_time = self.get_clock().now()
        self._inference_interval = 0.5  # 500ms 간격으로 추론

        self.get_logger().info(
            f"yolo_detector_node 초기화 완료 | "
            f"model={self.yolo_model_path}, threshold={self.confidence_threshold}, "
            f"backend_url={self.backend_url}"
        )

    def _get_current_pose(self):
        try:
            tf = self.tf_buffer.lookup_transform(
                self.odom_frame,
                self.base_frame,
                rclpy.time.Time(),
            )
            return tf.transform.translation.x, tf.transform.translation.y
        except (LookupException, ConnectivityException, ExtrapolationException):
            return None, None

    def _on_image(self, msg: Image):
        if self.model is None:
            return

        now = self.get_clock().now()
        dt = (now - self._last_inference_time).nanoseconds / 1e9
        if dt < self._inference_interval:
            return  # Throttling
        self._last_inference_time = now

        try:
            # 1) 이미지를 PIL 로 변환 후 YOLO 추론
            if PILImage is None:
                return

            w, h = msg.width, msg.height
            enc = msg.encoding
            data = bytes(msg.data)

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
                self.get_logger().warn(f"[ENC] 지원 안하는 인코딩: {enc} (w={w}, h={h})")
                return # 지원하지 않는 포맷

            # 2) YOLO 추론 실행
            results = self.model(img, verbose=False)

            # 디버그: 모든 박스의 최고 confidence 한 줄 로그 (탐지 0건이어도)
            top_conf = 0.0
            box_count = 0
            for r in results:
                for box in r.boxes:
                    box_count += 1
                    c = float(box.conf[0])
                    if c > top_conf:
                        top_conf = c
            self.get_logger().info(
                f"[DBG] enc={enc} {w}x{h} | boxes={box_count} top_conf={top_conf:.3f} (threshold={self.confidence_threshold})"
            )

            detected_ids = set()
            accepted_boxes = []
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    conf = float(box.conf[0])
                    cls_idx = int(box.cls[0])
                    class_name = self.model.names[cls_idx]
                    if conf >= self.confidence_threshold:
                        detected_ids.add(class_name)
                        accepted_boxes.append(f"{class_name}:{conf:.3f}")

            if accepted_boxes:
                self.get_logger().info(f"[DBG] threshold 통과 클래스: {', '.join(accepted_boxes)}")
            elif box_count > 0:
                self.get_logger().warn("[DBG] box 는 있지만 threshold 를 통과한 클래스가 없습니다.")

            # 3) 발견된 대상 전부 전송 (중복 방지 없음 — 매번 보냄)
            for person_id in detected_ids:
                self._handle_detection(person_id, msg)

        except Exception as e:
            self.get_logger().error(f"YOLO 추론 에러: {e}")

    def _handle_detection(self, person_id, img_msg):
        x, y = self._get_current_pose()
        if x is None or y is None:
            self.get_logger().warn(f"[{person_id}] 탐지되었으나 TF 조회가 안되어 좌표를 알 수 없음.")
            x, y = 0.0, 0.0

        self.get_logger().info(f"[DETECT] 새로운 실종자 탐지: {person_id} (x={x:.2f}, y={y:.2f})")

        if not self.backend_url:
            self.get_logger().warn("backend_url 이 설정되지 않아 POST 전송을 건너뜁니다.")
            return

        if requests is None:
            self.get_logger().error("requests 라이브러리가 없어 POST 전송 불가.")
            return

        t = threading.Thread(
            target=self._send_to_backend,
            args=(person_id, img_msg, x, y),
            daemon=True,
        )
        t.start()

    def _send_to_backend(self, person_id, img_msg, x, y):
        try:
            jpeg = ros_image_to_jpeg(img_msg)
        except Exception as e:
            self.get_logger().error(f"이미지 변환 에러: {e}")
            return

        url = f"{self.backend_url}/api/robots/{self.robot_id}/missing-person"
        iso_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        
        files = {"image": (f"{person_id}_detected.jpg", jpeg, "image/jpeg")}
        data = {
            "missing_person_id": person_id,
            "x": f"{x:.2f}",
            "y": f"{y:.2f}",
            "timestamp": iso_ts,
        }

        try:
            r = requests.post(url, files=files, data=data, timeout=5.0)
            if 200 <= r.status_code < 300:
                self.get_logger().info(f"[POST] 실종자 {person_id} 전송 완료: {r.status_code}")
            else:
                self.get_logger().error(
                    f"[POST] 실종자 {person_id} 전송 실패: {r.status_code} {r.text[:300]}"
                )
        except Exception as e:
            self.get_logger().error(f"[POST] 실종자 {person_id} 전송 실패: {e}")


def main(args=None):
    rclpy.init(args=args)
    node = YoloDetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
