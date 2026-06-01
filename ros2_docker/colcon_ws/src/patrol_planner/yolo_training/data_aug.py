# augment.py - 1장을 20장으로 뻥튀기
from PIL import Image, ImageEnhance, ImageFilter
import random, os

img = Image.open("raw_photos/missing_person.png").convert("RGB")
out_dir = "dataset/images/train_missing_person"
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