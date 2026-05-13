# patrol_planner

영천 빈집 순찰용 ROS2 Galactic 패키지.
빈집 ID 리스트를 받아 **TSP(nearest-neighbor) + A\*** 로 도로 그래프 위 최적 순찰 경로를 만들고,
`/cmd_vel` 로 차량을 자동 주행시킨다. 각 빈집에 도착하면 지정된 yaw 로 회전한 후
`/house_arrival` 발행 + 카메라 사진 캡처 + 백엔드 엔드포인트로 POST.

## 구성

```
patrol_planner/
├── package.xml
├── setup.py / setup.cfg
├── resource/patrol_planner
├── patrol_planner/
│   ├── graph.py          # 도로 노드/엣지, 빈집 좌표·yaw 정의
│   ├── planner.py        # A*, TSP, 전체 경로 합성
│   ├── path_follower.py  # 한 점씩 따라가는 회전/전진 컨트롤러
│   └── patrol_node.py    # 메인 ROS2 노드 (상태머신 + 카메라 + POST)
└── README.md
```

## 동작 흐름

```
/house_request    ──► PLAN(TSP+A*) ──► FOLLOWING ─┐
                                                  │ 빈집 위치 도달
                                                  ▼
                                              ROTATING (목표 yaw 정렬)
                                                  │ 정렬 완료
                                                  ▼
                                              CAPTURING
                                                ├─ /house_arrival 발행
                                                ├─ 캐시된 카메라 이미지 → JPEG
                                                ├─ 백엔드로 multipart POST
                                                └─ pause_at_house 초 대기
                                                  │
                                                  ▼
                                              FOLLOWING (다음 빈집)
```

## 토픽 / TF

| 방향        | 토픽 / 프레임             | 타입                  | 비고                                |
|------------|--------------------------|-----------------------|-------------------------------------|
| Subscribe  | `/house_request`         | `std_msgs/String`     | 방문할 빈집 ID, 쉼표 구분           |
| Subscribe  | `/camera/image_raw` (기본) | `sensor_msgs/Image`   | `camera_topic` 파라미터로 변경 가능 |
| Publish    | `/cmd_vel`               | `geometry_msgs/Twist` | 차량 속도 명령                       |
| Publish    | `/house_arrival`         | `std_msgs/String`     | 도착한 빈집 ID                       |
| TF         | `odom` → `base_footprint` | —                    | 현재 위치/자세                       |

## 파라미터

| 이름                  | 기본값                | 설명                                                |
|----------------------|----------------------|-----------------------------------------------------|
| `linear_speed`       | `1.0`                | 직진 속도 (m/s, odom 스케일 기준)                    |
| `angular_speed`      | `0.8`                | 회전 속도 (rad/s)                                    |
| `yaw_tolerance`      | `0.3`                | 일반 주행 yaw 허용 오차 (rad)                        |
| `arrival_tolerance`  | `0.5`                | 웨이포인트 도착 판정 거리 (m)                        |
| `house_yaw_tolerance`| `0.1`                | 빈집 도착 yaw 정렬 허용 오차 (rad, 더 빡빡함)        |
| `pause_at_house`     | `2.0`                | 사진 + POST 후 정지 시간 (s)                         |
| `odom_frame`         | `odom`               | tf source frame                                      |
| `base_frame`         | `base_footprint`     | tf target frame                                      |
| `control_rate`       | `20.0`               | 제어 루프 주파수 (Hz)                                |
| `auto_start`         | `True`               | 시작 시 `DEFAULT_HOUSE_IDS` 자동 방문                |
| `camera_topic`       | `/camera/image_raw`  | 캡처할 카메라 이미지 토픽                            |
| `backend_url`        | `""` (빈 값)         | 사진을 POST 할 URL. 비어있으면 POST 스킵             |
| `post_timeout`       | `5.0`                | POST timeout (s)                                     |

## 빈집 정의

[`graph.py`](patrol_planner/graph.py) 의 `HOUSES` 딕셔너리:

