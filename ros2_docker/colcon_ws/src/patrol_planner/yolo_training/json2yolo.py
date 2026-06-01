import os
import json
import glob
import shutil

# 클래스 맵핑 (Labelme에서 입력한 P-1을 YOLO의 0번으로)
CLASS_MAPPING = {"P-1": 0}

# 변환된 JSON 의 백업 위치. 재변환 가능하도록 삭제 대신 옮긴다.
JSON_BACKUP_DIR = "dataset/_json_backup"


def shape_to_bbox(shape, img_w, img_h):
    """
    LabelMe shape -> YOLO bbox (cx, cy, w, h) 정규화 좌표.

    rectangle/polygon/linestrip 등 어떤 shape 모드든 모든 점에서 min/max 를
    구해서 외접 직사각형을 만든다. 기존 코드처럼 `pts[0], pts[1]` 만 쓰면
    polygon 모드에서 박스가 한 변으로 찌부러지는 버그가 생긴다.
    """
    pts = shape.get("points") or []
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    cx = ((x1 + x2) / 2.0) / img_w
    cy = ((y1 + y2) / 2.0) / img_h
    w = (x2 - x1) / img_w
    h = (y2 - y1) / img_h
    # 음수/이상치 클램프
    if w <= 0 or h <= 0:
        return None
    return cx, cy, w, h


def convert_dir(subset):
    img_dir = f"dataset/images/{subset}"
    lbl_dir = f"dataset/labels/{subset}"
    backup_dir = os.path.join(JSON_BACKUP_DIR, subset)

    os.makedirs(lbl_dir, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    json_files = glob.glob(os.path.join(img_dir, "*.json"))
    if not json_files:
        print(f"⚠️ {subset} 폴더에 JSON 파일이 없습니다.")
        return

    suspect_count = 0
    for json_file in json_files:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        img_w = data["imageWidth"]
        img_h = data["imageHeight"]
        base_name = os.path.splitext(os.path.basename(json_file))[0]
        txt_path = os.path.join(lbl_dir, f"{base_name}.txt")

        with open(txt_path, "w", encoding="utf-8") as f_out:
            for shape in data.get("shapes", []):
                label = shape["label"]
                if label not in CLASS_MAPPING:
                    continue
                bbox = shape_to_bbox(shape, img_w, img_h)
                if bbox is None:
                    continue
                cx, cy, w, h = bbox

                # 의심스러운 박스 감지: 너무 가늘거나 너무 작으면 경고
                aspect = max(w, h) / max(min(w, h), 1e-9)
                if w < 0.05 or h < 0.05 or aspect > 4.0:
                    print(f"⚠️  [의심] {base_name}: shape_type={shape.get('shape_type')} "
                          f"bbox=(w={w:.3f}, h={h:.3f}, aspect={aspect:.1f}) "
                          f"points={len(shape.get('points', []))}")
                    suspect_count += 1

                f_out.write(
                    f"{CLASS_MAPPING[label]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"
                )

        # JSON 은 삭제하지 않고 백업으로 이동. 추후 재변환/검수 가능.
        shutil.move(json_file, os.path.join(backup_dir, os.path.basename(json_file)))

        print(f"✅ [{subset}] 변환 완료: {base_name}.txt")

    if suspect_count:
        print(f"\n⚠️  의심 라벨 {suspect_count}건. 위 경고 목록 확인 후 LabelMe 로 재라벨링 권장.")


if __name__ == "__main__":
    print("🚀 변환을 시작합니다...")
    convert_dir("train")
    convert_dir("val")
    print("\n🎉 모든 변환이 완료되었습니다!")
    print(f"📦 원본 JSON 은 {JSON_BACKUP_DIR}/ 에 백업됨.")
    print("\n다음 명령어로 학습을 다시 시작하세요:")
    print("yolo detect train data=dataset.yaml model=yolov8n.pt epochs=100 imgsz=640 batch=8 name=missing_person")
