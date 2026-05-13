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

        self._reported_persons = set()  # 한 번 탐지된 ID는 다시 전송하지 않음

        # TF 리스너
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # 이미지 구독 (Throttling 적용: 1초에 약 2번만 추론하도록)
        self.image_sub = self.create_subscription(
            Image, self.camera_topic, self._on_image, 1
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
                return # 지원하지 않는 포맷

            # 2) YOLO 추론 실행
            results = self.model(img, verbose=False)
            
            detected_ids = set()
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    conf = float(box.conf[0])
                    if conf >= self.confidence_threshold:
                        cls_idx = int(box.cls[0])
                        # model.names 에 클래스 라벨(이름)이 맵핑되어 있음
                        class_name = self.model.names[cls_idx]
                        detected_ids.add(class_name)

            # 3) 새로 탐지된 대상 전송
            for person_id in detected_ids:
                if person_id not in self._reported_persons:
                    self._handle_detection(person_id, msg)

        except Exception as e:
            self.get_logger().error(f"YOLO 추론 에러: {e}")

    def _handle_detection(self, person_id, img_msg):
        # 이번 런타임 내에서만 전송 방지 (메모리 셋에 추가)
        self._reported_persons.add(person_id)
        
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
            self.get_logger().info(f"[POST] 실종자 {person_id} 전송 완료: {r.status_code}")
        except Exception as e:
            self.get_logger().error(f"[POST] 실종자 {person_id} 전송 실패: {e}")
            # 실패 시 재전송을 위해 reported_persons 에서 제거할 수도 있음
            # self._reported_persons.discard(person_id)


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