```python
HOUSES = {
    "H1": {"pos": (8.80,  2.40), "yaw":  0.0},   # odom 좌표 + 도착 후 yaw (rad)
    "H2": {"pos": (-2.40, -9.00), "yaw":  1.57},
    ...
}
```

- `pos`: 빈집 앞 차량 정지 위치 (odom 프레임, m)
- `yaw`: 정지 후 차량이 향할 방향 (rad). 카메라가 빈집을 향하도록 잡아준다.
  - `0` = +X (동), `pi/2` = +Y (북), `pi` = -X (서), `-pi/2` = -Y (남)

라우팅용 도로 노드는 좌표로부터 자동 매핑(`HOUSE_NODES`) 되므로 따로 적을 필요 없다.

## 부분 탐지 (전체 카탈로그 중 일부만 방문)

`HOUSES` 자체를 수정할 필요 없다. 두 가지 방법:

### 1) 런타임에 토픽으로 선택

```bash
ros2 topic pub --once /house_request std_msgs/String "data: 'H1,H4,H6'"
```

이 시점에 다시 TSP+A\* 계획이 짜인다.

### 2) 자동 시작 기본 리스트 수정

[`patrol_node.py`](patrol_planner/patrol_node.py) 상단의 `DEFAULT_HOUSE_IDS` 만 바꿔 빌드/재시작.

## 빌드 & 실행

### 빌드 (도커 컨테이너 안, root 유저)

```bash
cd /root/colcon_ws
source /opt/ros/galactic/setup.bash
colcon build --packages-select patrol_planner --symlink-install
source install/setup.bash
```

> `--symlink-install` 로 빌드해두면 이후 파이썬 소스 수정은 노드 재시작만으로 반영된다.

### 외부 파이썬 의존성

```bash
# 도커 컨테이너 안에서 (root)
apt-get update && apt-get install -y python3-pil python3-requests
# 또는 pip
pip3 install Pillow requests
```

### 실행

```bash
# Unity + ROS-TCP-Endpoint 가 떠 있고 TF/카메라가 publish 되는 상태에서
ros2 run patrol_planner patrol_node \
    --ros-args \
    -p backend_url:=http://<백엔드_호스트>:<포트>/<엔드포인트> \
    -p camera_topic:=/camera/image_raw
```

`backend_url` 을 비워두면 POST 단계만 스킵되고 그 외 동작(yaw 회전 + 토픽 발행 + 사진 캡처)은
정상 진행된다 — 백엔드 없이도 차량 동작 확인 가능.

## 외부 도구로 확인

```bash
# 빈집 방문 요청
ros2 topic pub --once /house_request std_msgs/String "data: 'H1,H4'"

# 도착 모니터링
ros2 topic echo /house_arrival

# 차량 속도 명령 확인
ros2 topic echo /cmd_vel

# 디버그 로그 (각 틱마다 위치/yaw/명령)
ros2 run patrol_planner patrol_node --ros-args --log-level debug
```

## 백엔드 POST 형식

`multipart/form-data` 로 다음 필드를 전송:

| 필드       | 타입         | 내용                                 |
|-----------|-------------|--------------------------------------|
| `image`   | file        | `<house_id>.jpg`, `image/jpeg`        |
| `house_id`| form field  | 예: `"H1"`                           |
| `timestamp` | form field | 유닉스 타임 (초, 소수점)             |

POST 는 별도 스레드로 비동기 실행돼 제어 루프를 막지 않는다. POST 실패는 로그로만
남기고 미션을 계속 진행한다.

## 그래프/맵 커스터마이징

- 도로 노드: `_RAW_NODES` (Unity 인스펙터 X, Z 값 그대로)
- 도로 연결: `_RAW_EDGES` (단방향만 적어도 양방향 자동 변환)
- 빈집: `HOUSES` (odom 좌표 + 목표 yaw)

좌표 변환은 `unity_to_odom()` 가 자동으로 처리하므로 인스펙터 값만 신경 쓰면 된다.