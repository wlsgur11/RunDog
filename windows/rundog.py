"""RunDog for Windows: CPU·GPU 사용률에 맞춰 작업 표시줄 트레이에서 달리는 픽셀 펫.

Mac 버전(mac/main.swift)과 같은 그림 로직을 파이썬으로 옮긴 것.
손으로 프레임을 찍는 대신 몸통·머리·다리를 도형으로 두고 관절 각도를 돌려가며 그리기 때문에,
트레이 아이콘 크기(16~32px)에 맞춰 그 크기 그대로 픽셀을 찍는다.

    python rundog.py                      # 트레이 앱 실행 (Windows)
    python rundog.py --preview out.png    # 모든 캐릭터 미리보기 시트 저장 (어느 OS든)
    python rundog.py --selftest           # 그리기·아이콘·CPU/GPU 측정 점검 (Windows)
    RunDog.exe --update-test              # 최신 릴리스를 받아 exe를 바꿔치기만 하고 종료 (CI 점검용)
"""

import ctypes
import json
import math
import os
import random
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager

from PIL import Image

FRAME_COUNT = 16
SMOOTH_FRAME_COUNT = 32  # 견종 강아지는 입체로 그리면 움직임이 잘 보여서 두 배로 촘촘하게
UNIT_H = 18  # 캐릭터 설계 높이 (Mac 메뉴 바 기준 단위)

# MARK: - 픽셀 캔버스

# 픽셀마다 어떤 재질인지 기록해 두고, 이미지로 바꿀 때 팔레트(또는 모노 알파)로 색을 정함
(NONE, FAR, BODY, LIGHT, EAR, STRIPE, BLUSH, DARK, HOLE, TONGUE, WHISKER, SWEAT,
 SKIN, SHIRT, PANTS, HAIR, GREEN, JEANS, TAN, HELMET) = range(20)
OPEN_INKS = (NONE, FAR, WHISKER, SWEAT)
GLOSSY = (DARK, SWEAT, TONGUE, HOLE)  # 입체 스타일에서 반짝이는 재질 (눈, 코, 혀, 리본)


class Shape:
    """test(x, y)로 안쪽인지 판단하는 도형. bbox로 검사할 픽셀 범위를 줄임.
    h(x, y)는 입체 스타일에서 쓰는 볼록한 높이 (안쪽이면 +, 없으면 납작하게 칠함)."""

    def __init__(self, test, bbox, h=None):
        self.test = test
        self.bbox = bbox
        self.h = h


class Canvas:
    """size x size 픽셀 격자. 캐릭터 단위 좌표의 상자 box(x0, y0, x1, y1)를 가운데에 맞춰 축소/확대해서 찍음."""

    def __init__(self, size, box):
        x0, y0, x1, y1 = box
        self.w = self.h = size
        self.scale = size / max(x1 - x0, y1 - y0)
        self.ox = (size / self.scale - (x1 - x0)) / 2 - x0
        self.oy = (size / self.scale - (y1 - y0)) / 2 - y0
        self.ink = [NONE] * (size * size)
        # 몸통·머리·다리 같은 부위 번호. 나중에 그린 부위가 앞에 있어서 경계에 테두리가 생김
        self.part = [0] * (size * size)
        self.parts = 0
        # 입체 스타일용: 칠한 순서대로 (재질, 도형, 모양을 가진 부위인지, 빈 곳만 칠하는지)
        self.ops = []
        # with c.rotated(...) 안에서 그리는 도형은 돌려서 찍음 (고개 갸웃, 엉덩이 실룩, 몸 털기)
        self.rotations = []

    @contextmanager
    def rotated(self, cx, cy, a):
        self.rotations.append((cx, cy, a))
        try:
            yield
        finally:
            self.rotations.pop()

    def paint(self, ink, shape, new_part=None, only_empty=False):
        for cx, cy, a in reversed(self.rotations):
            shape = rotate(shape, cx, cy, a)
        is_part = new_part if new_part is not None else ink in (BODY, FAR)
        # 무늬(밝은 털, 볼터치)는 색만 바꾸고, 부위·앞발·눈·코·혀는 볼록한 모양을 가짐
        self.ops.append((ink, shape, is_part or (new_part is False and ink == BODY) or ink in GLOSSY, only_empty))
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
        for cx, cy, a in reversed(self.rotations):
            c, s = math.cos(a), math.sin(a)
            p = (cx + (p[0] - cx) * c - (p[1] - cy) * s, cy + (p[0] - cx) * s + (p[1] - cy) * c)
        rad = 0.38 if ink == HOLE else 0.45
        self.ops.append((ink, ellipse(p[0], p[1], rad, rad), ink != HOLE, False))
        # 아이콘이 작으면 눈 하이라이트가 눈을 통째로 지워서 생략
        if ink == HOLE and self.scale < 0.7:
            return
        x, y = int((p[0] + self.ox) * self.scale), int((p[1] + self.oy) * self.scale)
        if 0 <= x < self.w and 0 <= y < self.h:
            self.ink[y * self.w + x] = ink

    def center_on(self, x):
        """좌우 대칭 캐릭터용: 가운데 x가 픽셀 경계나 픽셀 한가운데에 오도록 살짝 밀어서 양쪽 눈이 똑같이 찍히게 함."""
        self.ox = round((x + self.ox) * self.scale * 2) / 2 / self.scale - x

    def set_px(self, ink, x, y):
        """픽셀 좌표로 바로 찍기 (배율과 상관없이 1픽셀 선이 필요한 안경테 같은 곳)."""
        if 0 <= x < self.w and 0 <= y < self.h:
            if self.ink[y * self.w + x] == NONE:
                self.part[y * self.w + x] = self.parts
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
        return {LIGHT: 0.12, BLUSH: 0.22, STRIPE: 0.75, EAR: 0.95, HAIR: 0.95, SKIN: 0.3, SHIRT: 0.7,
                PANTS: 0.15, GREEN: 0.55, JEANS: 0.8, TAN: 0.3, HELMET: 0.95}.get(ink, 0.45)

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
                SKIN: 0xFFD9B8, SHIRT: 0xE8453C, PANTS: 0xFFD34D, HAIR: 0x2B1B12, GREEN: 0x1FA463,
                JEANS: 0x3F5E8C, TAN: 0xF2C29C, HELMET: 0x232838}.get(ink, p["fill"])


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
# 견종 강아지의 실제 털색 ("견종 털색"을 고르거나, 자동 색상에서 입체를 켜면 씀)
COATS = {
    "shiba": dict(fill=0xD9813A, outline=0x5A2D12, light=0xFFF5E6, ear=0xF3C9A0, dark=0x1C1410),  # 적갈색 + 하얀 볼
    "corgi": dict(fill=0xE29A4F, outline=0x5E3416, light=0xFFFFFF, ear=0xC9773A, dark=0x1C1410),  # 레드 & 화이트
    "pomeranian": dict(fill=0xF0A04B, outline=0x6A3A12, light=0xFFE7C2, ear=0xD9822F, dark=0x1C1410),  # 오렌지
    "maltese": dict(fill=0xFFFFFF, outline=0x9C8A78, light=0xFFFFFF, ear=0xF4EDE3, dark=0x1A1414),  # 순백색
    "poodle": dict(fill=0xB4602E, outline=0x4E230C, light=0xE4A87A, ear=0x96491F, dark=0x170D08),  # 레드 브라운
    "husky": dict(fill=0x555B66, outline=0x1F2228, light=0xFFFFFF, ear=0x3E434C, dark=0x101114),  # 진회색 + 하얀 가면
}
COLOR_CHOICES = ([("auto", "자동 (작업 표시줄 색)"), ("coat", "견종 털색")] +
                 [(k, v["title"]) for k, v in PALETTES.items()])


def palette_for(pet, color, solid=False):
    """견종 털색(또는 자동 색상의 입체)이면 견종마다 실제 털색. 견종이 아니면 치즈색으로."""
    if color == "coat" or (solid and color == "auto"):
        return COATS.get(pet, PALETTES["cheese"])
    return PALETTES.get(color)


# MARK: - 도형

def ellipse(cx, cy, rx, ry, tilt=0.0):
    c, s = math.cos(tilt), math.sin(tilt)

    def test(x, y):
        dx, dy = x - cx, y - cy
        u, v = dx * c + dy * s, -dx * s + dy * c
        return (u * u) / (rx * rx) + (v * v) / (ry * ry) <= 1

    def h(x, y):
        # 짧은 반지름만큼 솟은 타원체
        dx, dy = x - cx, y - cy
        u, v = dx * c + dy * s, -dx * s + dy * c
        q = 1 - (u * u) / (rx * rx) - (v * v) / (ry * ry)
        return min(rx, ry) * (math.sqrt(q) if q > 0 else -math.sqrt(-q))

    r = max(rx, ry)
    return Shape(test, (cx - r, cy - r, cx + r, cy + r), h)


def capsule(a, b, r):
    (ax, ay), (bx, by) = a, b
    vx, vy = bx - ax, by - ay
    len2 = vx * vx + vy * vy

    def dist2(x, y):
        t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / len2)) if len2 > 0 else 0.0
        dx, dy = x - (ax + vx * t), y - (ay + vy * t)
        return dx * dx + dy * dy

    def h(x, y):
        # 둥근 막대 (원기둥 + 반구)
        q = 1 - dist2(x, y) / (r * r)
        return r * (math.sqrt(q) if q > 0 else -math.sqrt(-q))

    return Shape(lambda x, y: dist2(x, y) <= r * r,
                 (min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r, max(ay, by) + r), h)


def triangle(a, b, c):
    def side(p, q, x, y):
        return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])

    def test(x, y):
        d1, d2, d3 = side(a, b, x, y), side(b, c, x, y), side(c, a, x, y)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    # 가장자리에서 안쪽으로 들어간 거리만큼 둥글게 부풀림 (귀)
    pts = (a, b, c)
    sign = 1 if side(a, b, *c) > 0 else -1
    edges = [(p, (q[0] - p[0]) / math.dist(p, q), (q[1] - p[1]) / math.dist(p, q)) for p, q in zip(pts, pts[1:] + pts[:1])]

    def h(x, y, R=0.9):
        d = min(sign * (ex * (y - p[1]) - ey * (x - p[0])) for p, ex, ey in edges)
        return d if d <= 0 else math.sqrt(R * R - (R - min(d, R)) ** 2)

    xs, ys = (a[0], b[0], c[0]), (a[1], b[1], c[1])
    return Shape(test, (min(xs), min(ys), max(xs), max(ys)), h)


