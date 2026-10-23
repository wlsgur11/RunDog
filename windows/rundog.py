"""RunDog for Windows: CPU·GPU 사용률에 맞춰 작업 표시줄 트레이에서 달리는 픽셀 펫.

Mac 버전(mac/main.swift)과 같은 그림 로직을 파이썬으로 옮긴 것.
손으로 프레임을 찍는 대신 몸통·머리·다리를 도형으로 두고 관절 각도를 돌려가며 그리기 때문에,
트레이 아이콘 크기(16~32px)에 맞춰 그 크기 그대로 픽셀을 찍는다.

    python rundog.py                      # 트레이 앱 실행 (Windows)
    python rundog.py --preview out.png    # 모든 캐릭터 미리보기 시트 저장 (어느 OS든)
    python rundog.py --selftest           # 그리기·아이콘·CPU/GPU 측정 점검 (Windows)
"""

import ctypes
import json
import math
import os
import sys
import tempfile
import threading
import time

from PIL import Image

FRAME_COUNT = 16
UNIT_H = 18  # 캐릭터 설계 높이 (Mac 메뉴 바 기준 단위)

# MARK: - 픽셀 캔버스

# 픽셀마다 어떤 재질인지 기록해 두고, 이미지로 바꿀 때 팔레트(또는 모노 알파)로 색을 정함
(NONE, FAR, BODY, LIGHT, EAR, STRIPE, BLUSH, DARK, HOLE, TONGUE, WHISKER, SWEAT,
 SKIN, SHIRT, PANTS, HAIR) = range(16)
OPEN_INKS = (NONE, FAR, WHISKER, SWEAT)


class Shape:
    """test(x, y)로 안쪽인지 판단하는 도형. bbox로 검사할 픽셀 범위를 줄임."""

    def __init__(self, test, bbox):
        self.test = test
        self.bbox = bbox


class Canvas:
    """size x size 픽셀 격자. 캐릭터 단위 좌표(unit_w x 18)를 가운데에 맞춰 축소/확대해서 찍음."""

    def __init__(self, size, unit_w):
        self.w = self.h = size
        self.scale = size / max(unit_w, UNIT_H)
        self.ox = (size / self.scale - unit_w) / 2
        self.oy = (size / self.scale - UNIT_H) / 2
        self.ink = [NONE] * (size * size)
        # 몸통·머리·다리 같은 부위 번호. 나중에 그린 부위가 앞에 있어서 경계에 테두리가 생김
        self.part = [0] * (size * size)
        self.parts = 0

    def paint(self, ink, shape, new_part=None, only_empty=False):
        is_part = new_part if new_part is not None else ink in (BODY, FAR)
        if is_part:
            self.parts += 1
        x0, y0, x1, y1 = shape.bbox
        s = self.scale
        px0, px1 = max(0, int((x0 + self.ox) * s) - 1), min(self.w - 1, int((x1 + self.ox) * s) + 1)
        py0, py1 = max(0, int((y0 + self.oy) * s) - 1), min(self.h - 1, int((y1 + self.oy) * s) + 1)
        for py in range(py0, py1 + 1):
            uy = (py + 0.5) / s - self.oy
            for px in range(px0, px1 + 1):
                if not shape.test((px + 0.5) / s - self.ox, uy):
                    continue
                i = py * self.w + px
                if only_empty and self.ink[i] != NONE:
                    continue
                if is_part or self.ink[i] == NONE:
                    self.part[i] = self.parts
                self.ink[i] = ink

    def dot(self, ink, p):
        # 아이콘이 작으면 눈 하이라이트가 눈을 통째로 지워서 생략
        if ink == HOLE and self.scale < 0.7:
            return
        x, y = int((p[0] + self.ox) * self.scale), int((p[1] + self.oy) * self.scale)
        if 0 <= x < self.w and 0 <= y < self.h:
            self.ink[y * self.w + x] = ink

    def at(self, x, y):
        return self.ink[y * self.w + x] if 0 <= x < self.w and 0 <= y < self.h else NONE

    def is_edge(self, x, y):
        """바깥이나 뒤쪽 부위와 맞닿은 픽셀이면 테두리."""
        me = self.part[y * self.w + x]
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            ink = self.at(nx, ny)
            if ink in OPEN_INKS or self.part[ny * self.w + nx] < me:
                return True
        return False

    def alpha(self, x, y):
        """모노 모드: 몸 안쪽은 반투명, 테두리는 진하게."""
        ink = self.at(x, y)
        if ink in (NONE, HOLE):
            return 0.0
        fixed = {FAR: 0.32, WHISKER: 0.75, SWEAT: 0.6, DARK: 1.0, TONGUE: 0.85}
        if ink in fixed:
            return fixed[ink]
        if self.is_edge(x, y):
            return 1.0
        return {LIGHT: 0.12, BLUSH: 0.22, STRIPE: 0.75, EAR: 0.95, HAIR: 0.95,
                SKIN: 0.3, SHIRT: 0.7, PANTS: 0.15}.get(ink, 0.45)

    def color(self, x, y, p):
        ink = self.at(x, y)
        if ink == NONE:
            return None
        fixed = {FAR: darker(p["fill"], 0.72), WHISKER: p["outline"], DARK: p["dark"],
                 HOLE: 0xFFFFFF, TONGUE: 0xFF6B85, SWEAT: 0x7EC8FF}
        if ink in fixed:
            return fixed[ink]
        if self.is_edge(x, y):
            return p["outline"]
        return {LIGHT: p["light"], EAR: p["ear"], STRIPE: p["ear"], BLUSH: 0xFF94A8,
                SKIN: 0xFFD9B8, SHIRT: 0xE8453C, PANTS: 0xFFD34D, HAIR: 0x2B1B12}.get(ink, p["fill"])


