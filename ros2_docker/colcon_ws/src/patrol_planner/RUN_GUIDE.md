# 영천 빈집 순찰 시스템 실행 가이드 (로컬 시뮬레이션 환경)

이 문서는 로컬 도커(Docker) 환경에서 유니티, 백엔드 대시보드, 로봇 제어, AI 실종자 탐지 노드를 모두 통합하여 실행하기 위해 필요한 **총 5개의 터미널 셋업 방법**을 설명합니다.

모든 실행 환경은 도커 컨테이너 내부로 격리되어 있으므로, 2~5번 터미널은 반드시 도커 컨테이너 내부로 접속한 뒤에 명령어를 실행해야 합니다.

---

## 🔑 도커 컨테이너 접속 방법 (공통)
2, 3, 4, 5번 터미널을 실행하려면 먼저 호스트(우분투)에서 새 터미널 창을 열고 아래 명령어를 입력하여 도커 안으로 진입해야 합니다.

```bash
sudo docker exec -it $(sudo docker ps -q | head -1) bash
```
> **참고**: 위 명령어는 현재 실행 중인 첫 번째 도커 컨테이너 안으로 접속하는 편리한 숏컷입니다. 접속에 성공하면 프롬프트가 `root@...:/#` 형태로 바뀝니다.

---

## 🖥️ 전체 실행 순서 (터미널 5개)

### [터미널 1] 도커 메인 시스템 (백그라운드)
가장 처음에 `docker run` (또는 `docker-compose`)으로 켠 창입니다.
- **역할**: 도커 시스템 유지 및 VNC/GUI 환경 제공
- **출력**: `x11vnc entered RUNNING state...`
- **조치**: **절대 건드리지 말고 (Ctrl+C 금지) 그냥 창을 최소화해 두세요.**

---
*(아래 터미널부터는 모두 공통 접속 방법으로 도커 안에 들어간 상태에서 실행합니다.)*

### [터미널 2] 유니티 ↔ ROS2 통신 브릿지
로봇의 위치 정보(TF)와 카메라 영상을 주고받기 위해 통신 통로를 엽니다.
```bash
# 도커 환경 설정 적용
source /opt/ros/galactic/setup.bash
cd /root/colcon_ws
source install/setup.bash

# 브릿지 서버 켜기
ros2 run ros_tcp_endpoint default_server_endpoint
```
> **체크 포인트**: 이 창을 켜둔 뒤 **유니티 에디터에서 재생(▶)** 버튼을 누르면 통신이 연결됩니다.

---

### [터미널 3] 관제 대시보드 (가짜 백엔드 서버)
지도 위에 로봇이 움직이는 모습과 실시간 카메라, 사진, 로그를 띄워주는 미니 서버입니다.
```bash
cd /root/colcon_ws/src/patrol_planner/dummy_backend
python3 -m uvicorn dummy_server:app --host 0.0.0.0 --port 8000
```
> **체크 포인트**: 실행 후 진짜 컴퓨터의 **크롬 브라우저**에서 `http://localhost:8000` 에 접속하여 화면을 띄워두세요.

---

### [터미널 4] 메인 터틀봇 두뇌 (자율주행 제어)
경로를 계산하고, 바퀴를 굴리며, 빈집 도착 시 사진을 찍어 3번(서버)으로 보내는 핵심 주행 노드입니다.
```bash
# 도커 환경 설정 적용
source /opt/ros/galactic/setup.bash
cd /root/colcon_ws
source install/setup.bash

# 메인 주행 노드 실행 (백엔드 서버 주소 주입)
ros2 run patrol_planner patrol_node --ros-args -p backend_url:="http://localhost:8000"
```
> **체크 포인트**: 실행 후 유니티 화면이나 브라우저 대시보드를 보시면 로봇이 맵의 길을 따라 움직이기 시작합니다.

---

### [터미널 5] 실종자 탐지 AI (선택 사항)
로봇의 카메라 영상을 받아 YOLO 모델로 실종자를 탐지하는 노드입니다. 실종자 발견 시 즉시 대시보드에 붉은색 경고가 뜹니다.
```bash
# 도커 환경 설정 적용
source /opt/ros/galactic/setup.bash
cd /root/colcon_ws
source install/setup.bash

# 욜로 탐지 노드 실행
ros2 run patrol_planner yolo_detector_node --ros-args -p backend_url:="http://localhost:8000"
```

---

## 🎮 시뮬레이션 즐기기!
위 5개의 터미널이 모두 켜져 있다면, 크롬 브라우저(대시보드)에서 다음 기능들을 완벽히 테스트하실 수 있습니다.

1. **실시간 뷰**: 왼쪽 캔버스에서 파란 점선을 따라 이동하는 빨간색 자동차 마커와 중앙의 실시간 카메라 영상을 감상하세요.
2. **동적 순찰 (Mission Control)**: 대시보드에서 방문하고 싶은 빈집 체크박스(예: H1, H6)만 선택한 뒤 `Start Selected Patrol` 버튼을 눌러보세요. 로봇이 즉각 반응하여 경로를 짧게 재수정하고 이동합니다!