def frame(cx, cy, hw, hh, thickness):
    """속이 빈 사각 테 (안경알 테두리)."""
    def test(x, y):
        dx, dy = abs(x - cx), abs(y - cy)
        return dx <= hw and dy <= hh and (dx > hw - thickness or dy > hh - thickness)

    return Shape(test, (cx - hw, cy - hh, cx + hw, cy + hh))


def union(*shapes):
    def test(x, y):
        for s in shapes:
            x0, y0, x1, y1 = s.bbox
            if x0 <= x <= x1 and y0 <= y <= y1 and s.test(x, y):
                return True
        return False

    def h(x, y):
        return max((s.h(x, y) for s in shapes if s.h and s.bbox[0] - 0.1 <= x <= s.bbox[2] + 0.1
                    and s.bbox[1] - 0.1 <= y <= s.bbox[3] + 0.1), default=-9.0)

    return Shape(test, (min(s.bbox[0] for s in shapes), min(s.bbox[1] for s in shapes),
                        max(s.bbox[2] for s in shapes), max(s.bbox[3] for s in shapes)), h)


def intersect(a, b):
    return Shape(lambda x, y: a.test(x, y) and b.test(x, y),
                 (max(a.bbox[0], b.bbox[0]), max(a.bbox[1], b.bbox[1]), min(a.bbox[2], b.bbox[2]), min(a.bbox[3], b.bbox[3])),
                 (lambda x, y: min(a.h(x, y), b.h(x, y))) if a.h and b.h else None)


def fluff(cx, cy, rx, ry, bumps, r):
    """몽글몽글한 털 뭉치: 타원 둘레에 작은 원을 빙 둘러 붙임.
    입체로는 알갱이마다 볼록하면 구슬 목걸이처럼 보여서, 전체를 큰 돔 하나로 보고 가장자리만 몽글하게."""
    s = union(ellipse(cx, cy, rx, ry), *[ellipse(cx + rx * math.cos(2 * math.pi * k / bumps),
                                                 cy + ry * math.sin(2 * math.pi * k / bumps), r, r) for k in range(bumps)])
    dome, bumpy = ellipse(cx, cy, rx + r, ry + r).h, s.h
    s.h = lambda x, y: max(dome(x, y), 0.5 * bumpy(x, y))
    return s


def rotate(shape, cx, cy, a):
    """(cx, cy)를 중심으로 a만큼 돌린 도형."""
    c, s = math.cos(a), math.sin(a)

    def back(x, y):
        dx, dy = x - cx, y - cy
        return cx + dx * c + dy * s, cy - dx * s + dy * c

    x0, y0, x1, y1 = shape.bbox
    pts = [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x in (x0, x1) for y in (y0, y1)]
    return Shape(lambda x, y: shape.test(*back(x, y)),
                 (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)),
                 (lambda x, y: shape.h(*back(x, y))) if shape.h else None)


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


# 트레이 아이콘은 정사각형이라 Mac 버전보다 몸통을 짧게 그림: 머리는 뒤로(HX), 꼬리는 앞으로(TX) 당김

def dog_frame(c, t, stress):
    HX, TX = -4.0, 3.0
    w = 2 * math.pi * t
    bob = 0.6 * math.sin(2 * w)
    hb = bob + 0.4 * math.sin(2 * w - 0.8)
    wag = 0.9 * math.sin(2 * w)
    shoulder, hip = (17.0, 6.2 + bob), (10.5, 6.2 + bob)

    def dog_leg(root, phase, hind):
        return gallop_leg(root, w, phase, hind, 0.9, 2.6, 2.6, 1.1, (1.4, 0.9))

    c.paint(FAR, dog_leg((shoulder[0] - 1.4, shoulder[1]), 0.2, False))
    c.paint(FAR, dog_leg((hip[0] - 1.4, hip[1]), 0.7, True))
    tail_tip = (5.5 + TX + wag, 14.5 + bob)
    c.paint(BODY, curve([(8 + TX, 9 + bob), (5 + TX, 10.5 + bob), tail_tip], 1.4, 1))
    c.paint(LIGHT, ellipse(tail_tip[0], tail_tip[1] - 0.3, 1.2, 1.2))
    # 동글동글한 몸통 + 밝은 가슴털
    c.paint(BODY, ellipse(13.7, 7.9 + bob, 5.2, 3.5, tilt=0.05 * math.sin(w)))
    c.paint(LIGHT, ellipse(17, 6.6 + bob, 2.2, 1.6))
    c.paint(BODY, dog_leg(shoulder, 0, False))
    c.paint(BODY, dog_leg(hip, 0.5, True))
    # 큰 머리 + 밝은 주둥이
    c.paint(BODY, ellipse(23.3 + HX, 11.4 + hb, 5.3, 4.8))
    c.paint(LIGHT, ellipse(27.6 + HX, 9.6 + hb, 2.4, 1.7))
    if stress:
        c.paint(TONGUE, capsule((27.3 + HX, 8.4 + hb), (26.9 + HX, 6.9 + hb), 0.85))
    # 축 처진 귀, 달릴 때 살짝 펄럭임
    c.paint(EAR, curve([(22.3 + HX, 15.4 + hb), (19.3 + HX, 15.1 + hb), (19.1 + HX + 0.7 * math.sin(2 * w - 1.2), 10.8 + hb)], 1.6, 1.2))
    c.paint(BLUSH, ellipse(24.4 + HX, 9.6 + hb, 1.1, 0.65))
    c.paint(DARK, ellipse(25.6 + HX, 12.1 + hb, 1.05, 1.5))
    c.dot(HOLE, (25.3 + HX, 12.8 + hb))
    c.paint(DARK, ellipse(29.4 + HX, 10.5 + hb, 1, 0.8))


def cat_frame(c, t, stress):
    HX, TX = -4.0, 3.5
    w = 2 * math.pi * t
    bob = 0.5 * math.sin(2 * w)
    hb = bob + 0.3 * math.sin(2 * w - 0.8)
    stretch = 0.6 * math.sin(w)
    shoulder, hip = (17.3 + stretch, 6 + bob), (9.8 - stretch, 6 + bob)

    def cat_leg(root, phase, hind):
        return gallop_leg(root, w, phase, hind, 1, 2.5, 2.7, 0.95, (1.2, 0.8))

    c.paint(FAR, cat_leg((shoulder[0] - 1.4, shoulder[1]), 0.2, False))
    c.paint(FAR, cat_leg((hip[0] - 1.4, hip[1]), 0.7, True))
    # 길게 S자로 휘는 꼬리 + 줄무늬 꼬리 끝
    tail_tip = (4 + TX + 1.2 * math.sin(w), 15.5 + bob)
    c.paint(BODY, curve([(7.5 + TX, 8 + bob), (2.5 + TX, 8.5 + bob), (1.8 + TX, 13 + bob), tail_tip], 1.2, 0.9))
    c.paint(STRIPE, ellipse(tail_tip[0] - 0.2, tail_tip[1] - 0.4, 1.2, 1.2))
    c.paint(BODY, ellipse(13.5, 7.6 + bob, 5.4 + stretch * 0.6, 3.1, tilt=0.05 * math.sin(w)))
    # 등 줄무늬
    for sx in (11, 13.5, 16):
        c.paint(STRIPE, capsule((sx + stretch * 0.5, 10.2 + bob), (sx - 0.5 + stretch * 0.5, 8.9 + bob), 0.6))
    c.paint(LIGHT, ellipse(17.2 + stretch, 6.2 + bob, 2.1, 1.4))
    c.paint(BODY, cat_leg(shoulder, 0, False))
    c.paint(BODY, cat_leg(hip, 0.5, True))
    # 동그란 머리 + 뾰족 귀 (귀 안쪽은 밝게)
    c.paint(BODY, ellipse(23.8 + HX, 10.8 + hb, 4.9, 4.3))
    c.paint(BODY, triangle((20.3 + HX, 12.8 + hb), (21 + HX, 17.4 + hb), (23.6 + HX, 14.6 + hb)))
    c.paint(BODY, triangle((24.2 + HX, 14.8 + hb), (26.6 + HX, 17.4 + hb), (27.4 + HX, 12.6 + hb)))
    c.paint(LIGHT, triangle((21.4 + HX, 14 + hb), (21.6 + HX, 16.3 + hb), (22.8 + HX, 14.8 + hb)))
    c.paint(LIGHT, triangle((25 + HX, 14.8 + hb), (26.3 + HX, 16.3 + hb), (26.5 + HX, 13.8 + hb)))
    c.paint(STRIPE, capsule((23.4 + HX, 15.2 + hb), (23.4 + HX, 14 + hb), 0.5))
    c.paint(LIGHT, ellipse(27 + HX, 8.6 + hb, 1.6, 1.1))
    if stress:
        c.paint(TONGUE, capsule((27.3 + HX, 8 + hb), (27.2 + HX, 7.1 + hb), 0.65))
    c.paint(BLUSH, ellipse(24.9 + HX, 9 + hb, 1.1, 0.65))
    c.paint(DARK, ellipse(25.8 + HX, 11.3 + hb, 1.05, 1.6))
    c.dot(HOLE, (25.5 + HX, 12 + hb))
    c.paint(DARK, ellipse(28.4 + HX, 10 + hb, 0.6, 0.5))
    c.paint(WHISKER, capsule((29 + HX, 9.4 + hb), (31.3 + HX, 9.9 + hb), 0.5), only_empty=True)