# MARK: - 색

def darker(c, f):
    return (int((c >> 16 & 0xFF) * f) << 16) | (int((c >> 8 & 0xFF) * f) << 8) | int((c & 0xFF) * f)


def rgb(c):
    return (c >> 16 & 0xFF, c >> 8 & 0xFF, c & 0xFF)


# "auto"는 색 없이 작업 표시줄 밝기에 맞춰 흰색/검은색으로 칠하는 모드
PALETTES = {
    "cheese": dict(title="치즈", fill=0xEBA35C, outline=0x5B3417, light=0xFFF3E0, ear=0xC47836, dark=0x2B1B12),
    "cream": dict(title="크림", fill=0xF3E6D3, outline=0x6E5A47, light=0xFFFFFF, ear=0xD9B78F, dark=0x2B2420),
    "choco": dict(title="초코", fill=0x8B5A3C, outline=0x3A2215, light=0xF0D2B0, ear=0x5C3824, dark=0x1E120B),
    "black": dict(title="까망 (턱시도)", fill=0x3C3C44, outline=0x141418, light=0xF2F2F5, ear=0x25252B, dark=0x0E0E10),
    "gray": dict(title="회색", fill=0xA8ABB3, outline=0x44474F, light=0xF1F1F4, ear=0x777B85, dark=0x1C1D22),
}
COLOR_CHOICES = [("auto", "자동 (작업 표시줄 색)")] + [(k, v["title"]) for k, v in PALETTES.items()]


# MARK: - 도형

def ellipse(cx, cy, rx, ry, tilt=0.0):
    c, s = math.cos(tilt), math.sin(tilt)

    def test(x, y):
        dx, dy = x - cx, y - cy
        u, v = dx * c + dy * s, -dx * s + dy * c
        return (u * u) / (rx * rx) + (v * v) / (ry * ry) <= 1

    r = max(rx, ry)
    return Shape(test, (cx - r, cy - r, cx + r, cy + r))


def capsule(a, b, r):
    (ax, ay), (bx, by) = a, b
    vx, vy = bx - ax, by - ay
    len2 = vx * vx + vy * vy

    def test(x, y):
        t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / len2)) if len2 > 0 else 0.0
        dx, dy = x - (ax + vx * t), y - (ay + vy * t)
        return dx * dx + dy * dy <= r * r

    return Shape(test, (min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r, max(ay, by) + r))


def triangle(a, b, c):
    def side(p, q, x, y):
        return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])

    def test(x, y):
        d1, d2, d3 = side(a, b, x, y), side(b, c, x, y), side(c, a, x, y)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    xs, ys = (a[0], b[0], c[0]), (a[1], b[1], c[1])
    return Shape(test, (min(xs), min(ys), max(xs), max(ys)))


