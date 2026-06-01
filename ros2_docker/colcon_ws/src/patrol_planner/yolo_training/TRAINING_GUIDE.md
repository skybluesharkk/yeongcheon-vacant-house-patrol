# YOLO 실종자 탐지 모델 학습 가이드

## 📁 폴더 구조

```
yolo_training/
├── dataset.yaml          ← YOLO 데이터셋 설정 파일
├── raw_photos/           ← 원본 사진 보관 (학습에 직접 안 씀)
│   └── missing_person.png
├── dataset/
│   ├── images/
│   │   ├── train/        ← 학습용 이미지 (80%)
│   │   └── val/          ← 검증용 이미지 (20%)
│   └── labels/
│       ├── train/        ← 학습용 라벨 (.txt)
│       └── val/          ← 검증용 라벨 (.txt)
└── runs/                 ← 학습 결과 (자동 생성)
    └── detect/train/weights/best.pt  ← 최종 모델
```

---

## 🔨 단계별 진행

### Step 1: 사진 준비 (최소 20~50장 권장)

데모용이라면 **20장 정도**면 충분합니다. 다양한 조건에서 찍어주세요:
- 각도 다르게 (정면, 좌우 45도, 위아래)
- 조명 다르게 (밝은곳, 어두운곳)
- 거리 다르게 (가까이, 멀리)
- 배경 다르게

> 💡 유니티 시뮬레이션 환경에서 실제로 카메라에 잡히는 것이므로, **유니티 화면을 캡처한 이미지**로 학습시키면 더 정확합니다!

### Step 2: 라벨링 (LabelMe 대신 labelImg 또는 Roboflow 권장)

**LabelMe는 YOLO 포맷이 아닙니다!** LabelMe는 JSON 형식이라 변환 작업이 필요합니다.

#### 옵션 A: labelImg (로컬, 무료) ⭐ 추천
```bash
pip install labelImg
labelImg
```
- Format을 **YOLO**로 설정 (좌측 상단)
- 클래스명을 `P-1` 로 설정
- 얼굴 주위에 바운딩 박스를 그리면 `.txt` 파일이 자동 생성

#### 옵션 B: Roboflow (웹, 무료 티어)
- https://roboflow.com 에서 이미지 업로드
- 웹에서 바운딩 박스 클릭
- YOLO 포맷으로 다운로드

### Step 3: 라벨 파일 형식

각 이미지 `.jpg`에 대응하는 `.txt` 파일이 필요합니다.
파일명이 같아야 합니다: `photo_01.jpg` → `photo_01.txt`

```
# 라벨 파일 내용 (한 줄 = 바운딩 박스 하나)
# <클래스번호> <중심x> <중심y> <너비> <높이>  (모두 0~1 사이 비율값)
0 0.5 0.4 0.3 0.5
```

### Step 4: 이미지 배치

라벨링이 끝나면 이미지와 라벨을 train/val에 나눠 넣기:
```bash
# 예시: 20장 중 16장은 train, 4장은 val
# images/train/  에 학습용 이미지
# labels/train/  에 학습용 라벨
# images/val/    에 검증용 이미지
# labels/val/    에 검증용 라벨
```

### Step 5: 학습 실행

```bash
# 호스트(우분투)에서 실행 (GPU 사용 가능)
cd /home/shim/yeongcheon-vacant-house-patrol/ros2_docker/colcon_ws/src/patrol_planner/yolo_training

# YOLOv8 nano 모델 기반으로 전이학습 (데모용: 50 에포크면 충분)
yolo detect train \
  data=dataset.yaml \
  model=yolov8n.pt \
  epochs=50 \
  imgsz=640 \
  batch=8 \
  name=missing_person
```

> GPU가 없으면 `device=cpu` 추가. 20장 + 50에포크면 CPU로도 10분 안에 끝납니다.

### Step 6: 학습된 모델 배포

```bash
# 학습 결과 모델 위치
ls runs/detect/missing_person/weights/best.pt

# 도커 안으로 복사
sudo docker cp runs/detect/missing_person/weights/best.pt \
  $(sudo docker ps -q | head -1):/root/colcon_ws/best.pt
```

### Step 7: YOLO 탐지 노드 실행

```bash
# 도커 내부 터미널 5에서:
source /opt/ros/galactic/setup.bash
cd /root/colcon_ws && source install/setup.bash

ros2 run patrol_planner yolo_detector_node --ros-args \
  -p backend_url:="http://localhost:8000" \
  -p yolo_model_path:="/root/colcon_ws/best.pt" \
  -p confidence_threshold:=0.5
```

---

## ⚡ 빠른 데모용 꿀팁

사진이 1장뿐이라면, **데이터 증강(augmentation)** 을 활용하세요:

```python
# augment.py - 1장을 20장으로 뻥튀기
from PIL import Image, ImageEnhance, ImageFilter
import random, os

img = Image.open("raw_photos/missing_person.png")
out_dir = "dataset/images/train"
os.makedirs(out_dir, exist_ok=True)

for i in range(20):
    aug = img.copy()
    # 밝기 변화
    aug = ImageEnhance.Brightness(aug).enhance(random.uniform(0.6, 1.4))
    # 좌우 반전
    if random.random() > 0.5:
        aug = aug.transpose(Image.FLIP_LEFT_RIGHT)
    # 회전
    angle = random.uniform(-15, 15)
    aug = aug.rotate(angle, fillcolor=(128, 128, 128))
    # 블러
    if random.random() > 0.7:
        aug = aug.filter(ImageFilter.GaussianBlur(radius=1))
    aug.save(f"{out_dir}/missing_{i:03d}.jpg")
    print(f"✅ missing_{i:03d}.jpg 생성")
```

단, 증강 후에도 각 이미지마다 **라벨 파일은 새로 만들어야** 합니다. (증강으로 바운딩박스 위치가 달라지므로)

---

## 📌 dataset.yaml 수정

`dataset.yaml`의 클래스명을 실제 `missingPersonId`에 맞게 수정하세요:

```yaml
names:
  0: P-1   # ← 실제 missingPersonId 값으로 변경
```