def horse_frame(c, t, stress):
    """옛날 택시 미터기 화면에서 달리던 말. 다리가 길쭉해서 네 발이 하나씩 따로 보이는 갤럽."""
    HX, TX = -3.0, 3.0
    w = 2 * math.pi * t
    bob = 0.5 * math.sin(2 * w)
    hb = bob + 0.4 * math.sin(2 * w - 1)
    flow = math.sin(2 * w - 1.5)
    shoulder, hip = (18.5, 7.4 + bob), (11.6, 7.6 + bob)

    def horse_leg(root, phase, hind):
        return gallop_leg(root, w, phase, hind, 0.8, 3, 3.1, 0.9, (0.95, 0.65))

    c.paint(FAR, horse_leg((shoulder[0] - 1.4, shoulder[1]), 0.2, False))
    c.paint(FAR, horse_leg((hip[0] - 1.4, hip[1]), 0.7, True))
    # 휘날리는 꼬리
    c.paint(EAR, curve([(7 + TX, 10.4 + bob), (4 + TX, 11 + bob + 0.4 * flow), (1.5 + TX, 8.6 + bob + flow)], 1.2, 0.7), new_part=True)
    # 둥근 엉덩이 + 몸통 + 가슴
    c.paint(BODY, union(ellipse(12, 9.5 + bob, 3.7, 3.1), ellipse(15, 9.4 + bob, 4.8, 2.8), ellipse(18.1, 9.2 + bob, 3.1, 3)))
    c.paint(BODY, horse_leg(shoulder, 0, False))
    c.paint(BODY, horse_leg(hip, 0.5, True))
    # 굵은 목 + 길쭉한 머리 + 쫑긋한 귀
    c.paint(BODY, curve([(18.1, 9.8 + bob), (19.3, 12.3 + hb), (23.6 + HX, 13.9 + hb)], 2.3, 1.7))
    c.paint(BODY, union(capsule((24 + HX, 14.2 + hb), (28 + HX, 12 + hb), 1.6), ellipse(28.3 + HX, 11.9 + hb, 1.8, 1.45),
                        triangle((22.9 + HX, 14.8 + hb), (23.4 + HX, 17.2 + hb), (24.7 + HX, 15.3 + hb))))
    # 바람에 날리는 갈기
    c.paint(EAR, curve([(23.6 + HX, 15.6 + hb), (21.2 + HX, 14.4 + hb + 0.4 * flow), (17, 11.8 + bob + 0.6 * flow)], 1.2, 0.8))
    c.paint(DARK, ellipse(25.5 + HX, 13.9 + hb, 0.75, 1))
    c.dot(HOLE, (25.3 + HX, 14.4 + hb))
    c.paint(DARK, ellipse(29.5 + HX, 12.2 + hb, 0.45, 0.45))


# 견종 강아지: 견종마다 실제 강아지가 자주 하는 대표 행동 하나를 반복하고,
# 사이사이 가끔 몸 털기·하품·손!을 함 (EXTRAS). 귀여운 부분은 크게 보이게 견종마다 구도가 다름

def pointy_ears(hx, hy, base, tip, spread, inner=False):
    """머리 양쪽의 뾰족 귀. inner면 그 안쪽의 밝은 귓속."""
    if inner:
        return union(triangle((hx - spread + 0.9, hy + base + 1.3), (hx - spread + 1.3, hy + tip - 1.3), (hx - 2.8, hy + base + 2.2)),
                     triangle((hx + spread - 0.9, hy + base + 1.3), (hx + spread - 1.3, hy + tip - 1.3), (hx + 2.8, hy + base + 2.2)))
    return union(triangle((hx - spread, hy + base), (hx - spread + 0.9, hy + tip), (hx - 1.7, hy + base + 2.1)),
                 triangle((hx + spread, hy + base), (hx + spread - 0.9, hy + tip), (hx + 1.7, hy + base + 2.1)))


def dog_eye(c, ex, ey, kind="open", rx=0.9, ry=1.0, ink=DARK):
    """open: 동그란 눈 + 반짝이, happy: ^^, closed: 감은 눈."""
    if kind == "happy":
        c.paint(DARK, curve([(ex - rx - 0.1, ey - 0.5), (ex, ey + 1.1), (ex + rx + 0.1, ey - 0.5)], 0.38, 0.38), new_part=False)
    elif kind == "closed":
        c.paint(DARK, curve([(ex - rx - 0.1, ey + 0.2), (ex, ey - 0.8), (ex + rx + 0.1, ey + 0.2)], 0.34, 0.34), new_part=False)
    else:
        c.paint(ink, ellipse(ex, ey, rx, ry), new_part=False)
        if ink == SWEAT:  # 허스키 파란 눈의 동공
            c.dot(DARK, (ex + 0.4, ey - 0.4))
        c.dot(HOLE, (ex - rx * 0.45, ey + ry * 0.45))


def w_mouth(c, x, y, s=1.0):
    """ω 입."""
    for sx in (-1, 1):
        c.paint(DARK, curve([(x, y + 0.1), (x + sx * 0.7 * s, y - 0.6 * s), (x + sx * 1.4 * s, y)], 0.24, 0.24), new_part=False)


def breed_tail(c, breed, wag, x=16.2, y=5.8):
    if breed in ("shiba", "husky"):  # 등 위로 돌돌 말린 꼬리
        c.paint(BODY, ellipse(x + 0.8 * wag, y, 2.2, 2))
        c.paint(LIGHT, ellipse(x + 0.2 + 0.8 * wag, y - 0.2, 0.9, 0.8))
    elif breed in ("pomeranian", "maltese"):  # 복슬복슬한 깃털 꼬리
        c.paint(BODY, fluff(x - 0.2 + 0.9 * wag, y + 0.2, 1.6, 1.3, 7, 1))
    elif breed == "poodle":  # 끝에 방울이 달린 꼬리
        c.paint(BODY, union(capsule((x - 2.7, y - 1.8), (x - 0.2 + 0.8 * wag, y + 0.6), 0.6),
                            fluff(x + 0.9 * wag, y + 1, 0.9, 0.9, 6, 0.8)))
    # 코기는 꼬리가 없음


def sitting_body(c, breed, paws=True):
    """앉은 몸통 + 뒷발 (+ 앞발 두 개). 시바·코기·허스키는 흰 가슴·양말."""
    marked = breed in ("shiba", "corgi", "husky")
    for fx in (5.9, 14.1):
        c.paint(BODY, ellipse(fx, 1.4, 1.9, 1.3))
        if marked:
            c.paint(LIGHT, ellipse(fx, 1.3, 1.2, 0.7))
    c.paint(BODY, ellipse(10, 4.4, 4.6, 3.9))
    if marked:
        c.paint(LIGHT, ellipse(10, 4.4, 2.4, 2.8))
    for lx in (8.2, 11.8) if paws else (8.2,):
        c.paint(BODY, union(capsule((lx, 5), (lx, 1.2), 1.05), ellipse(lx, 1.2, 1.3, 0.95)), new_part=False)
        if marked:
            c.paint(LIGHT, ellipse(lx, 1.2, 0.9, 0.6))


def breed_head(c, breed, hx, hy, eyes="open", mouth="closed"):
    """앉은 자세용 정면 머리. 코의 y를 돌려줌 (입을 따로 그릴 때)."""
    eye_y = hy - 0.5
    head = ellipse(hx, hy, 6.1, 4.8)
    if breed in ("shiba", "husky"):
        c.paint(BODY, union(head, pointy_ears(hx, hy, 1.8, 6.6, 5.6)))
        c.paint(LIGHT, pointy_ears(hx, hy, 1.8, 6.6, 5.6, inner=True))
        if breed == "shiba":
            c.paint(LIGHT, intersect(head, ellipse(hx, hy - 2.6, 4.6, 2.6)))
            for sx in (-1, 1):
                c.paint(LIGHT, ellipse(hx + sx * 2.5, eye_y + 2.5, 0.5, 0.5))
        else:
            c.paint(LIGHT, intersect(head, union(ellipse(hx, hy - 2.3, 5.2, 2.8), capsule((hx, hy + 3.4), (hx, hy - 1), 0.7))))
            for sx in (-1, 1):
                c.paint(LIGHT, ellipse(hx + sx * 2.7, hy + 2, 0.9, 0.6))
    elif breed == "corgi":
        c.paint(BODY, union(head, pointy_ears(hx, hy, 1.2, 7.4, 6.1)))
        c.paint(LIGHT, pointy_ears(hx, hy, 1.2, 7.4, 6.1, inner=True))
        c.paint(LIGHT, intersect(head, union(ellipse(hx, hy - 2.5, 3.6, 2.4), capsule((hx, hy + 4.6), (hx, hy - 1), 0.8))))
    elif breed == "pomeranian":
        c.paint(BODY, union(fluff(hx, hy - 0.4, 6, 4.8, 16, 1.2), pointy_ears(hx, hy, 3.4, 6.4, 3.9)))
        c.paint(LIGHT, ellipse(hx, hy - 2.4, 2.6, 1.6))
    elif breed == "maltese":
        for sx in (-1, 1):
            c.paint(BODY, union(capsule((hx + sx * 4.8, hy + 1.6), (hx + sx * 5.9, hy - 3), 2), ellipse(hx + sx * 5.9, hy - 3.5, 2, 1.4)))
        c.paint(BODY, union(ellipse(hx, hy, 5.3, 4.6), ellipse(hx, hy + 4.2, 1.6, 1.2)))
        c.paint(TONGUE, union(triangle((hx - 2.2, hy + 4.4), (hx - 2.2, hy + 6.4), (hx, hy + 5.4)),
                              triangle((hx + 2.2, hy + 4.4), (hx + 2.2, hy + 6.4), (hx, hy + 5.4))))
    elif breed == "poodle":
        c.paint(BODY, fluff(hx, hy + 4.2, 3, 1.8, 9, 1))
        c.paint(BODY, ellipse(hx, hy - 0.2, 5, 4.5))
        for sx in (-1, 1):
            c.paint(EAR, fluff(hx + sx * 5.2, hy - 1.6, 1.3, 2.4, 8, 1), new_part=True)
    for sx in (-1, 1):
        c.paint(BLUSH, ellipse(hx + sx * 5, eye_y - 1.5, 1, 0.5))
        dog_eye(c, hx + sx * 3, eye_y, eyes, ink=SWEAT if breed == "husky" else DARK)
    nose_y = eye_y - 1.5
    c.paint(DARK, ellipse(hx, nose_y, 1, 0.5))
    if mouth == "open":  # 벌린 입 + 혀
        c.paint(DARK, ellipse(hx, nose_y - 1.3, 0.9, 0.75))
        c.paint(TONGUE, ellipse(hx, nose_y - 1.65, 0.65, 0.4))
    elif mouth == "tongue":
        c.paint(TONGUE, capsule((hx, nose_y - 1), (hx, nose_y - 2), 0.75))
    return nose_y