def union(*shapes):
    def test(x, y):
        for s in shapes:
            x0, y0, x1, y1 = s.bbox
            if x0 <= x <= x1 and y0 <= y <= y1 and s.test(x, y):
                return True
        return False

    return Shape(test, (min(s.bbox[0] for s in shapes), min(s.bbox[1] for s in shapes),
                        max(s.bbox[2] for s in shapes), max(s.bbox[3] for s in shapes)))


def curve(points, r0, r1):
    """베지어 곡선을 따라가는 굵기가 줄어드는 선 (꼬리, 귀)."""
    def at(t):
        q = list(points)
        while len(q) > 1:
            q = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for a, b in zip(q, q[1:])]
        return q[0]

    n = 24
    pts = [at(i / n) for i in range(n + 1)]
    return union(*[capsule(pts[i], pts[i + 1], r0 + (r1 - r0) * i / n) for i in range(n)])


def leg(root, upper, lower, swing, fold, r, paw):
    """관절에서 아래로 뻗는 2마디 다리. swing은 앞뒤 각도, fold는 무릎이 뒤로 접히는 정도."""
    knee = (root[0] + math.sin(swing) * upper, root[1] - math.cos(swing) * upper)
    a = swing - fold
    foot = (knee[0] + math.sin(a) * lower, knee[1] - math.cos(a) * lower)
    return union(capsule(root, knee, r), capsule(knee, foot, r * 0.9), ellipse(foot[0] + 0.4, foot[1], paw[0], paw[1]))


# MARK: - 캐릭터
# 각 프레임 함수의 stress는 사용률이 높을 때 쓰는 버전 (혀 내밀기, 땀, 효과선)

def gallop_leg(root, w, phase, hind, amp, upper, lower, r, paw):
    """갤럽: 앞다리 한 쌍과 뒷다리 한 쌍이 반 박자씩 어긋나서 움직임."""
    a = w + 2 * math.pi * phase
    rest = 0.25 if hind else 0.0
    return leg(root, upper, lower, amp * math.sin(a) + rest, 0.8 * max(0.0, math.cos(a)) + rest * 2, r, paw)


def dog_frame(c, t, stress):
    w = 2 * math.pi * t
    bob = 0.6 * math.sin(2 * w)
    hb = bob + 0.4 * math.sin(2 * w - 0.8)
    wag = 0.9 * math.sin(2 * w)
    shoulder, hip = (19.5, 5.6 + bob), (9.5, 5.6 + bob)

    def dog_leg(root, phase, hind):
        return gallop_leg(root, w, phase, hind, 0.7, 2.2, 2.3, 1.3, (1.5, 1))

    c.paint(FAR, dog_leg((shoulder[0] - 1.2, shoulder[1]), 0.12, False))
    c.paint(FAR, dog_leg((hip[0] - 1.2, hip[1]), 0.62, True))
    tail_tip = (5.5 + wag, 14.5 + bob)
    c.paint(BODY, curve([(8, 9 + bob), (5, 10.5 + bob), tail_tip], 1.4, 1))
    c.paint(LIGHT, ellipse(tail_tip[0], tail_tip[1] - 0.3, 1.2, 1.2))
    # 동글동글한 몸통 + 밝은 가슴털
    c.paint(BODY, ellipse(14.5, 7.4 + bob, 6.6, 4, tilt=0.05 * math.sin(w)))
    c.paint(LIGHT, ellipse(19.2, 6 + bob, 2.4, 1.8))
    c.paint(BODY, dog_leg(shoulder, 0, False))
    c.paint(BODY, dog_leg(hip, 0.5, True))
    # 큰 머리 + 밝은 주둥이
    c.paint(BODY, ellipse(23.3, 11.4 + hb, 5.3, 4.8))
    c.paint(LIGHT, ellipse(27.6, 9.6 + hb, 2.4, 1.7))
    if stress:
        c.paint(TONGUE, capsule((27.3, 8.4 + hb), (26.9, 6.9 + hb), 0.85))
    # 축 처진 귀, 달릴 때 살짝 펄럭임
    c.paint(EAR, curve([(22.3, 15.4 + hb), (19.3, 15.1 + hb), (19.1 + 0.7 * math.sin(2 * w - 1.2), 10.8 + hb)], 1.6, 1.2))
    c.paint(BLUSH, ellipse(24.4, 9.6 + hb, 1.1, 0.65))
    c.paint(DARK, ellipse(25.6, 12.1 + hb, 1.05, 1.5))
    c.dot(HOLE, (25.3, 12.8 + hb))
    c.paint(DARK, ellipse(29.4, 10.5 + hb, 1, 0.8))