def shiba_frame(c, t, stress):
    """시바: 얼굴을 크게, 헥헥 웃다가(^^) 가끔 동그란 눈으로 쳐다봄. 말린 꼬리가 머리 뒤로 삐죽."""
    w = 2 * math.pi * t
    wag = math.sin(4 * w)
    smile = math.sin(w) > -0.35
    hx, hy = 10.0, 8.6 + (0.4 if math.sin(2 * w) > 0 else 0)
    c.paint(BODY, ellipse(16.4 + 0.7 * wag, 4.2, 2.5, 2.3))
    c.paint(LIGHT, ellipse(16.6 + 0.7 * wag, 4.0, 1.05, 0.95))
    c.paint(BODY, ellipse(10, 1.0, 6.6, 2.8))
    c.paint(LIGHT, ellipse(10, 1.0, 3.4, 2.2))
    head = ellipse(hx, hy, 7.6, 6.0)
    c.paint(BODY, union(head, pointy_ears(hx, hy, 2.4, 8.4, 6.9)))
    c.paint(LIGHT, pointy_ears(hx, hy, 2.4, 8.4, 6.9, inner=True))
    c.paint(LIGHT, intersect(head, ellipse(hx, hy - 3.4, 6.0, 3.6)))  # 우라지로 (하얀 볼·턱)
    for sx in (-1, 1):
        c.paint(LIGHT, ellipse(hx + sx * 3.3, hy + 3.0, 0.75, 0.6))  # 눈썹 점
        c.paint(BLUSH, ellipse(hx + sx * 5.5, hy - 1.4, 1.2, 0.6))
        dog_eye(c, hx + sx * 3.5, hy + 0.6, "happy" if smile else "open", 1.05, 1.2)
    c.paint(DARK, ellipse(hx, hy - 1.2, 1.2, 0.65))
    if stress:
        c.paint(DARK, ellipse(hx, hy - 2.8, 1.3, 1.0))
        c.paint(TONGUE, capsule((hx, hy - 3.2), (hx, hy - 4.6), 0.9))
    elif smile:
        c.paint(DARK, ellipse(hx, hy - 2.8, 1.3, 1.0))
        c.paint(TONGUE, ellipse(hx, hy - 3.2, 0.95, 0.55))
    else:
        w_mouth(c, hx, hy - 1.9)


def corgi_frame(c, t, stress):
    """코기: 뒤돌아 짧은 다리로 종종 걸으며 식빵 엉덩이를 실룩, 가끔 어깨 너머로 돌아봄."""
    w = 2 * math.pi * t
    for k, fx in enumerate((6.4, 13.6)):
        up = 0.7 * max(0.0, math.sin(2 * w + k * math.pi))
        c.paint(BODY, union(capsule((fx, 3), (fx, 0.9 + up), 1.3), ellipse(fx, 0.9 + up, 1.7, 1.1)))
        c.paint(LIGHT, ellipse(fx, 0.8 + up, 1.1, 0.6))
    hx, hy = 10.8, 12.6
    head = ellipse(hx, hy, 4.6, 3.6)
    c.paint(BODY, union(head, pointy_ears(hx, hy, 0.8, 5.2, 4.7)))
    c.paint(LIGHT, pointy_ears(hx, hy, 0.8, 5.2, 4.7, inner=True))
    if math.sin(w) > -0.2:
        c.paint(LIGHT, intersect(head, ellipse(hx + 3.4, hy - 1.4, 2.6, 2)))
        dog_eye(c, hx + 2.4, hy + 0.2, "happy" if stress else "open", 0.8, 0.9)
        c.paint(DARK, ellipse(hx + 4.4, hy - 1.0, 0.65, 0.5))
        c.paint(BLUSH, ellipse(hx + 2.8, hy - 1.4, 0.8, 0.45))
    c.paint(BODY, ellipse(10, 8.2, 5.2, 4.2))  # 등
    with c.rotated(10, 1.2, 0.13 * math.sin(2 * w)):
        for sx in (-1, 1):
            cheek = ellipse(10 + sx * 2.35, 4.6, 3.5, 3.9)
            c.paint(BODY, cheek)
            c.paint(LIGHT, intersect(cheek, fluff(10, 1.6, 5.4, 2.6, 12, 0.9)))  # 하얀 털바지
        c.paint(BODY, ellipse(10, 8.0, 1.0, 0.8), new_part=True)  # 짧은 꽁지
        if stress:
            c.paint(SWEAT, ellipse(15.6, 10.4, 0.6, 0.9), new_part=True)


def pomeranian_frame(c, t, stress):
    """포메: 동그란 솜뭉치가 반가워서 깡총깡총. 착지할 땐 납작, 뛰어오르면 ^^."""
    w = 2 * math.pi * t
    wag = math.sin(4 * w)
    s = abs(math.sin(2 * w))
    hop, squash = 1.8 * s, max(0.0, 1 - 3 * s)
    cx, cy = 10.0, 7.0 + hop
    rx, ry = 6.2 * (1 + 0.1 * squash), 5.6 * (1 - 0.1 * squash)
    c.paint(BODY, fluff(15.4 + 0.8 * wag, cy + 3.4, 1.9, 1.6, 7, 1.1))
    for fx in (7.6, 12.4):
        c.paint(BODY, ellipse(fx, cy - ry - 0.2, 1.4, 0.9))
    c.paint(BODY, union(fluff(cx, cy, rx, ry, 18, 1.3), pointy_ears(cx, cy, ry - 0.6, ry + 3.6, 3.8)))
    c.paint(LIGHT, pointy_ears(cx, cy, ry - 0.6, ry + 3.6, 3.8, inner=True))
    c.paint(LIGHT, ellipse(cx, cy - 1.7, 2.6, 1.8))
    for sx in (-1, 1):
        c.paint(BLUSH, ellipse(cx + sx * 3.9, cy - 1.0, 0.9, 0.5))
        dog_eye(c, cx + sx * 2.3, cy + 0.4, "happy" if hop > 1.2 else "open", 0.85, 0.95)
    c.paint(DARK, ellipse(cx, cy - 1.0, 0.8, 0.45))
    if stress:
        c.paint(TONGUE, capsule((cx, cy - 1.9), (cx, cy - 2.8), 0.6))
    else:
        w_mouth(c, cx, cy - 1.6, 0.7)


def maltese_frame(c, t, stress):
    """말티즈: 큰 얼굴로 소리를 듣고 갸웃갸웃, 긴 귀가 팔랑. 가끔 메롱."""
    w = 2 * math.pi * t
    flap = 0.7 * math.sin(2 * w - 1)
    hx, hy = 10.0, 9.4
    c.paint(BODY, ellipse(10, 1.2, 5.2, 2.6))
    with c.rotated(10, 3.0, 0.15 * max(-1.0, min(1.0, 1.8 * math.sin(w)))):
        for sx in (-1, 1):
            c.paint(BODY, union(capsule((hx + sx * 4.6, hy + 1.8), (hx + sx * 6.4, hy - 5.2 + flap * sx), 2.2),
                                fluff(hx + sx * 6.4, hy - 5.6 + flap * sx, 1.9, 1.3, 7, 0.8)))
        c.paint(BODY, union(ellipse(hx, hy, 6.0, 5.4), fluff(hx, hy + 5.0, 2.2, 1.1, 8, 0.9)))
        c.paint(TONGUE, union(triangle((hx - 2.6, hy + 5.4), (hx - 2.6, hy + 7.8), (hx, hy + 6.6)),
                              triangle((hx + 2.6, hy + 5.4), (hx + 2.6, hy + 7.8), (hx, hy + 6.6))))
        c.paint(TONGUE, ellipse(hx, hy + 6.6, 0.7, 0.7))
        for sx in (-1, 1):
            c.paint(BLUSH, ellipse(hx + sx * 4.4, hy - 1.9, 1.1, 0.55))
            dog_eye(c, hx + sx * 2.8, hy - 0.1, "happy" if math.sin(w) < -0.8 else "open", 1.2, 1.35)
        c.paint(DARK, ellipse(hx, hy - 2.1, 1.0, 0.6))
        if stress or math.sin(w) > 0.7:
            c.paint(TONGUE, capsule((hx, hy - 3.1), (hx, hy - 3.9), 0.7))


def poodle_frame(c, t, stress):
    """푸들: 곰돌이 컷 얼굴로 두 발로 서서 앞발을 모으고 까딱까딱 (앙)."""
    w = 2 * math.pi * t
    wag = math.sin(4 * w)
    bob = 0.6 * math.sin(2 * w)
    flop = 0.4 * math.sin(2 * w - 1.2)
    c.paint(BODY, fluff(14.8 + 0.6 * wag, 4.2, 1.1, 1.1, 7, 0.8))
    for fx in (7.8, 12.2):
        c.paint(BODY, fluff(fx, 1.5, 1.3, 1.1, 7, 0.7))
    c.paint(BODY, fluff(10, 5.4 + bob * 0.3, 3.5, 3.4, 12, 1.0))
    hy = 11.5 + bob
    for sx in (-1, 1):
        c.paint(EAR, fluff(10 + sx * 5.2, hy - 0.8 + flop, 1.5, 2.2, 9, 0.9), new_part=True)
    c.paint(BODY, fluff(10, hy, 4.7, 4.2, 16, 1.0))
    c.paint(LIGHT, ellipse(10, hy - 1.9, 2.3, 1.6))
    for sx in (-1, 1):
        c.paint(BLUSH, ellipse(10 + sx * 3.6, hy - 1.6, 0.9, 0.5))
        dog_eye(c, 10 + sx * 2.2, hy + 0.3, "happy" if bob > 0.45 else "open", 0.8, 0.9)
    c.paint(DARK, ellipse(10, hy - 1.2, 0.85, 0.5))
    if stress:
        c.paint(TONGUE, capsule((10, hy - 2.1), (10, hy - 3.0), 0.6))
    else:
        w_mouth(c, 10, hy - 1.8, 0.65)
    for sx in (-1, 1):
        a, b = (10 + sx * 2.4, 5.6 + bob * 0.5), (10 + sx * 1.0, 7.0 + bob)
        c.paint(BODY, union(capsule(a, b, 0.9), fluff(b[0], b[1], 0.8, 0.7, 6, 0.6)))


def husky_frame(c, t, stress):
    """허스키: 고개를 들고 아우~ 하울링, 사이사이 파란 눈으로 쳐다봄."""
    w = 2 * math.pi * t
    wag = math.sin(4 * w)
    howl = math.sin(w) > 0.1
    c.paint(BODY, union(capsule((14, 3), (16.6 + 0.6 * wag, 6.2), 1.4), ellipse(16.8 + 0.6 * wag, 6.6, 1.6, 1.4)))
    c.paint(LIGHT, ellipse(17.2 + 0.6 * wag, 7.4, 1.0, 0.8))
    sitting_body(c, "husky")
    hx, hy = 10.0, 10.6 + (0.8 if howl else 0)
    head = ellipse(hx, hy, 6.1, 4.8)
    c.paint(BODY, union(head, pointy_ears(hx, hy, 1.8, 6.6, 5.6)))
    c.paint(LIGHT, pointy_ears(hx, hy, 1.8, 6.6, 5.6, inner=True))
    c.paint(LIGHT, intersect(head, union(ellipse(hx, hy - 2.3, 5.2, 2.8), capsule((hx, hy + 3.4), (hx, hy - 1), 0.7))))
    for sx in (-1, 1):
        c.paint(LIGHT, ellipse(hx + sx * 2.7, hy + 2, 0.9, 0.6))
        c.paint(BLUSH, ellipse(hx + sx * 5, hy - 2, 1, 0.5))
    if howl:
        for sx in (-1, 1):
            dog_eye(c, hx + sx * 3, hy - 0.3, "closed")
        c.paint(DARK, ellipse(hx, hy - 1.4, 0.9, 0.5))
        c.paint(DARK, ellipse(hx, hy - 3.0, 1.0, 1.2))
        c.paint(TONGUE, ellipse(hx, hy - 3.6, 0.65, 0.45))
        k = (math.sin(w) - 0.1) / 0.9
        for i, y in enumerate((14.6, 16.4)):  # 아우~ 소리 물결
            if k > 0.25 + 0.3 * i:
                c.paint(WHISKER, curve([(15.6, y), (16.4, y + 0.7), (17.2, y), (18.0, y + 0.7), (18.8, y)], 0.28, 0.28), new_part=False)
    else:
        for sx in (-1, 1):
            dog_eye(c, hx + sx * 3, hy - 0.5, "open", 0.95, 1.05, SWEAT)
        c.paint(DARK, ellipse(hx, hy - 2.0, 1.0, 0.5))
        if stress:
            c.paint(TONGUE, capsule((hx, hy - 3), (hx, hy - 4), 0.75))


# 사이사이 가끔 하는 행동 (모든 견종, 앉은 자세)

def shake_frame(breed, c, t, stress):
    """몸 털기: 앞 절반은 좌우로 부르르 (눈 질끈), 뒤 절반은 개운하게 방긋."""
    w = 2 * math.pi * t
    if t < 0.5:
        a = 0.28 * math.sin(8 * w) * math.sin(2 * w)
        with c.rotated(10, 1.0, a):
            breed_tail(c, breed, math.sin(8 * w))
            sitting_body(c, breed)
            with c.rotated(10, 6.5, 0.8 * a):
                breed_head(c, breed, 10, 10.5, eyes="closed")
        for sx in (-1, 1):  # 휙휙 효과선
            c.paint(WHISKER, curve([(10 + sx * 8.2, 6), (10 + sx * 9.2, 9), (10 + sx * 8.2, 12)], 0.26, 0.26), new_part=False)
    else:
        breed_tail(c, breed, math.sin(4 * w))
        sitting_body(c, breed)
        breed_head(c, breed, 10, 10.5, eyes="happy", mouth="open")


def yawn_frame(breed, c, t, stress):
    """하품: 고개를 살짝 들고 입을 쩍 벌리며 눈을 감았다가 끔뻑."""
    w = 2 * math.pi * t
    k = max(0.0, math.sin(w))
    breed_tail(c, breed, 0.3 * math.sin(4 * w))
    sitting_body(c, breed)
    nose = breed_head(c, breed, 10, 10.5 + 0.6 * k, eyes="closed" if k > 0.3 else "open")
    if k > 0.05:
        c.paint(DARK, ellipse(10, nose - 1.0 - 1.1 * k, 1.1 + 0.5 * k, 0.5 + 1.3 * k))
        c.paint(TONGUE, ellipse(10, nose - 1.2 - 2.0 * k, 0.8 + 0.3 * k, 0.4 + 0.5 * k))


def paw_frame(breed, c, t, stress):
    """손!: 앞발 하나를 앞으로 쑥 내밀어 분홍 젤리를 보여주고 고개를 갸웃."""
    w = 2 * math.pi * t
    k = max(0.0, math.sin(w)) ** 0.6  # 내민 정도
    breed_tail(c, breed, math.sin(4 * w), x=4.0)  # 내미는 발과 겹치지 않게 꼬리는 반대쪽
    sitting_body(c, breed, paws=False)
    with c.rotated(10, 6.5, 0.18 * k):
        breed_head(c, breed, 10, 10.5, mouth="open" if k > 0.5 else "closed")
    px, py, pr = 12.2 + 0.9 * k, 1.4 + 4.6 * k, 1.1 + 1.25 * k
    c.paint(BODY, capsule((11.8, 5), (px, py), 1.0))
    c.paint(BODY, ellipse(px, py, pr * 1.1, pr))
    c.paint(LIGHT, ellipse(px, py, pr * 0.85, pr * 0.78))
    if k > 0.3:
        c.paint(BLUSH, ellipse(px, py - pr * 0.25, pr * 0.42, pr * 0.32))  # 큰 젤리
        for dx in (-0.55, 0.0, 0.55):  # 발가락 젤리
            c.paint(BLUSH, ellipse(px + dx * pr, py + pr * 0.42 - (0.12 * pr if dx == 0 else 0), pr * 0.17, pr * 0.17))


BREEDS = [("shiba", "시바견"), ("corgi", "웰시코기"), ("pomeranian", "포메라니안"),
          ("maltese", "말티즈"), ("poodle", "토이푸들"), ("husky", "허스키")]
BREED_FRAMES = {"shiba": shiba_frame, "corgi": corgi_frame, "pomeranian": pomeranian_frame,
                "maltese": maltese_frame, "poodle": poodle_frame, "husky": husky_frame}
EXTRAS = [("shake", shake_frame), ("yawn", yawn_frame), ("paw", paw_frame)]


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


# 상동호수공원 밈(감스트) 캐릭터 두 가지. 공통: 청록 티셔츠 + 가슴 패치, 남색 반모 헬멧 + 검정 턱끈.
# 바쁠수록 볼살·턱살이 더 빨리 덜덜 떨림

def scooter_frame(c, t, stress):
    """A. 스쿠터 타고 가는 옆모습. 스쿠터는 바퀴만 돌고, 큰 얼굴의 볼살이 떨림. 색상 메뉴는 스쿠터 차체 색."""
    w = 2 * math.pi * t
    amp = 0.75 if stress else 0.5
    r = 0.15 * math.sin(4 * w)            # 몸은 살짝만 출렁
    cheek = amp * math.sin(4 * w + 0.8)   # 볼살은 한 박자 늦게
    jowl = amp * math.sin(4 * w + 1.8)    # 턱살은 한 박자 더 늦게
    spin = -w

    # 배기 연기: 두 덩이가 번갈아 뒤로 퍼짐
    for k in range(2):
        p = (t * 2 + k / 2) % 1
        c.paint(LIGHT, ellipse(5.6 - 4 * p, 3.2 + 2.4 * p, 0.6 + 0.8 * p, 0.6 + 0.8 * p), new_part=True)
    # 바퀴: 타이어 + 휠 + 돌아가는 바큇살
    for wx in (9.0, 21.5):
        c.paint(HAIR, ellipse(wx, 2.6, 2.6, 2.6), new_part=True)
        c.paint(LIGHT, ellipse(wx, 2.6, 1.4, 1.4))
        for a in (spin, spin + math.pi / 2):
            c.paint(HAIR, capsule((wx - math.cos(a) * 1.4, 2.6 - math.sin(a) * 1.4), (wx + math.cos(a) * 1.4, 2.6 + math.sin(a) * 1.4), 0.35))
    # 앞 포크 → 뒤 카울과 발판 → 안장
    c.paint(HAIR, capsule((21.5, 2.6), (20.2, 9.5), 0.6), new_part=True)
    c.paint(BODY, ellipse(11, 5.8, 4.6, 2.3))
    c.paint(BODY, capsule((13, 4.4), (18, 4.4), 0.9))
    c.paint(HAIR, capsule((8.6, 8.3), (13, 8.3), 0.8), new_part=True)
    # 청바지 다리 + 신발
    c.paint(JEANS, union(capsule((12, 8.9 + r), (15.4, 8.8), 1.4), capsule((15.4, 8.8), (16, 5.4), 1.25)), new_part=True)
    c.paint(HAIR, ellipse(16.9, 5, 1.4, 0.8), new_part=True)
    # 앞 가림막 + 헤드라이트 + 핸들
    c.paint(BODY, capsule((18.6, 4.4), (19.8, 9.2), 1.4))
    c.paint(LIGHT, ellipse(21.2, 9, 0.7, 0.9))
    c.paint(HAIR, capsule((18.6, 10.6), (20.8, 10.4), 0.55), new_part=True)
    # 통통한 몸통 + 핸들 잡은 팔
    c.paint(GREEN, ellipse(11.4, 9.8 + r, 3.4, 2.5, tilt=-0.1), new_part=True)
    c.paint(SHIRT, ellipse(13.6, 9.6 + r, 0.7, 0.7))
    c.paint(GREEN, capsule((13.4, 10.8 + r), (15, 10.6 + r), 1.1), new_part=True)
    c.paint(TAN, union(capsule((15, 10.6 + r), (18.8, 10.6), 0.95), ellipse(19, 10.6, 1.15, 1.05)), new_part=True)
    # 큰 얼굴 + 떨리는 볼살·턱살 + 남색 반모 헬멧 + 검정 턱끈
    c.paint(TAN, ellipse(12.8, 13.8 + r, 4.1, 3.6), new_part=True)
    c.paint(TAN, ellipse(13.6, 10.9 + r + jowl * 0.6, 2.6, 1.3), new_part=True)
    c.paint(TAN, ellipse(15.3, 12 + r + cheek, 1.6, 1.4), new_part=True)
    c.paint(BLUSH, ellipse(15.3, 12 + r + cheek, 0.8, 0.5))
    c.paint(HELMET, ellipse(12.2, 16.5 + r, 4.4, 1.4))
    c.paint(HELMET, capsule((10, 15.3 + r), (10.8, 11.6 + r + jowl * 0.6), 0.4))
    # 옆에서 본 뿔테 안경: 밝은 알 + 테 + 점 눈, 귀까지 가는 다리
    c.paint(LIGHT, frame(15.5, 14.5 + r, 1.4, 1.4, 1.4))
    c.paint(HELMET, frame(15.5, 14.5 + r, 1.4, 1.4, 0.5))
    c.paint(DARK, ellipse(15.5, 14.5 + r, 0.45, 0.45))
    c.paint(HELMET, capsule((14, 14.9 + r), (12.2, 15.1 + r), 0.35))
    c.paint(DARK, ellipse(16.6, 12.9 + r + cheek * 0.5, 0.45, 0.4))
    if stress:
        # 바람 가르는 효과선
        for y in (11.0, 14.0):
            c.paint(WHISKER, capsule((1.5, y), (5, y), 0.45), only_empty=True)