def cat_frame(c, t, stress):
    w = 2 * math.pi * t
    bob = 0.5 * math.sin(2 * w)
    hb = bob + 0.3 * math.sin(2 * w - 0.8)
    stretch = 0.6 * math.sin(w)
    shoulder, hip = (19.5 + stretch, 5.5 + bob), (8.8 - stretch, 5.5 + bob)

    def cat_leg(root, phase, hind):
        return gallop_leg(root, w, phase, hind, 0.85, 2.2, 2.5, 1.05, (1.2, 0.8))

    c.paint(FAR, cat_leg((shoulder[0] - 1.2, shoulder[1]), 0.12, False))
    c.paint(FAR, cat_leg((hip[0] - 1.2, hip[1]), 0.62, True))
    # 길게 S자로 휘는 꼬리 + 줄무늬 꼬리 끝
    tail_tip = (4 + 1.2 * math.sin(w), 15.5 + bob)
    c.paint(BODY, curve([(7.5, 8 + bob), (2.5, 8.5 + bob), (1.8, 13 + bob), tail_tip], 1.2, 0.9))
    c.paint(STRIPE, ellipse(tail_tip[0] - 0.2, tail_tip[1] - 0.4, 1.2, 1.2))
    c.paint(BODY, ellipse(14, 7.2 + bob, 7 + stretch, 3.4, tilt=0.05 * math.sin(w)))
    # 등 줄무늬
    for sx in (10.5, 13.5, 16.5):
        c.paint(STRIPE, capsule((sx + stretch * 0.5, 10.2 + bob), (sx - 0.5 + stretch * 0.5, 8.9 + bob), 0.6))
    c.paint(LIGHT, ellipse(19.4 + stretch, 5.6 + bob, 2.3, 1.6))
    c.paint(BODY, cat_leg(shoulder, 0, False))
    c.paint(BODY, cat_leg(hip, 0.5, True))
    # 동그란 머리 + 뾰족 귀 (귀 안쪽은 밝게)
    c.paint(BODY, ellipse(23.8, 10.8 + hb, 4.9, 4.3))
    c.paint(BODY, triangle((20.3, 12.8 + hb), (21, 17.4 + hb), (23.6, 14.6 + hb)))
    c.paint(BODY, triangle((24.2, 14.8 + hb), (26.6, 17.4 + hb), (27.4, 12.6 + hb)))
    c.paint(LIGHT, triangle((21.4, 14 + hb), (21.6, 16.3 + hb), (22.8, 14.8 + hb)))
    c.paint(LIGHT, triangle((25, 14.8 + hb), (26.3, 16.3 + hb), (26.5, 13.8 + hb)))
    c.paint(STRIPE, capsule((23.4, 15.2 + hb), (23.4, 14 + hb), 0.5))
    c.paint(LIGHT, ellipse(27, 8.6 + hb, 1.6, 1.1))
    if stress:
        c.paint(TONGUE, capsule((27.3, 8 + hb), (27.2, 7.1 + hb), 0.65))
    c.paint(BLUSH, ellipse(24.9, 9 + hb, 1.1, 0.65))
    c.paint(DARK, ellipse(25.8, 11.3 + hb, 1.05, 1.6))
    c.dot(HOLE, (25.5, 12 + hb))
    c.paint(DARK, ellipse(28.4, 10 + hb, 0.6, 0.5))
    c.paint(WHISKER, capsule((29, 9.4 + hb), (31.3, 9.9 + hb), 0.5), only_empty=True)