def face_glasses(c, cy):
    """검정 뿔테 안경: 렌즈 두 개를 가운데 띄우고 코다리로 연결, 렌즈 안엔 세로로 긴 점 눈 + 반짝이.
    아이콘 배율이 제각각이라 테가 끊기거나 두꺼워지지 않게 픽셀 단위로 좌우 대칭을 맞춰 찍음."""
    s = c.scale
    lw = max(5, int(round(4.8 * s)) | 1)  # 렌즈 폭: 홀수라야 눈이 가운데 옴
    lh = max(4, int(round(3 * s)))
    m2 = int(round((10 + c.ox) * s * 2))   # 얼굴 가운데 (반 픽셀 단위)
    if m2 % 2 == 0:  # 가운데가 픽셀 경계 → 틈도 짝수
        gap = max(2, int(round(2 * s / 2)) * 2)
        left_end = m2 // 2 - gap // 2 - 1
    else:            # 가운데가 픽셀 한가운데 → 틈도 홀수
        gap = max(1, int(round(2 * s)) | 1)
        left_end = (m2 - 1) // 2 - (gap - 1) // 2 - 1
    top = int(round((cy + c.oy) * s + lh / 2)) - 1
    for x0 in (left_end - lw + 1, left_end + gap + 1):
        for x in range(x0, x0 + lw):
            for y in range(top - lh + 1, top + 1):
                if x in (x0, x0 + lw - 1) or y in (top, top - lh + 1):
                    c.set_px(HELMET, x, y)
        eye_x = x0 + lw // 2
        for y in range(top - lh + 2, top):
            if abs(y - (top - (lh - 1) / 2)) <= 1:
                c.set_px(DARK, eye_x, y)
        c.set_px(LIGHT, x0 + 1, top - 1)
    for x in range(left_end + 1, left_end + gap + 1):
        c.set_px(HELMET, x, top - 1)


def face_frame(c, t, stress):
    """B. 영상처럼 셀카 시점의 얼굴 클로즈업: 남색 반모 헬멧(검정 턱끈, 왼쪽 빨간 버클), 청록 티셔츠와 가슴 패치,
    통통한 볼과 이중턱, 가는 눈과 검정 뿔테 안경, 뾰로통 내민 입. 양 볼살과 턱살이 엇갈려 덜덜 떨림."""
    w = 2 * math.pi * t
    amp = 0.8 if stress else 0.5
    head = 0.15 * math.sin(4 * w)
    left = amp * math.sin(4 * w + 0.6)
    right = amp * math.sin(4 * w + 0.6 + math.pi)  # 양 볼이 엇갈려 출렁
    chin = amp * math.sin(4 * w + 1.6)
    chin2 = amp * math.sin(4 * w + 2.4)            # 이중턱은 한 박자 더 늦게
    pout = abs(math.sin(2 * w))                    # 입술을 쭉 내밀었다 오므렸다

    # 어깨: 청록 티셔츠 + 왼쪽 가슴 캐릭터 패치
    c.paint(GREEN, ellipse(10, 0, 9.6, 4.4), new_part=True)
    c.paint(LIGHT, ellipse(5.8, 2, 1.1, 1), new_part=True)
    c.paint(SHIRT, ellipse(5.8, 1.8, 0.5, 0.45))
    # 통통한 얼굴 (넓고 둥글게)
    c.paint(TAN, ellipse(10, 10 + head, 6.7, 5.2), new_part=True)
    # 검정 턱끈: 볼살 뒤로 지나가서 관자놀이와 턱 밑에만 보임
    # (Windows는 렌즈가 커서 렌즈 아래부터 그림. 위쪽은 안경 다리에 가려짐)
    c.paint(HELMET, capsule((4.4, 9.4 + head), (7, 3.8 + head + chin), 0.45))
    c.paint(HELMET, capsule((15.6, 9.4 + head), (13, 3.8 + head + chin), 0.45))
    # 처진 양 볼살 → 턱살 → 이중턱 (볼과 이중턱은 테두리가 생겨서 살 접힘처럼 보임)
    c.paint(TAN, ellipse(4.9, 7.6 + head + left, 2.7, 2.3), new_part=True)
    c.paint(TAN, ellipse(15.1, 7.6 + head + right, 2.7, 2.3), new_part=True)
    c.paint(TAN, ellipse(10, 5.4 + head + chin, 4, 1.8))
    c.paint(TAN, ellipse(10, 3.6 + head + chin2, 3.2, 1.2), new_part=True)
    c.paint(BLUSH, ellipse(5.2, 8.2 + head + left, 1, 0.55))
    c.paint(BLUSH, ellipse(14.8, 8.2 + head + right, 1, 0.55))
    # 왼쪽 턱 아래 빨간 버클
    c.paint(SHIRT, ellipse(6.3, 4.4 + head + chin, 0.8, 0.7), new_part=True)
    # 남색 반모 헬멧: 이마 위만 덮음 + 반짝이
    helmet_y, dome = 13.8 + head, ellipse(10, 13.8 + head, 7.2, 4.2)
    c.paint(HELMET, Shape(lambda x, y: y > helmet_y and dome.test(x, y), dome.bbox), new_part=True)
    c.paint(LIGHT, ellipse(6.8, 16.2 + head, 1, 0.6))
    face_glasses(c, 11 + head)
    # 뾰로통 내민 입술 (테두리 없이 분홍 + 가운데 작게 벌린 입)
    mouth_y = 7.8 + head + chin * 0.3
    c.paint(BLUSH, ellipse(10, mouth_y, 0.9 + 0.3 * pout, 0.6))
    c.paint(DARK, ellipse(10, mouth_y, 0.6, 0.45 + 0.15 * pout))
    if stress:
        # 바람에 날리는 효과선
        for x0, y in ((0.3, 13.0), (0.5, 10.0), (17.4, 13.0), (17.2, 10.0)):
            c.paint(WHISKER, capsule((x0, y), (x0 + 2.2, y), 0.45), only_empty=True)


# id: (메뉴 이름, 설계 상자, 프레임 함수)
PETS = {
    "dog": ("강아지", (0, 0, 32, UNIT_H), dog_frame),
    "cat": ("고양이", (0, 0, 32, UNIT_H), cat_frame),
    "horse": ("택시 미터기 말", (0, 0, 32, UNIT_H), horse_frame),
    **{bid: (title, (0, 0, 20, UNIT_H), BREED_FRAMES[bid]) for bid, title in BREEDS},
    "monkey": ("원숭이 (머리 긁기)", (0, 0, 20, UNIT_H), monkey_frame),
    "dancer": ("엉덩이 춤", (0, 0, 20, UNIT_H), dancer_frame),
    "scooter": ("상동호수공원 (스쿠터)", (0, 0, 32, UNIT_H), scooter_frame),
    "face": ("상동호수공원 (얼굴)", (0, 0, 20, UNIT_H), face_frame),
}

_fit_boxes = {}


def frame_count(pet):
    """한 바퀴의 프레임 수. 견종 강아지만 두 배."""
    return SMOOTH_FRAME_COUNT if pet in dict(BREEDS) else FRAME_COUNT


def extra_draw(breed, kind):
    return lambda c, t, stress: dict(EXTRAS)[kind](breed, c, t, stress)