def monkey_frame(c, t, stress):
    """앉아서 머리를 벅벅 긁는 원숭이. 바쁠수록 더 빨리 긁음."""
    w = 2 * math.pi * t
    scratch = math.sin(2 * w)
    hb = 0.35 * math.sin(2 * w + 0.5)

    # 돌돌 말린 꼬리
    c.paint(BODY, curve([(13, 2.5), (18.6, 2.5), (18.6, 8), (16.3 + 0.4 * math.sin(w), 8.3)], 0.9, 0.7))
    # 앉은 몸통 + 밝은 배 + 발
    c.paint(BODY, ellipse(10, 5, 4.6, 4))
    c.paint(LIGHT, ellipse(10, 4.5, 2.9, 2.6))
    c.paint(BODY, ellipse(6.8, 1.3, 1.9, 1))
    c.paint(BODY, ellipse(13.2, 1.3, 1.9, 1))
    # 배 위에 얹은 손
    c.paint(BODY, capsule((6.2, 7.2), (8.2, 4.2), 1.1))
    # 귀 (머리 뒤)
    for ex in (4.7, 15.3):
        c.paint(BODY, ellipse(ex, 11.4 + hb, 1.8, 1.8))
        c.paint(LIGHT, ellipse(ex, 11.4 + hb, 0.9, 1))
    # 큰 머리 + 하트 모양 얼굴
    c.paint(BODY, ellipse(10, 11.6 + hb, 5, 4.5))
    c.paint(LIGHT, union(ellipse(8.6, 12.2 + hb, 2, 2.2), ellipse(11.4, 12.2 + hb, 2, 2.2), ellipse(10, 9.9 + hb, 3, 1.9)))
    c.paint(BLUSH, ellipse(7.3, 10.3 + hb, 0.8, 0.5))
    c.paint(BLUSH, ellipse(12.7, 10.3 + hb, 0.8, 0.5))
    for ex in (8.6, 11.4):
        c.paint(DARK, ellipse(ex, 12.3 + hb, 0.8, 1.1))
        c.dot(HOLE, (ex - 0.3, 12.8 + hb))
    c.paint(DARK, capsule((9.3, 9.3 + hb), (10.7, 9.3 + hb), 0.45))
    # 머리 긁는 팔: 팔꿈치를 들고 손이 정수리를 좌우로 벅벅
    shoulder, elbow = (13.4, 7.3), (18, 11.5 + 0.3 * scratch)
    hand = (12.6 + 1.2 * scratch, 16.4 + hb)
    c.paint(BODY, union(capsule(shoulder, elbow, 1.25), capsule(elbow, hand, 1.15), ellipse(hand[0], hand[1], 1.6, 1.2)))
    if stress:
        c.paint(SWEAT, union(ellipse(2.6, 14, 1.1, 1.1), triangle((1.6, 14.3), (3.6, 14.3), (2.6, 16.6))))


def dancer_frame(c, t, stress):
    """뒤돌아서 엉덩이를 좌우로 흔드는 아이. 바쁠수록 더 빨리 흔듦."""
    w = 2 * math.pi * t
    s = math.sin(w)
    bounce = 0.4 * abs(math.cos(w))
    hx = 10 + 1.7 * s  # 엉덩이 중심
    tx = 10 + 0.5 * s  # 상체는 덜 움직임

    # 다리 + 신발
    for side, foot in ((-1.0, 7.6), (1.0, 12.4)):
        c.paint(SKIN, capsule((hx + side * 1.7, 5.2 + bounce), (foot, 1.8), 1.35), new_part=True)
        c.paint(HAIR, ellipse(foot + side * 0.3, 1, 1.6, 0.9), new_part=True)
    # 상체(셔츠)는 엉덩이 반대쪽으로 살짝 기울어짐
    c.paint(SHIRT, ellipse(tx, 10.4 + bounce, 3.3, 2.7, tilt=-0.2 * s), new_part=True)
    # 신나게 흔드는 두 팔
    for side in (-1.0, 1.0):
        hand = (10 + side * 6.3, 12.8 - side * 1.6 * s + bounce)
        c.paint(SKIN, union(capsule((tx + side * 2.4, 11 + bounce), hand, 1.25), ellipse(hand[0], hand[1], 1.5, 1.5)), new_part=True)
    # 반바지 엉덩이 (볼기짝 두 개)
    c.paint(PANTS, ellipse(hx - 1.9, 6.8 + bounce, 2.7, 2.5), new_part=True)
    c.paint(PANTS, ellipse(hx + 1.9, 6.8 + bounce, 2.7, 2.5), new_part=True)
    # 뒤통수: 양쪽 귀 + 머리카락
    head_x = tx - 0.3 * s
    c.paint(SKIN, ellipse(head_x - 3.5, 14.2 + bounce, 1, 1.2), new_part=True)
    c.paint(SKIN, ellipse(head_x + 3.5, 14.2 + bounce, 1, 1.2), new_part=True)
    c.paint(HAIR, ellipse(head_x, 14.4 + bounce, 3.6, 3.2), new_part=True)
    if stress:
        # 씰룩씰룩 효과선
        for d in (-1.0, 1.0):
            c.paint(WHISKER, capsule((hx + d * 5.2, 8.6), (hx + d * 6.3, 9.3), 0.45), only_empty=True)
            c.paint(WHISKER, capsule((hx + d * 5.2, 5.2), (hx + d * 6.3, 4.5), 0.45), only_empty=True)


# id: (메뉴 이름, 설계 폭, 프레임 함수)
PETS = {
    "dog": ("강아지", 32, dog_frame),
    "cat": ("고양이", 32, cat_frame),
    "monkey": ("원숭이 (머리 긁기)", 20, monkey_frame),
    "dancer": ("엉덩이 춤", 20, dancer_frame),
}


def render_frames(pet, size, stress):
    _, unit_w, draw = PETS[pet]
    frames = []
    for i in range(FRAME_COUNT):
        c = Canvas(size, unit_w)
        draw(c, i / FRAME_COUNT, stress)
        frames.append(c)
    return frames


def to_image(c, palette=None, mono=(0, 0, 0)):
    """palette가 없으면 mono 색 + 알파로 칠함."""
    img = Image.new("RGBA", (c.w, c.h))
    px = img.load()
    for y in range(c.h):
        for x in range(c.w):
            if palette:
                col = c.color(x, y, palette)
                px[x, c.h - 1 - y] = rgb(col) + (255,) if col is not None else (0, 0, 0, 0)
            else:
                px[x, c.h - 1 - y] = mono + (int(c.alpha(x, y) * 255),)
    return img


def write_preview(path, sizes=(16, 24, 32)):
    """캐릭터 x [모노, 치즈, 초코+바쁠 때] 줄마다 8프레임, 크기별로 확대해서 한 장에 저장."""
    zoom, pad, cols = 6, 6, 8
    looks = [(None, False), ("cheese", False), ("choco", True)]
    rows = [(pet, color, stress) for pet in PETS for color, stress in looks]
    cell = max(sizes) * zoom + pad * 2
    sheet = Image.new("RGB", (cell * cols * len(sizes) + pad * len(sizes), cell * len(rows)), (236, 236, 236))
    for r, (pet, color, stress) in enumerate(rows):
        for si, size in enumerate(sizes):
            frames = render_frames(pet, size, stress)
            for col in range(cols):
                img = to_image(frames[col * FRAME_COUNT // cols], PALETTES.get(color))
                big = img.resize((size * max(sizes) * zoom // size,) * 2, Image.NEAREST)
                x = si * (cell * cols + pad) + col * cell + pad
                sheet.paste(big, (x, r * cell + pad), big)
    sheet.save(path)


# MARK: - 사용률 측정

def make_cpu_reader():
    import psutil
    psutil.cpu_percent(None)
    return lambda: psutil.cpu_percent(None)


class GPUReader:
    """Windows 성능 카운터 'GPU Engine' 합계 (작업 관리자의 GPU %와 같은 방식).

    엔진(3D, 복사, 비디오 등)마다 모든 프로세스 사용률을 더하고, 그중 가장 높은 엔진 값을 씀.
    """

    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2

    def __init__(self):
        self.ok = False
        if os.name != "nt":
            return
        from ctypes import wintypes

        class FmtValue(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

        class FmtItem(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", FmtValue)]

        self.FmtItem = FmtItem
        pdh = ctypes.WinDLL("pdh.dll")
        pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        for fn in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW, pdh.PdhCollectQueryData, pdh.PdhGetFormattedCounterArrayW):
            fn.restype = ctypes.c_uint32
        self.pdh = pdh
        self.query, self.counter = ctypes.c_void_p(), ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            return
        # 와일드카드(*)라서 새로 생기는 프로세스/엔진도 자동으로 따라감
        if pdh.PdhAddEnglishCounterW(self.query, r"\GPU Engine(*)\Utilization Percentage", 0, ctypes.byref(self.counter)) != 0:
            return
        pdh.PdhCollectQueryData(self.query)
        self.ok = True

    def __call__(self):
        if not self.ok:
            return 0.0
        from ctypes import wintypes
        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return 0.0
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = self.pdh.PdhGetFormattedCounterArrayW(self.counter, self.PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if status != self.PDH_MORE_DATA:
            return 0.0
        buf = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, self.PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return 0.0
        items = ctypes.cast(buf, ctypes.POINTER(self.FmtItem))
        engines = {}
        for i in range(count.value):
            item = items[i]
            if item.FmtValue.CStatus not in (0, 1):
                continue
            # 인스턴스 이름 예: pid_1234_luid_0x0_0xD1F2_phys_0_eng_0_engtype_3D → 엔진 단위로 묶음
            name = item.szName or ""
            start, end = name.find("luid"), name.find("_engtype")
            key = name[start:end] if 0 <= start < end else name
            engines[key] = engines.get(key, 0.0) + item.FmtValue.doubleValue
        return min(100.0, max(engines.values(), default=0.0))


# MARK: - Windows 설정 (테마, 자동 실행, 저장)

SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RunDog", "settings.json")
DEFAULTS = {"cpu": {"pet": "dog", "color": "auto"}, "gpu": {"pet": "cat", "color": "auto"}}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def load_settings():
    settings = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            for source, values in json.load(f).items():
                settings.setdefault(source, {}).update(values)
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def taskbar_is_light():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def launch_command():
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return '"%s" "%s"' % (pythonw if os.path.exists(pythonw) else sys.executable, os.path.abspath(__file__))


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "RunDog")
            return True
    except OSError:
        return False


def set_autostart(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "RunDog", 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, "RunDog")
            except OSError:
                pass


def tray_icon_size():
    """현재 화면 배율에 맞는 트레이 아이콘 크기 (100% 16px, 150% 24px, 200% 32px)."""
    return ctypes.windll.user32.GetSystemMetrics(49) or 16  # SM_CXSMICON


def make_hicon(img):
    """PIL 이미지를 Windows 아이콘 핸들로. 애니메이션 중엔 이 핸들만 갈아 끼움."""
    from pystray._util import win32
    fd, path = tempfile.mkstemp(".ico")
    try:
        with os.fdopen(fd, "wb") as f:
            img.save(f, format="ICO", sizes=[img.size])
        return win32.LoadImage(None, path, win32.IMAGE_ICON, img.width, img.height, win32.LR_LOADFROMFILE)
    finally:
        os.unlink(path)


# MARK: - 트레이 펫

class PetTray:
    """사용률 하나(CPU 또는 GPU)를 맡아서 트레이에서 달리는 한 마리."""

    def __init__(self, app, source, read):
        import pystray
        from pystray._util import win32

        class TrayIcon(pystray.Icon):
            # pystray는 그림을 바꿀 때마다 임시 파일을 써서 느림 → 미리 만든 핸들로 바로 교체
            def show_handle(self, handle):
                if self.visible and self._hwnd:
                    self._message(win32.NIM_MODIFY, win32.NIF_ICON, hIcon=handle)

        self.app, self.source, self.read = app, source, read
        self.label = source.upper()
        self.usage = self.speed = 0.0
        self.lock = threading.Lock()
        self.handles = [[], []]  # [평소, 바쁠 때]
        self.light = taskbar_is_light()
        self.pystray, self.win32 = pystray, win32
        self.icon = TrayIcon("RunDog-" + source, self.first_image(), "%s 사용량" % self.label, self.build_menu())

    @property
    def settings(self):
        return self.app.settings[self.source]

    def frames_for(self, stress):
        palette = PALETTES.get(self.settings["color"])
        mono = (0, 0, 0) if self.light else (255, 255, 255)
        return [to_image(c, palette, mono) for c in render_frames(self.settings["pet"], self.app.icon_size, stress)]

    def first_image(self):
        return self.frames_for(False)[0]

    def rebuild(self):
        new = [[make_hicon(img) for img in self.frames_for(stress)] for stress in (False, True)]
        with self.lock:
            old, self.handles = self.handles, new
        for h in old[0] + old[1]:
            self.win32.DestroyIcon(h)
        # 탐색기가 다시 시작될 때 쓰는 기본 그림도 맞춰 둠
        self.icon.icon = self.first_image()

    def build_menu(self):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu

        def choose(key, value):
            def action(icon, item):
                self.settings[key] = value
                save_settings(self.app.settings)
                self.rebuild()
                icon.update_menu()
            return action

        def checked(key, value):
            return lambda item: self.settings[key] == value

        pets = Menu(*[Item(title, choose("pet", pid), checked=checked("pet", pid), radio=True)
                      for pid, (title, _, _) in PETS.items()])
        colors = Menu(*[Item(title, choose("color", cid), checked=checked("color", cid), radio=True)
                        for cid, title in COLOR_CHOICES])
        def toggle_autostart(icon, item):
            set_autostart(not autostart_enabled())
            icon.update_menu()

        # 사용률은 아이콘에 마우스를 올리면 툴팁으로 보임 (열린 메뉴를 주기적으로 다시 만들면 메뉴가 튐)
        return Menu(
            Item("캐릭터", pets),
            Item("색상", colors),
            Menu.SEPARATOR,
            Item("작업 관리자 열기", lambda icon, item: os.startfile("taskmgr"), default=True),
            Item("로그인 시 자동 실행", toggle_autostart, checked=lambda item: autostart_enabled()),
            Item("종료", lambda icon, item: self.app.quit()),
        )

    def setup(self, icon):
        icon.visible = True
        self.rebuild()
        threading.Thread(target=self.animate, daemon=True).start()

    def sample(self):
        self.usage = self.read()
        self.icon.title = "%s %.0f%%" % (self.label, self.usage)
        light = taskbar_is_light()
        if light != self.light:
            self.light = light
            if self.settings["color"] == "auto":
                self.rebuild()

    def animate(self):
        """사용률이 높을수록 한 바퀴(16프레임) 도는 시간이 짧아짐: 0% 1.6초 → 100% 약 0.12초.
        속도는 서서히 따라가고, 너무 빠르면 초당 20장으로 제한하고 프레임을 건너뜀."""
        position = 0.0
        while self.app.running:
            cycle = 1.6 / (1 + self.speed / 8)
            interval = max(1 / 20, cycle / FRAME_COUNT)
            self.speed += (self.usage - self.speed) * min(1.0, interval * 2)
            position = (position + interval / cycle * FRAME_COUNT) % FRAME_COUNT
            with self.lock:
                frames = self.handles[1 if self.speed > 40 else 0]
                if frames:
                    self.icon.show_handle(frames[int(round(position)) % FRAME_COUNT])
            time.sleep(interval)


class App:
    def __init__(self):
        self.settings = load_settings()
        self.icon_size = tray_icon_size()
        self.running = True
        self.pets = [PetTray(self, "cpu", make_cpu_reader()), PetTray(self, "gpu", GPUReader())]

    def sample_loop(self):
        while self.running:
            time.sleep(1.5)
            for pet in self.pets:
                pet.sample()

    def quit(self):
        self.running = False
        for pet in self.pets:
            pet.icon.stop()

    def run(self):
        threading.Thread(target=self.sample_loop, daemon=True).start()
        # 하나는 별도 스레드, 마지막 하나는 메인 스레드에서 메시지 루프를 돌림
        for pet in self.pets[:-1]:
            pet.icon.run_detached(pet.setup)
        self.pets[-1].icon.run(self.pets[-1].setup)


def selftest():
    """CI(Windows)에서 그리기·아이콘 핸들·측정이 동작하는지 확인."""
    for pet in PETS:
        for size in (16, 24, 32):
            for stress in (False, True):
                assert len(render_frames(pet, size, stress)) == FRAME_COUNT
    img = to_image(render_frames("dog", 24, False)[0], PALETTES["cheese"])
    handle = make_hicon(img)
    assert handle, "아이콘 핸들 생성 실패"
    print("icon size", tray_icon_size(), "hicon ok")
    cpu, gpu = make_cpu_reader(), GPUReader()
    time.sleep(1)
    print("cpu %.1f%%  gpu %.1f%% (counter ok: %s)" % (cpu(), gpu(), gpu.ok))
    print("selftest ok")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--preview":
        write_preview(sys.argv[2])
        return
    if os.name != "nt":
        sys.exit("RunDog Windows 버전은 Windows에서 실행하세요. (미리보기: --preview out.png)")
    # 화면 배율에 맞는 선명한 아이콘 크기를 받으려면 DPI 인식을 켜야 함
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        selftest()
        return
    App().run()


if __name__ == "__main__":
    main()