def fit_box(pet, extras=False):
    """모든 프레임(평소 + 바쁠 때)에서 실제로 칠해지는 범위를 재서, 아이콘을 꽉 채울 상자를 구함.
    프레임마다 따로 맞추면 캐릭터가 출렁이니 전체 범위 하나로 맞춤.
    extras면 그 견종이 가끔 하는 행동 전체를 한 상자로 (행동마다 크기가 달라지지 않게)."""
    if (pet, extras) not in _fit_boxes:
        _, design, draw = PETS[pet]
        draws = [extra_draw(pet, kind) for kind, _ in EXTRAS] if extras else [draw]
        n, xs, ys, count = 64, [], [], frame_count(pet)
        for draw in draws:
            for stress in (False, True):
                for i in range(count):
                    c = Canvas(n, design)
                    draw(c, i / count, stress)
                    for idx, ink in enumerate(c.ink):
                        if ink != NONE:
                            xs.append(idx % n)
                            ys.append(idx // n)
        c, m = Canvas(n, design), 0.2
        _fit_boxes[pet, extras] = (min(xs) / c.scale - c.ox - m, min(ys) / c.scale - c.oy - m,
                                   (max(xs) + 1) / c.scale - c.ox + m, (max(ys) + 1) / c.scale - c.oy + m)
    return _fit_boxes[pet, extras]


def render_frames(pet, size, stress, count=None, extra=None):
    """extra를 주면 견종 강아지가 가끔 하는 행동(shake, yawn, paw)의 프레임. count는 앞에서부터 몇 장만."""
    box, draw = (fit_box(pet, True), extra_draw(pet, extra)) if extra else (fit_box(pet), PETS[pet][2])
    total = frame_count(pet)
    frames = []
    for i in range(total if count is None else count):
        c = Canvas(size, box)
        if pet in dict(BREEDS):
            c.center_on(10)
        draw(c, i / total, stress)
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


# MARK: - 입체 스타일
# 도형마다 볼록한 높이가 있어서, 빛을 받는 쪽은 밝게·반대쪽은 어둡게, 앞 부위는 뒤 부위에 그림자를 드리움.
# 픽셀 하나를 SS x SS로 쪼개 칠해서 가장자리가 부드러움. 테두리 선 대신 가장자리만 살짝 어둡게.

SOLID_PETS = [bid for bid, _ in BREEDS]


def _unit(x, y, z):
    n = math.sqrt(x * x + y * y + z * z)
    return x / n, y / n, z / n


SUN = _unit(-0.4, 0.45, 1.0)  # 왼쪽 위 앞에서 오는 빛 (y는 위쪽이 +)
SUN_HALF = _unit(SUN[0], SUN[1], SUN[2] + 1)


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _solid_sample(ops, x, y, p):
    """(x, y) 지점의 색. 칠한 순서대로 훑어서 맨 앞 재질과 모양을 정한 뒤 빛을 계산."""
    ink, under, surf, front = NONE, NONE, None, 0
    for k, (ik, shape, geom, only_empty) in enumerate(ops):
        x0, y0, x1, y1 = shape.bbox
        if not (x0 <= x <= x1 and y0 <= y <= y1) or not shape.test(x, y):
            continue
        if only_empty and ink != NONE:
            continue
        if geom or ink == NONE:
            surf, front = shape, k + 1
        if ik == BLUSH:
            under = ink
        ink = ik
    if ink == NONE:
        return None
    if ink == HOLE:
        return (255.0, 255.0, 255.0)

    n = (0.0, 0.0, 1.0)
    if surf.h:
        e = 0.04
        n = _unit(-(surf.h(x + e, y) - surf.h(x - e, y)) / (2 * e), -(surf.h(x, y + e) - surf.h(x, y - e)) / (2 * e), 1)
    # 앞에 있는 부위가 빛을 가리면 그림자 (머리 밑 몸통, 앞발 옆 가슴 등)
    shade = 1.0
    for dist in (0.63, 1.4):
        qx, qy = x + SUN[0] * dist, y + SUN[1] * dist
        for ik, shape, geom, _ in ops[front:]:
            x0, y0, x1, y1 = shape.bbox
            if geom and ik not in GLOSSY and x0 <= qx <= x1 and y0 <= qy <= y1 and shape.test(qx, qy):
                shade *= 0.8
                break

    def base(ik):
        return rgb({FAR: darker(p["fill"], 0.72), WHISKER: p["outline"], DARK: 0x24180F, TONGUE: 0xFF6B85,
                    SWEAT: 0x5DB4F5, LIGHT: p["light"], EAR: p["ear"], STRIPE: p["ear"], BLUSH: 0xFF8FA6}.get(ik, p["fill"]))

    col = _mix(base(under), base(BLUSH), 0.75) if ink == BLUSH else base(ink)
    ndl = n[0] * SUN[0] + n[1] * SUN[1] + n[2] * SUN[2]
    ndh = max(0.0, n[0] * SUN_HALF[0] + n[1] * SUN_HALF[1] + n[2] * SUN_HALF[2])
    light = (0.6 + 0.6 * max(0.0, (ndl + 0.25) / 1.25)) * shade
    spec = 0.85 * ndh ** 50 if ink in GLOSSY else 0.1 * ndh ** 12
    col = _mix(tuple(min(255.0, v * light) for v in col), rgb(p["outline"]), (1 - n[2]) ** 2 * 0.35)
    return tuple(min(255.0, v + 255 * spec * shade) for v in col)


def to_solid_image(c, palette, ss=4):
    img = Image.new("RGBA", (c.w, c.h))
    px, s = img.load(), c.scale
    outline = rgb(palette["outline"])
    for py in range(c.h):
        uy0, uy1 = py / s - c.oy, (py + 1) / s - c.oy
        for qx in range(c.w):
            ux0, ux1 = qx / s - c.ox, (qx + 1) / s - c.ox
            # 이 픽셀 근처(그림자 거리 포함)에 걸치는 도형만 추려서 검사 횟수를 줄임
            near = [op for op in c.ops if op[1].bbox[0] <= ux1 + 1.5 and op[1].bbox[2] >= ux0 - 1.5
                    and op[1].bbox[1] <= uy1 + 1.5 and op[1].bbox[3] >= uy0 - 1.5]
            if not near:
                continue
            acc, hits = [0.0, 0.0, 0.0], 0
            for sy in range(ss):
                y = (py + (sy + 0.5) / ss) / s - c.oy
                for sx in range(ss):
                    col = _solid_sample(near, (qx + (sx + 0.5) / ss) / s - c.ox, y, palette)
                    if col:
                        hits += 1
                        acc = [a + v for a, v in zip(acc, col)]
            if hits:
                col = [a / hits for a in acc]
                if hits < ss * ss:  # 바깥 가장자리는 테두리 색을 섞어서 밝은 배경에서도 윤곽이 보이게
                    col = _mix(col, outline, 0.45)
                px[qx, c.h - 1 - py] = tuple(int(v) for v in col) + (255 * hits // (ss * ss),)
    return img


def frame_image(c, pet, color, solid, mono=(0, 0, 0)):
    """설정에 맞는 한 프레임. 입체는 견종 강아지만, 자동 색상이면 견종 털색으로."""
    solid = solid and pet in SOLID_PETS
    palette = palette_for(pet, color, solid)
    return to_solid_image(c, palette) if solid else to_image(c, palette, mono)


def write_preview(path, sizes=(16, 20, 24, 32)):
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
                img = to_image(frames[col * len(frames) // cols], PALETTES.get(color))
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
LOG_PATH = os.path.join(os.path.dirname(SETTINGS_PATH), "error.log")
DEFAULTS = {"cpu": {"pet": "dog", "color": "auto"}, "gpu": {"pet": "cat", "color": "auto"}}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def load_settings():
    settings = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            for source, values in json.load(f).items():
                settings.setdefault(source, {}).update(values)
    except (OSError, ValueError, AttributeError):
        pass
    # 다른 버전이 저장한 모르는 캐릭터·색상이면 기본값으로 (이 버전에 없는 이름 때문에 꺼지지 않게)
    for source, defaults in DEFAULTS.items():
        values = settings[source] if isinstance(settings.get(source), dict) else {}
        if values.get("pet") not in PETS:
            values["pet"] = defaults["pet"]
        if values.get("color") not in dict(COLOR_CHOICES):
            values["color"] = defaults["color"]
        settings[source] = values
    return settings


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def log_error(text):
    """창 없는 exe라 오류가 보이지 않으니 %APPDATA%\\RunDog\\error.log에 남김 (100KB 넘으면 새로 시작)."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        mode = "w" if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 100_000 else "a"
        with open(LOG_PATH, mode, encoding="utf-8") as f:
            f.write("[%s] v%s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), VERSION, text.rstrip()))
    except OSError:
        pass


def wait_for_taskbar(timeout=120):
    """로그인 직후 자동 실행되면 작업 표시줄이 아직 없을 수 있어서, 생길 때까지 기다림."""
    find = ctypes.windll.user32.FindWindowW
    end = time.time() + timeout
    while not find("Shell_TrayWnd", None) and time.time() < end:
        time.sleep(1)


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


def fix_autostart():
    """자동 실행이 다른 곳의 RunDog.exe(예: 예전에 받은 옛 버전)를 가리키면 지금 실행 중인 exe로 바꿈.
    옛 버전은 새 캐릭터를 몰라서 로그인 때 오류로 꺼지고, 자동 업데이트도 못 함."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            command = winreg.QueryValueEx(k, "RunDog")[0]
        if os.path.normcase(command) != os.path.normcase(launch_command()):
            set_autostart(True)
    except OSError:
        pass


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


# MARK: - 자동 업데이트
# exe로 실행 중이면 켤 때와 6시간마다 GitHub의 최신 릴리스를 확인해서, 새 버전이 있으면
# 받아서 exe를 바꿔치기하고 다시 실행함. 실행 중인 exe는 지울 수 없지만 이름은 바꿀 수 있음.

REPO = "wlsgur11/RunDog"
try:
    from _version import VERSION  # CI가 태그 빌드 때 만들어 넣음 (예: "1.4.0")
except ImportError:
    VERSION = "dev"
UPDATE_EVERY = 6 * 3600


def parse_version(v):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return ()


def latest_release():
    """최신 릴리스의 (버전, exe 주소, exe 크기). exe가 아직 안 올라왔으면 None."""
    import urllib.request
    req = urllib.request.Request("https://api.github.com/repos/%s/releases/latest" % REPO,
                                 headers={"User-Agent": "RunDog", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    for asset in data.get("assets", []):
        if asset.get("name") == "RunDog.exe":
            return data["tag_name"].lstrip("v"), asset["browser_download_url"], asset["size"]
    return None


def install_update(exe, url, size):
    """새 exe를 옆에 받아 두고, 실행 중인 exe는 .old로 이름을 바꾼 뒤 그 자리에 넣음."""
    import urllib.request
    new, old = exe + ".new", exe + ".old"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "RunDog"}), timeout=120) as r, \
            open(new, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    if os.path.getsize(new) != size:
        os.remove(new)
        raise OSError("받은 파일 크기가 릴리스와 다름")
    try:
        os.remove(old)
    except OSError:
        pass
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)
        raise


def relaunch(exe, *args):
    """PyInstaller 단일 exe가 다른 단일 exe를 띄울 땐 환경 변수를 초기화해야 새 exe가 자기 파일로 시작함."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([exe, *args], env=env, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def remove_old_exe():
    """업데이트 직후엔 이전 exe가 아직 종료 중일 수 있어서 몇 번 다시 시도."""
    for _ in range(10):
        try:
            os.remove(sys.executable + ".old")
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(1)


def update_test():
    """CI 점검: 최신 릴리스 exe를 받아 지금 실행 중인 exe와 바꿔치기만 하고 종료 코드로 결과를 알림."""
    try:
        version, url, size = latest_release()
        install_update(sys.executable, url, size)
        ok = os.path.getsize(sys.executable) == size and os.path.exists(sys.executable + ".old")
    except Exception:
        ok = False
    sys.exit(0 if ok else 1)


def tray_icon_size():
    """작업 표시줄이 있는 모니터의 배율에 맞는 트레이 아이콘 크기 (100% 16px, 125% 20px, 150% 24px, 200% 32px).
    크기가 어긋나면 Windows가 그림을 늘리거나 줄여서 픽셀이 뭉개짐."""
    user32 = ctypes.windll.user32
    try:
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        dpi = user32.GetDpiForWindow(taskbar) if taskbar else user32.GetDpiForSystem()
        return user32.GetSystemMetricsForDpi(49, dpi) or 16  # SM_CXSMICON
    except AttributeError:  # Windows 10 1607 이전
        return user32.GetSystemMetrics(49) or 16


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

            def _show(self):
                # 로그인 직후엔 작업 표시줄이 바빠서 아이콘 추가가 실패(또는 시간 초과)할 수 있음 → 다시 시도.
                # 시간 초과여도 실제로는 추가됐을 수 있어서, 바꾸기(MODIFY)가 되면 이미 있는 것으로 봄
                for _ in range(30):
                    self._assert_icon_handle()
                    for code in (win32.NIM_ADD, win32.NIM_MODIFY):
                        if win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
                                cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW), hWnd=self._hwnd, hID=id(self),
                                uFlags=win32.NIF_MESSAGE | win32.NIF_ICON | win32.NIF_TIP,
                                uCallbackMessage=win32.WM_NOTIFY, hIcon=self._icon_handle, szTip=self.title)):
                            return
                    time.sleep(1)

        self.app, self.source, self.read = app, source, read
        self.label = source.upper()
        self.usage = self.speed = 0.0
        self.lock = threading.Lock()
        self.handles = [[], []]  # [평소, 바쁠 때]
        self.extra_handles = []  # 견종 강아지가 가끔 하는 행동마다 한 바퀴
        self.light = taskbar_is_light()
        self.pystray, self.win32 = pystray, win32
        self.icon = TrayIcon("RunDog-" + source, self.first_image(), "%s 사용량" % self.label, self.build_menu())

    @property
    def settings(self):
        return self.app.settings[self.source]

    def frames_for(self, stress, count=None, extra=None):
        pet, color = self.settings["pet"], self.settings["color"]
        mono = (0, 0, 0) if self.light else (255, 255, 255)
        return [frame_image(c, pet, color, self.settings.get("solid", False), mono)
                for c in render_frames(pet, self.app.icon_size, stress, count, extra)]

    def first_image(self):
        return self.frames_for(False, 1)[0]

    def rebuild(self):
        new = [[make_hicon(img) for img in self.frames_for(stress)] for stress in (False, True)]
        with self.lock:
            old, self.handles = self.handles, new
            old += self.extra_handles
            self.extra_handles = []
        for h in [h for frames in old for h in frames]:
            self.win32.DestroyIcon(h)
        # 탐색기가 다시 시작될 때 쓰는 기본 그림도 맞춰 둠
        self.icon.icon = self.first_image()
        # 가끔 하는 행동은 기본 동작이 먼저 움직이기 시작한 뒤에 그림 (입체는 몇 초 걸림)
        if self.settings["pet"] in dict(BREEDS):
            extras = [[make_hicon(img) for img in self.frames_for(False, extra=kind)] for kind, _ in EXTRAS]
            with self.lock:
                old, self.extra_handles = self.extra_handles, extras
            for h in [h for frames in old for h in frames]:
                self.win32.DestroyIcon(h)

    def build_menu(self):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu

        def choose(key, value):
            def action(icon, item):
                self.settings[key] = value
                # 견종 강아지를 고를 때 자동(흑백) 색상이면 실제 털색으로 바꿔 줌
                if key == "pet" and value in dict(BREEDS) and self.settings["color"] == "auto":
                    self.settings["color"] = "coat"
                save_settings(self.app.settings)
                self.rebuild()
                icon.update_menu()
            return action

        def checked(key, value):
            return lambda item: self.settings[key] == value

        pet_items = []
        for pid, (title, _, _) in PETS.items():
            # 달리는 동물 / 앉아 있는 강아지들 / 그 밖의 캐릭터로 묶음
            if pid in ("shiba", "monkey"):
                pet_items.append(Menu.SEPARATOR)
            pet_items.append(Item(title, choose("pet", pid), checked=checked("pet", pid), radio=True))
        pets = Menu(*pet_items)
        def toggle_solid(icon, item):
            self.settings["solid"] = not self.settings.get("solid", False)
            save_settings(self.app.settings)
            self.rebuild()
            icon.update_menu()

        # 입체는 견종 강아지만 (자동 색상이면 견종 털색으로 칠함)
        colors = Menu(*[Item(title, choose("color", cid), checked=checked("color", cid), radio=True)
                        for cid, title in COLOR_CHOICES],
                      Menu.SEPARATOR,
                      Item("입체 (견종 강아지)", toggle_solid, checked=lambda item: self.settings.get("solid", False),
                           enabled=lambda item: self.settings["pet"] in SOLID_PETS))
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
            Item("버전 %s (자동 업데이트)" % VERSION, lambda icon, item: None, enabled=False),
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
            if self.settings["color"] == "auto" and not (self.settings.get("solid") and self.settings["pet"] in SOLID_PETS):
                self.rebuild()

    def animate(self):
        """사용률이 높을수록 한 바퀴(16프레임, 견종 강아지는 32프레임) 도는 시간이 짧아짐: 0% 1.6초 → 100% 약 0.27초.
        초당 20장을 넘으면 매번 같은 칸 수(정수)만큼 건너뜀. 칸 수가 들쭉날쭉하거나
        한 번에 너무 많이 건너뛰면 다리가 순간이동하는 것처럼 보여서 최고 속도도 여기에 맞춤."""
        index = 0
        extra, next_extra = None, time.time() + random.uniform(15, 40)
        while self.app.running:
            # 견종 강아지는 15~40초마다 한 번 몸 털기·하품·손! 중 하나를 천천히 한 바퀴 (바쁠 땐 안 함)
            if extra is None and time.time() >= next_extra:
                next_extra = time.time() + random.uniform(15, 40)
                with self.lock:
                    if self.extra_handles and self.speed <= 40:
                        extra, index = random.randrange(len(self.extra_handles)), 0
            if extra is not None:
                with self.lock:
                    frames = self.extra_handles[extra] if extra < len(self.extra_handles) else []
                    if index < len(frames):
                        self.icon.show_handle(frames[index])
                if index < len(frames):
                    index += 1
                    time.sleep(1.6 / len(frames))
                    continue
                extra, index = None, 0
            with self.lock:
                count = len(self.handles[0]) or FRAME_COUNT
            cycle = 1.6 / (1 + self.speed / 20)
            step = max(1, math.ceil(count / 20 / cycle - 1e-9))
            interval = cycle / count * step
            self.speed += (self.usage - self.speed) * min(1.0, interval * 2)
            index = (index + step) % count
            with self.lock:
                frames = self.handles[1 if self.speed > 40 else 0]
                if index < len(frames):
                    self.icon.show_handle(frames[index])
            time.sleep(interval)


class App:
    def __init__(self):
        self.settings = load_settings()
        self.icon_size = tray_icon_size()
        self.running = True
        self.just_updated = "--updated" in sys.argv
        self.pets = [PetTray(self, "cpu", make_cpu_reader()), PetTray(self, "gpu", GPUReader())]

    def update_loop(self):
        if not getattr(sys, "frozen", False):
            return
        remove_old_exe()
        fix_autostart()
        if self.just_updated:
            try:
                self.pets[0].icon.notify("v%s로 업데이트했어요" % VERSION, "RunDog")
            except Exception:
                pass
        if not parse_version(VERSION):  # Actions 아티팩트 같은 개발 빌드는 업데이트하지 않음
            return
        time.sleep(10)
        while self.running:
            try:
                found = latest_release()
                if found and parse_version(found[0]) > parse_version(VERSION):
                    install_update(sys.executable, found[1], found[2])
                    relaunch(sys.executable, "--updated")
                    self.quit()
                    return
            except Exception:  # 인터넷이 끊겼거나 쓰기 권한이 없으면 다음에 다시
                log_error("업데이트 확인 실패\n" + traceback.format_exc())
            time.sleep(UPDATE_EVERY)

    def sample_loop(self):
        while self.running:
            time.sleep(1.5)
            for pet in self.pets:
                pet.sample()
            # 화면 배율을 바꾸면 그 크기에 맞춰 다시 그림
            size = tray_icon_size()
            if size != self.icon_size:
                self.icon_size = size
                for pet in self.pets:
                    pet.rebuild()

    def quit(self):
        self.running = False
        for pet in self.pets:
            try:
                pet.icon.stop()
            except Exception:
                pass

    def run(self):
        threading.Thread(target=self.sample_loop, daemon=True).start()
        threading.Thread(target=self.update_loop, daemon=True).start()
        # 하나는 별도 스레드, 마지막 하나는 메인 스레드에서 메시지 루프를 돌림
        for pet in self.pets[:-1]:
            pet.icon.run_detached(pet.setup)
        self.pets[-1].icon.run(self.pets[-1].setup)


def selftest():
    """CI(Windows)에서 그리기·아이콘 핸들·측정이 동작하는지 확인."""
    for pet in PETS:
        for size in (16, 24, 32):
            for stress in (False, True):
                assert len(render_frames(pet, size, stress)) == frame_count(pet)
    img = to_image(render_frames("dog", 24, False)[0], PALETTES["cheese"])
    handle = make_hicon(img)
    assert handle, "아이콘 핸들 생성 실패"
    print("icon size", tray_icon_size(), "hicon ok")
    for pet in SOLID_PETS:
        assert frame_image(render_frames(pet, 24, True, 1)[0], pet, "auto", True).getbbox(), "입체 그리기 실패"
        for kind, _ in EXTRAS:
            assert len(render_frames(pet, 16, False, extra=kind)) == SMOOTH_FRAME_COUNT
    print("solid, extras ok")
    cpu, gpu = make_cpu_reader(), GPUReader()
    time.sleep(1)
    print("cpu %.1f%%  gpu %.1f%% (counter ok: %s)" % (cpu(), gpu(), gpu.ok))
    print("version", VERSION, "latest release", latest_release()[0])
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
    if len(sys.argv) >= 2 and sys.argv[1] == "--update-test":
        update_test()
    # 오류 창 대신 로그를 남기고, 켜자마자 실패하면(로그인 직후 등) 잠시 뒤 다시 시작
    threading.excepthook = lambda a: log_error("".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    wait_for_taskbar()
    for _ in range(5):
        app = None
        try:
            app = App()
            app.run()
            return
        except Exception:
            log_error(traceback.format_exc())
            if app:
                app.quit()
            time.sleep(10)


if __name__ == "__main__":
    main()
