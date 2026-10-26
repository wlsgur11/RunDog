import AppKit
import IOKit
import ServiceManagement

// MARK: - 픽셀 캔버스
// 손으로 프레임을 찍는 대신, 몸통·머리·다리를 도형으로 두고 관절 각도를 돌려가며
// 작은 픽셀 격자에 그린다. 그래서 프레임 수를 늘려도 동작이 자연스럽게 이어진다.

let gridH = 18
let frameCount = 16

// 픽셀마다 어떤 재질인지 기록해 두고, 이미지로 바꿀 때 팔레트(또는 모노 알파)로 색을 정함
enum Ink: UInt8 {
    case none, far, body, light, ear, stripe, blush, dark, hole, tongue, whisker, sweat
    case skin, shirt, pants, hair, green, jeans, tan, helmet
}

struct P {
    var x, y: Double
    init(_ x: Double, _ y: Double) { self.x = x; self.y = y }
}

typealias Shape = (Double, Double) -> Bool

struct Canvas {
    let w: Int
    let h = gridH
    var ink: [Ink]
    // 몸통·머리·다리 같은 부위 번호. 나중에 그린 부위가 앞에 있어서 경계에 테두리가 생김
    var part: [Int]
    var partCount = 0

    init(w: Int) {
        self.w = w
        ink = [Ink](repeating: .none, count: w * gridH)
        part = [Int](repeating: 0, count: w * gridH)
    }

    // 픽셀 중심 좌표로 도형 안쪽을 칠함 (y는 위쪽이 +). 무늬는 아래 부위 번호를 유지
    mutating func paint(_ value: Ink, newPart: Bool? = nil, onlyEmpty: Bool = false, _ shape: Shape) {
        let isPart = newPart ?? (value == .body || value == .far)
        if isPart { partCount += 1 }
        for y in 0..<h {
            for x in 0..<w where shape(Double(x) + 0.5, Double(y) + 0.5) {
                let i = y * w + x
                if onlyEmpty && ink[i] != .none { continue }
                if isPart || ink[i] == .none { part[i] = partCount }
                ink[i] = value
            }
        }
    }

    mutating func dot(_ value: Ink, _ p: P) {
        let x = Int(p.x), y = Int(p.y)
        if (0..<w).contains(x) && (0..<h).contains(y) { ink[y * w + x] = value }
    }

    func at(_ x: Int, _ y: Int) -> Ink {
        (0..<w).contains(x) && (0..<h).contains(y) ? ink[y * w + x] : .none
    }

    // 바깥이나 뒤쪽 부위와 맞닿은 픽셀이면 테두리
    func isEdge(_ x: Int, _ y: Int) -> Bool {
        let me = part[y * w + x]
        return [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)].contains { nx, ny in
            switch at(nx, ny) {
            case .none, .far, .whisker, .sweat: return true
            default: return part[ny * w + nx] < me
            }
        }
    }

    // 모노(템플릿) 모드: 몸 안쪽은 반투명, 테두리는 진하게
    func alpha(_ x: Int, _ y: Int) -> Double {
        let ink = at(x, y)
        switch ink {
        case .none, .hole: return 0
        case .far: return 0.32
        case .whisker: return 0.75
        case .sweat: return 0.6
        case .dark: return 1
        case .tongue: return 0.85
        default:
            if isEdge(x, y) { return 1 }
            switch ink {
            case .light: return 0.12
            case .blush: return 0.22
            case .stripe: return 0.75
            case .ear, .hair: return 0.95
            case .skin: return 0.3
            case .green: return 0.55
            case .tan: return 0.3
            case .helmet: return 0.95
            case .jeans: return 0.8
            case .shirt: return 0.7
            case .pants: return 0.15
            default: return 0.45
            }
        }
    }

    func color(_ x: Int, _ y: Int, _ p: Palette) -> RGB? {
        let ink = at(x, y)
        switch ink {
        case .none: return nil
        case .far: return p.fill.darker(0.72)
        case .whisker: return p.outline
        case .dark: return p.dark
        case .hole: return RGB(0xFFFFFF)
        case .tongue: return RGB(0xFF6B85)
        case .sweat: return RGB(0x7EC8FF)
        default:
            if isEdge(x, y) { return p.outline }
            switch ink {
            case .light: return p.light
            case .ear, .stripe: return p.ear
            case .blush: return RGB(0xFF94A8)
            case .skin: return RGB(0xFFD9B8)
            case .green: return RGB(0x1FA463)
            case .tan: return RGB(0xF2C29C)
            case .helmet: return RGB(0x232838)
            case .jeans: return RGB(0x3F5E8C)
            case .shirt: return RGB(0xE8453C)
            case .pants: return RGB(0xFFD34D)
            case .hair: return RGB(0x2B1B12)
            default: return p.fill
            }
        }
    }
}

// MARK: - 색

struct RGB {
    var r, g, b: UInt8
    init(_ hex: UInt32) { r = UInt8(hex >> 16 & 0xFF); g = UInt8(hex >> 8 & 0xFF); b = UInt8(hex & 0xFF) }
    func darker(_ f: Double) -> RGB {
        var c = self
        c.r = UInt8(Double(r) * f); c.g = UInt8(Double(g) * f); c.b = UInt8(Double(b) * f)
        return c
    }
}

struct Palette {
    let id, title: String
    let fill, outline, light, ear, dark: RGB
}

// 첫 번째(nil)는 색 없이 메뉴 바 글자색을 따라가는 템플릿 모드
let palettes: [Palette?] = [
    nil,
    Palette(id: "cheese", title: "치즈", fill: RGB(0xEBA35C), outline: RGB(0x5B3417), light: RGB(0xFFF3E0), ear: RGB(0xC47836), dark: RGB(0x2B1B12)),
    Palette(id: "cream", title: "크림", fill: RGB(0xF3E6D3), outline: RGB(0x6E5A47), light: RGB(0xFFFFFF), ear: RGB(0xD9B78F), dark: RGB(0x2B2420)),
    Palette(id: "choco", title: "초코", fill: RGB(0x8B5A3C), outline: RGB(0x3A2215), light: RGB(0xF0D2B0), ear: RGB(0x5C3824), dark: RGB(0x1E120B)),
    Palette(id: "black", title: "까망 (턱시도)", fill: RGB(0x3C3C44), outline: RGB(0x141418), light: RGB(0xF2F2F5), ear: RGB(0x25252B), dark: RGB(0x0E0E10)),
    Palette(id: "gray", title: "회색", fill: RGB(0xA8ABB3), outline: RGB(0x44474F), light: RGB(0xF1F1F4), ear: RGB(0x777B85), dark: RGB(0x1C1D22)),
]

// MARK: - 도형

func ellipse(_ cx: Double, _ cy: Double, _ rx: Double, _ ry: Double, tilt: Double = 0) -> Shape {
    let c = cos(tilt), s = sin(tilt)
    return { x, y in
        let dx = x - cx, dy = y - cy
        let u = dx * c + dy * s, v = -dx * s + dy * c
        return (u * u) / (rx * rx) + (v * v) / (ry * ry) <= 1
    }
}

func capsule(_ a: P, _ b: P, _ r: Double) -> Shape {
    return { x, y in
        let vx = b.x - a.x, vy = b.y - a.y
        let len2 = vx * vx + vy * vy
        let t = len2 > 0 ? max(0, min(1, ((x - a.x) * vx + (y - a.y) * vy) / len2)) : 0
        let dx = x - (a.x + vx * t), dy = y - (a.y + vy * t)
        return dx * dx + dy * dy <= r * r
    }
}

func triangle(_ a: P, _ b: P, _ c: P) -> Shape {
    return { x, y in
        func side(_ p: P, _ q: P) -> Double { (q.x - p.x) * (y - p.y) - (q.y - p.y) * (x - p.x) }
        let d1 = side(a, b), d2 = side(b, c), d3 = side(c, a)
        return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))
    }
}

// 속이 빈 사각 테 (안경알 테두리)
func frame(_ cx: Double, _ cy: Double, _ hw: Double, _ hh: Double, thickness: Double) -> Shape {
    return { x, y in
        let dx = abs(x - cx), dy = abs(y - cy)
        return dx <= hw && dy <= hh && (dx > hw - thickness || dy > hh - thickness)
    }
}

func union(_ shapes: [Shape]) -> Shape {
    return { x, y in shapes.contains { $0(x, y) } }
}

// 베지어 곡선을 따라가는 굵기가 줄어드는 선 (꼬리, 귀)
func curve(_ points: [P], from r0: Double, to r1: Double) -> Shape {
    func at(_ t: Double) -> P {
        var q = points
        while q.count > 1 {
            q = zip(q, q.dropFirst()).map { a, b in P(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t) }
        }
        return q[0]
    }
    let n = 24
    let samples = (0...n).map { at(Double($0) / Double(n)) }
    return union((0..<n).map { i in capsule(samples[i], samples[i + 1], r0 + (r1 - r0) * Double(i) / Double(n)) })
}

// 관절에서 아래로 뻗는 2마디 다리. swing은 앞뒤 각도, fold는 무릎이 뒤로 접히는 정도
func leg(_ root: P, upper: Double, lower: Double, swing: Double, fold: Double, r: Double, paw: (Double, Double)) -> Shape {
    let knee = P(root.x + sin(swing) * upper, root.y - cos(swing) * upper)
    let a = swing - fold
    let foot = P(knee.x + sin(a) * lower, knee.y - cos(a) * lower)
    return union([capsule(root, knee, r), capsule(knee, foot, r * 0.9), ellipse(foot.x + 0.4, foot.y, paw.0, paw.1)])
}

// MARK: - 캐릭터
// 각 프레임 함수의 stress는 사용률이 높을 때 쓰는 버전 (혀 내밀기, 땀, 효과선)

// 갤럽: 앞다리 한 쌍과 뒷다리 한 쌍이 반 박자씩 어긋나서 움직임
func gallopLeg(_ root: P, _ w: Double, phase: Double, hind: Bool, amp: Double, upper: Double, lower: Double, r: Double, paw: (Double, Double)) -> Shape {
    let a = w + 2 * .pi * phase
    let rest = hind ? 0.25 : 0
    return leg(root, upper: upper, lower: lower, swing: amp * sin(a) + rest, fold: 0.8 * max(0, cos(a)) + rest * 2, r: r, paw: paw)
}

func dogFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let bob = 0.6 * sin(2 * w)
    let hb = bob + 0.4 * sin(2 * w - 0.8)
    let wag = 0.9 * sin(2 * w)
    let shoulder = P(19.5, 5.6 + bob), hip = P(9.5, 5.6 + bob)
    func dogLeg(_ root: P, _ phase: Double, hind: Bool) -> Shape {
        gallopLeg(root, w, phase: phase, hind: hind, amp: 0.7, upper: 2.2, lower: 2.3, r: 1.3, paw: (1.5, 1))
    }

    var c = Canvas(w: 32)
    c.paint(.far, dogLeg(P(shoulder.x - 1.2, shoulder.y), 0.12, hind: false))
    c.paint(.far, dogLeg(P(hip.x - 1.2, hip.y), 0.62, hind: true))
    let tailTip = P(5.5 + wag, 14.5 + bob)
    c.paint(.body, curve([P(8, 9 + bob), P(5, 10.5 + bob), tailTip], from: 1.4, to: 1))
    c.paint(.light, ellipse(tailTip.x, tailTip.y - 0.3, 1.2, 1.2))
    // 동글동글한 몸통 + 밝은 가슴털
    c.paint(.body, ellipse(14.5, 7.4 + bob, 6.6, 4, tilt: 0.05 * sin(w)))
    c.paint(.light, ellipse(19.2, 6 + bob, 2.4, 1.8))
    c.paint(.body, dogLeg(shoulder, 0, hind: false))
    c.paint(.body, dogLeg(hip, 0.5, hind: true))
    // 큰 머리 + 밝은 주둥이
    c.paint(.body, ellipse(23.3, 11.4 + hb, 5.3, 4.8))
    c.paint(.light, ellipse(27.6, 9.6 + hb, 2.4, 1.7))
    if stress {
        c.paint(.tongue, capsule(P(27.3, 8.4 + hb), P(26.9, 6.9 + hb), 0.85))
    }
    // 축 처진 귀, 달릴 때 살짝 펄럭임
    c.paint(.ear, curve([P(22.3, 15.4 + hb), P(19.3, 15.1 + hb), P(19.1 + 0.7 * sin(2 * w - 1.2), 10.8 + hb)], from: 1.6, to: 1.2))
    c.paint(.blush, ellipse(24.4, 9.6 + hb, 1.1, 0.65))
    c.paint(.dark, ellipse(25.6, 12.1 + hb, 1.05, 1.5))
    c.dot(.hole, P(25.3, 12.8 + hb))
    c.paint(.dark, ellipse(29.4, 10.5 + hb, 1, 0.8))
    return c
}

func catFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let bob = 0.5 * sin(2 * w)
    let hb = bob + 0.3 * sin(2 * w - 0.8)
    let stretch = 0.6 * sin(w)
    let shoulder = P(19.5 + stretch, 5.5 + bob), hip = P(8.8 - stretch, 5.5 + bob)
    func catLeg(_ root: P, _ phase: Double, hind: Bool) -> Shape {
        gallopLeg(root, w, phase: phase, hind: hind, amp: 0.85, upper: 2.2, lower: 2.5, r: 1.05, paw: (1.2, 0.8))
    }

    var c = Canvas(w: 32)
    c.paint(.far, catLeg(P(shoulder.x - 1.2, shoulder.y), 0.12, hind: false))
    c.paint(.far, catLeg(P(hip.x - 1.2, hip.y), 0.62, hind: true))
    // 길게 S자로 휘는 꼬리 + 줄무늬 꼬리 끝
    let tailTip = P(4 + 1.2 * sin(w), 15.5 + bob)
    c.paint(.body, curve([P(7.5, 8 + bob), P(2.5, 8.5 + bob), P(1.8, 13 + bob), tailTip], from: 1.2, to: 0.9))
    c.paint(.stripe, ellipse(tailTip.x - 0.2, tailTip.y - 0.4, 1.2, 1.2))
    c.paint(.body, ellipse(14, 7.2 + bob, 7 + stretch, 3.4, tilt: 0.05 * sin(w)))
    // 등 줄무늬
    for sx in [10.5, 13.5, 16.5] {
        c.paint(.stripe, capsule(P(sx + stretch * 0.5, 10.2 + bob), P(sx - 0.5 + stretch * 0.5, 8.9 + bob), 0.6))
    }
    c.paint(.light, ellipse(19.4 + stretch, 5.6 + bob, 2.3, 1.6))
    c.paint(.body, catLeg(shoulder, 0, hind: false))
    c.paint(.body, catLeg(hip, 0.5, hind: true))
    // 동그란 머리 + 뾰족 귀 (귀 안쪽은 밝게)
    c.paint(.body, ellipse(23.8, 10.8 + hb, 4.9, 4.3))
    c.paint(.body, triangle(P(20.3, 12.8 + hb), P(21, 17.4 + hb), P(23.6, 14.6 + hb)))
    c.paint(.body, triangle(P(24.2, 14.8 + hb), P(26.6, 17.4 + hb), P(27.4, 12.6 + hb)))
    c.paint(.light, triangle(P(21.4, 14 + hb), P(21.6, 16.3 + hb), P(22.8, 14.8 + hb)))
    c.paint(.light, triangle(P(25, 14.8 + hb), P(26.3, 16.3 + hb), P(26.5, 13.8 + hb)))
    c.paint(.stripe, capsule(P(23.4, 15.2 + hb), P(23.4, 14 + hb), 0.5))
    c.paint(.light, ellipse(27, 8.6 + hb, 1.6, 1.1))
    if stress {
        c.paint(.tongue, capsule(P(27.3, 8 + hb), P(27.2, 7.1 + hb), 0.65))
    }
    c.paint(.blush, ellipse(24.9, 9 + hb, 1.1, 0.65))
    c.paint(.dark, ellipse(25.8, 11.3 + hb, 1.05, 1.6))
    c.dot(.hole, P(25.5, 12 + hb))
    c.paint(.dark, ellipse(28.4, 10 + hb, 0.6, 0.5))
    c.paint(.whisker, onlyEmpty: true, capsule(P(29, 9.4 + hb), P(31.3, 9.9 + hb), 0.5))
    return c
}

// 앉아서 머리를 벅벅 긁는 원숭이. 바쁠수록 더 빨리 긁음
func monkeyFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let scratch = sin(2 * w)
    let hb = 0.35 * sin(2 * w + 0.5)

    var c = Canvas(w: 20)
    // 돌돌 말린 꼬리
    c.paint(.body, curve([P(13, 2.5), P(18.6, 2.5), P(18.6, 8), P(16.3 + 0.4 * sin(w), 8.3)], from: 0.9, to: 0.7))
    // 앉은 몸통 + 밝은 배 + 발
    c.paint(.body, ellipse(10, 5, 4.6, 4))
    c.paint(.light, ellipse(10, 4.5, 2.9, 2.6))
    c.paint(.body, ellipse(6.8, 1.3, 1.9, 1))
    c.paint(.body, ellipse(13.2, 1.3, 1.9, 1))
    // 배 위에 얹은 손
    c.paint(.body, capsule(P(6.2, 7.2), P(8.2, 4.2), 1.1))
    // 귀 (머리 뒤)
    for ex in [4.7, 15.3] {
        c.paint(.body, ellipse(ex, 11.4 + hb, 1.8, 1.8))
        c.paint(.light, ellipse(ex, 11.4 + hb, 0.9, 1))
    }
    // 큰 머리 + 하트 모양 얼굴
    c.paint(.body, ellipse(10, 11.6 + hb, 5, 4.5))
    c.paint(.light, union([ellipse(8.6, 12.2 + hb, 2, 2.2), ellipse(11.4, 12.2 + hb, 2, 2.2), ellipse(10, 9.9 + hb, 3, 1.9)]))
    c.paint(.blush, ellipse(7.3, 10.3 + hb, 0.8, 0.5))
    c.paint(.blush, ellipse(12.7, 10.3 + hb, 0.8, 0.5))
    for ex in [8.6, 11.4] {
        c.paint(.dark, ellipse(ex, 12.3 + hb, 0.8, 1.1))
        c.dot(.hole, P(ex - 0.3, 12.8 + hb))
    }
    c.paint(.dark, capsule(P(9.3, 9.3 + hb), P(10.7, 9.3 + hb), 0.45))
    // 머리 긁는 팔: 팔꿈치를 들고 손이 정수리를 좌우로 벅벅
    let shoulder = P(13.4, 7.3), elbow = P(18, 11.5 + 0.3 * scratch)
    let hand = P(12.6 + 1.2 * scratch, 16.4 + hb)
    c.paint(.body, union([capsule(shoulder, elbow, 1.25), capsule(elbow, hand, 1.15), ellipse(hand.x, hand.y, 1.6, 1.2)]))
    if stress {
        c.paint(.sweat, union([ellipse(2.6, 14, 1.1, 1.1), triangle(P(1.6, 14.3), P(3.6, 14.3), P(2.6, 16.6))]))
    }
    return c
}

// 뒤돌아서 엉덩이를 좌우로 흔드는 아이. 바쁠수록 더 빨리 흔듦
func dancerFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let s = sin(w)
    let bounce = 0.4 * abs(cos(w))
    let hx = 10 + 1.7 * s   // 엉덩이 중심
    let tx = 10 + 0.5 * s   // 상체는 덜 움직임

    var c = Canvas(w: 20)
    // 다리 + 신발
    for (side, foot) in [(-1.0, 7.6), (1.0, 12.4)] {
        c.paint(.skin, newPart: true, capsule(P(hx + side * 1.7, 5.2 + bounce), P(foot, 1.8), 1.35))
        c.paint(.hair, newPart: true, ellipse(foot + side * 0.3, 1, 1.6, 0.9))
    }
    // 상체(셔츠)는 엉덩이 반대쪽으로 살짝 기울어짐
    c.paint(.shirt, newPart: true, ellipse(tx, 10.4 + bounce, 3.3, 2.7, tilt: -0.2 * s))
    // 신나게 흔드는 두 팔
    for side in [-1.0, 1.0] {
        let hand = P(10 + side * 6.3, 12.8 - side * 1.6 * s + bounce)
        c.paint(.skin, newPart: true, union([capsule(P(tx + side * 2.4, 11 + bounce), hand, 1.25), ellipse(hand.x, hand.y, 1.5, 1.5)]))
    }
    // 반바지 엉덩이 (볼기짝 두 개)
    c.paint(.pants, newPart: true, ellipse(hx - 1.9, 6.8 + bounce, 2.7, 2.5))
    c.paint(.pants, newPart: true, ellipse(hx + 1.9, 6.8 + bounce, 2.7, 2.5))
    // 뒤통수: 양쪽 귀 + 머리카락
    let headX = tx - 0.3 * s
    c.paint(.skin, newPart: true, ellipse(headX - 3.5, 14.2 + bounce, 1, 1.2))
    c.paint(.skin, newPart: true, ellipse(headX + 3.5, 14.2 + bounce, 1, 1.2))
    c.paint(.hair, newPart: true, ellipse(headX, 14.4 + bounce, 3.6, 3.2))
    if stress {
        // 씰룩씰룩 효과선
        for dir in [-1.0, 1.0] {
            c.paint(.whisker, onlyEmpty: true, capsule(P(hx + dir * 5.2, 8.6), P(hx + dir * 6.3, 9.3), 0.45))
            c.paint(.whisker, onlyEmpty: true, capsule(P(hx + dir * 5.2, 5.2), P(hx + dir * 6.3, 4.5), 0.45))
        }
    }
    return c
}

// 상동호수공원 밈(감스트) 캐릭터 두 가지. 공통: 청록 티셔츠 + 가슴 패치, 남색 반모 헬멧 + 검정 턱끈.
// 바쁠수록 볼살·턱살이 더 빨리 덜덜 떨림

// A. 스쿠터 타고 가는 옆모습. 스쿠터는 바퀴만 돌고, 큰 얼굴의 볼살이 떨림. 색상 메뉴는 스쿠터 차체 색
func scooterFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let amp = stress ? 0.75 : 0.5
    let r = 0.15 * sin(4 * w)                 // 몸은 살짝만 출렁
    let cheek = amp * sin(4 * w + 0.8)        // 볼살은 한 박자 늦게
    let jowl = amp * sin(4 * w + 1.8)         // 턱살은 한 박자 더 늦게
    let spin = -w

    var c = Canvas(w: 32)
    // 배기 연기: 두 덩이가 번갈아 뒤로 퍼짐
    for k in 0..<2 {
        let p = (t * 2 + Double(k) / 2).truncatingRemainder(dividingBy: 1)
        c.paint(.light, newPart: true, ellipse(5.6 - 4 * p, 3.2 + 2.4 * p, 0.6 + 0.8 * p, 0.6 + 0.8 * p))
    }
    // 바퀴: 타이어 + 휠 + 돌아가는 바큇살
    for wx in [9.0, 21.5] {
        c.paint(.hair, newPart: true, ellipse(wx, 2.6, 2.6, 2.6))
        c.paint(.light, ellipse(wx, 2.6, 1.4, 1.4))
        for a in [spin, spin + .pi / 2] {
            c.paint(.hair, capsule(P(wx - cos(a) * 1.4, 2.6 - sin(a) * 1.4), P(wx + cos(a) * 1.4, 2.6 + sin(a) * 1.4), 0.35))
        }
    }
    // 앞 포크 → 뒤 카울과 발판 → 안장
    c.paint(.hair, newPart: true, capsule(P(21.5, 2.6), P(20.2, 9.5), 0.6))
    c.paint(.body, ellipse(11, 5.8, 4.6, 2.3))
    c.paint(.body, capsule(P(13, 4.4), P(18, 4.4), 0.9))
    c.paint(.hair, newPart: true, capsule(P(8.6, 8.3), P(13, 8.3), 0.8))
    // 청바지 다리 + 신발
    c.paint(.jeans, newPart: true, union([capsule(P(12, 8.9 + r), P(15.4, 8.8), 1.4), capsule(P(15.4, 8.8), P(16, 5.4), 1.25)]))
    c.paint(.hair, newPart: true, ellipse(16.9, 5, 1.4, 0.8))
    // 앞 가림막 + 헤드라이트 + 핸들
    c.paint(.body, capsule(P(18.6, 4.4), P(19.8, 9.2), 1.4))
    c.paint(.light, ellipse(21.2, 9, 0.7, 0.9))
    c.paint(.hair, newPart: true, capsule(P(18.6, 10.6), P(20.8, 10.4), 0.55))
    // 통통한 몸통 + 핸들 잡은 팔
    c.paint(.green, newPart: true, ellipse(11.4, 9.8 + r, 3.4, 2.5, tilt: -0.1))
    c.paint(.shirt, ellipse(13.6, 9.6 + r, 0.7, 0.7))
    c.paint(.green, newPart: true, capsule(P(13.4, 10.8 + r), P(15, 10.6 + r), 1.1))
    c.paint(.tan, newPart: true, union([capsule(P(15, 10.6 + r), P(18.8, 10.6), 0.95), ellipse(19, 10.6, 1.15, 1.05)]))
    // 큰 얼굴 + 떨리는 볼살·턱살 + 남색 반모 헬멧 + 검정 턱끈
    c.paint(.tan, newPart: true, ellipse(12.8, 13.8 + r, 4.1, 3.6))
    c.paint(.tan, newPart: true, ellipse(13.6, 10.9 + r + jowl * 0.6, 2.6, 1.3))
    c.paint(.tan, newPart: true, ellipse(15.3, 12 + r + cheek, 1.6, 1.4))
    c.paint(.blush, ellipse(15.3, 12 + r + cheek, 0.8, 0.5))
    c.paint(.helmet, ellipse(12.2, 16.5 + r, 4.4, 1.4))
    c.paint(.helmet, capsule(P(10, 15.3 + r), P(10.8, 11.6 + r + jowl * 0.6), 0.4))
    // 옆에서 본 뿔테 안경: 밝은 알 + 테 + 점 눈, 귀까지 가는 다리
    c.paint(.light, frame(15.5, 14.5 + r, 1.4, 1.4, thickness: 1.4))
    c.paint(.helmet, frame(15.5, 14.5 + r, 1.4, 1.4, thickness: 0.5))
    c.paint(.dark, ellipse(15.5, 14.5 + r, 0.45, 0.45))
    c.paint(.helmet, capsule(P(14, 14.9 + r), P(12.2, 15.1 + r), 0.35))
    c.paint(.dark, ellipse(16.6, 12.9 + r + cheek * 0.5, 0.45, 0.4))
    if stress {
        // 바람 가르는 효과선
        for y in [11.0, 14.0] {
            c.paint(.whisker, onlyEmpty: true, capsule(P(1.5, y), P(5, y), 0.45))
        }
    }
    return c
}

// B. 영상처럼 셀카 시점의 얼굴 클로즈업: 남색 반모 헬멧(검정 턱끈, 왼쪽 빨간 버클), 청록 티셔츠와 가슴 패치,
// 통통한 볼과 이중턱, 가는 눈과 검정 뿔테 안경, 뾰로통 내민 입. 양 볼살과 턱살이 엇갈려 덜덜 떨림
func faceFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let amp = stress ? 0.8 : 0.5
    let head = 0.15 * sin(4 * w)
    let left = amp * sin(4 * w + 0.6)
    let right = amp * sin(4 * w + 0.6 + .pi)  // 양 볼이 엇갈려 출렁
    let chin = amp * sin(4 * w + 1.6)
    let chin2 = amp * sin(4 * w + 2.4)         // 이중턱은 한 박자 더 늦게
    let pout = abs(sin(2 * w))                 // 입술을 쭉 내밀었다 오므렸다

    var c = Canvas(w: 20)
    // 어깨: 청록 티셔츠 + 왼쪽 가슴 캐릭터 패치
    c.paint(.green, newPart: true, ellipse(10, 0, 9.6, 4.4))
    c.paint(.light, newPart: true, ellipse(5.8, 2, 1.1, 1))
    c.paint(.shirt, ellipse(5.8, 1.8, 0.5, 0.45))
    // 통통한 얼굴 (넓고 둥글게)
    c.paint(.tan, newPart: true, ellipse(10, 10 + head, 6.7, 5.2))
    // 검정 턱끈: 볼살 뒤로 지나가서 관자놀이와 턱 밑에만 보임
    c.paint(.helmet, capsule(P(3.8, 13.8 + head), P(7, 3.8 + head + chin), 0.45))
    c.paint(.helmet, capsule(P(16.2, 13.8 + head), P(13, 3.8 + head + chin), 0.45))
    // 처진 양 볼살 → 턱살 → 이중턱 (볼과 이중턱은 테두리가 생겨서 살 접힘처럼 보임)
    c.paint(.tan, newPart: true, ellipse(4.9, 7.6 + head + left, 2.7, 2.3))
    c.paint(.tan, newPart: true, ellipse(15.1, 7.6 + head + right, 2.7, 2.3))
    c.paint(.tan, ellipse(10, 5.4 + head + chin, 4, 1.8))
    c.paint(.tan, newPart: true, ellipse(10, 3.6 + head + chin2, 3.2, 1.2))
    c.paint(.blush, ellipse(5.2, 8.2 + head + left, 1, 0.55))
    c.paint(.blush, ellipse(14.8, 8.2 + head + right, 1, 0.55))
    // 왼쪽 턱 아래 빨간 버클
    c.paint(.shirt, newPart: true, ellipse(6.3, 4.4 + head + chin, 0.8, 0.7))
    // 남색 반모 헬멧: 이마 위만 덮음 + 반짝이
    let helmetY = 13.8 + head
    c.paint(.helmet, newPart: true, { x, y in y > helmetY && ellipse(10, 13.8 + head, 7.2, 4.2)(x, y) })
    c.paint(.light, ellipse(6.8, 16.2 + head, 1, 0.6))
    // 검정 뿔테 안경: 5x4픽셀 테 두 개를 가운데 2픽셀 띄우고 코다리로 연결.
    // 렌즈 안은 피부색에 세로로 긴 점 눈 + 반짝이 한 점 (픽셀 격자에 맞게 x.5 위치)
    for ex in [6.5, 13.5] {
        c.paint(.helmet, frame(ex, 11 + head, 2.4, 1.9, thickness: 0.5))
        c.paint(.dark, ellipse(ex, 11 + head, 0.45, 0.9))
        c.dot(.light, P(ex - 1, 11.5 + head))
    }
    c.paint(.helmet, capsule(P(9, 11.5 + head), P(11, 11.5 + head), 0.4))
    // 뾰로통 내민 입술 (테두리 없이 분홍 + 가운데 작게 벌린 입)
    let mouthY = 7.8 + head + chin * 0.3
    c.paint(.blush, ellipse(10, mouthY, 0.9 + 0.3 * pout, 0.6))
    c.paint(.dark, ellipse(10, mouthY, 0.6, 0.45 + 0.15 * pout))
    if stress {
        // 바람에 날리는 효과선
        for (x0, y) in [(0.3, 13.0), (0.5, 10.0), (17.4, 13.0), (17.2, 10.0)] {
            c.paint(.whisker, onlyEmpty: true, capsule(P(x0, y), P(x0 + 2.2, y), 0.45))
        }
    }
    return c
}

enum Pet: String, CaseIterable {
    case dog, cat, monkey, dancer, scooter, face

    var title: String {
        switch self {
        case .dog: return "강아지"
        case .cat: return "고양이"
        case .monkey: return "원숭이 (머리 긁기)"
        case .dancer: return "엉덩이 춤"
        case .scooter: return "상동호수공원 (스쿠터)"
        case .face: return "상동호수공원 (얼굴)"
        }
    }

    func canvases(stress: Bool) -> [Canvas] {
        (0..<frameCount).map { i in
            let t = Double(i) / Double(frameCount)
            switch self {
            case .dog: return dogFrame(t, stress: stress)
            case .cat: return catFrame(t, stress: stress)
            case .monkey: return monkeyFrame(t, stress: stress)
            case .dancer: return dancerFrame(t, stress: stress)
            case .scooter: return scooterFrame(t, stress: stress)
            case .face: return faceFrame(t, stress: stress)
            }
        }
    }
}

// MARK: - 이미지 변환

// mono: 색 없는 모드에서 칠할 색. nil이면 템플릿 이미지(메뉴 바가 알아서 칠함)
func makeImage(_ canvas: Canvas, palette: Palette?, mono: RGB? = nil) -> NSImage {
    let scale = 2
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: canvas.w * scale, pixelsHigh: canvas.h * scale,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: canvas.w, height: canvas.h)
    let data = rep.bitmapData!
    for py in 0..<canvas.h * scale {
        for px in 0..<canvas.w * scale {
            let x = px / scale, y = canvas.h - 1 - py / scale
            let i = py * rep.bytesPerRow + px * 4
            if let palette {
                let c = canvas.color(x, y, palette)
                data[i] = c?.r ?? 0; data[i + 1] = c?.g ?? 0; data[i + 2] = c?.b ?? 0
                data[i + 3] = c == nil ? 0 : 255
            } else {
                // 미리 곱한 알파(premultiplied) 형식이라 색에도 알파를 곱함
                let a = canvas.alpha(x, y), m = mono ?? RGB(0)
                data[i] = UInt8(Double(m.r) * a); data[i + 1] = UInt8(Double(m.g) * a); data[i + 2] = UInt8(Double(m.b) * a)
                data[i + 3] = UInt8(a * 255)
            }
        }
    }
    let image = NSImage(size: rep.size)
    image.addRepresentation(rep)
    image.isTemplate = palette == nil && mono == nil
    return image
}

// `RunDog --preview out.png`: 캐릭터별로 [모노, 치즈, 초코+스트레스] 3줄씩 확대한 시트를 PNG로 저장
func writePreview(to path: String) {
    let zoom = 8, pad = 4, cols = 8
    let background = RGB(0xECECEC)
    let looks: [(String?, Bool)] = [(nil, false), ("cheese", false), ("choco", true)]
    var rows: [([Canvas], Palette?)] = []
    // PREVIEW_PETS=monkey,dancer 처럼 일부 캐릭터만 볼 수 있음
    let only = ProcessInfo.processInfo.environment["PREVIEW_PETS"]?.split(separator: ",").map(String.init)
    for pet in Pet.allCases where only?.contains(pet.rawValue) ?? true {
        for (id, stress) in looks {
            let frames = pet.canvases(stress: stress)
            rows.append((stride(from: 0, to: frameCount, by: frameCount / cols).map { frames[$0] }, palettes.first { $0?.id == id } ?? nil))
        }
    }
    let cellW = (32 + pad) * zoom, cellH = (gridH + pad) * zoom
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: cellW * cols, pixelsHigh: cellH * rows.count,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let data = rep.bitmapData!
    for py in 0..<rep.pixelsHigh {
        for px in 0..<rep.pixelsWide {
            let (frames, palette) = rows[py / cellH]
            let canvas = frames[px / cellW]
            let x = (px % cellW) / zoom - pad / 2, y = gridH - 1 - ((py % cellH) / zoom - pad / 2)
            var c = background
            if (0..<canvas.w).contains(x) && (0..<gridH).contains(y) {
                if let palette {
                    c = canvas.color(x, y, palette) ?? background
                } else {
                    c = background.darker(1 - canvas.alpha(x, y) * 0.9)
                }
            }
            let i = py * rep.bytesPerRow + px * 4
            data[i] = c.r; data[i + 1] = c.g; data[i + 2] = c.b; data[i + 3] = 255
        }
    }
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: path))
}

// MARK: - 사용률 측정

final class CPUMonitor {
    private var previous: host_cpu_load_info?

    // 직전 호출 이후 전체 CPU 사용률(%)
    func usage() -> Double {
        var info = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info>.stride / MemoryLayout<integer_t>.stride)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return 0 }
        defer { previous = info }
        guard let prev = previous else { return 0 }

        let user = Double(info.cpu_ticks.0 &- prev.cpu_ticks.0)
        let system = Double(info.cpu_ticks.1 &- prev.cpu_ticks.1)
        let idle = Double(info.cpu_ticks.2 &- prev.cpu_ticks.2)
        let nice = Double(info.cpu_ticks.3 &- prev.cpu_ticks.3)
        let total = user + system + idle + nice
        return total > 0 ? (user + system + nice) / total * 100 : 0
    }
}

// Apple Silicon GPU 사용률: IOAccelerator의 PerformanceStatistics에서 읽음 (Stats 앱과 같은 방식)
func gpuUsage() -> Double {
    var iterator: io_iterator_t = 0
    guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"), &iterator) == KERN_SUCCESS else { return 0 }
    defer { IOObjectRelease(iterator) }
    var usage = 0.0
    while case let service = IOIteratorNext(iterator), service != 0 {
        defer { IOObjectRelease(service) }
        if let stats = IORegistryEntryCreateCFProperty(service, "PerformanceStatistics" as CFString, kCFAllocatorDefault, 0)?
            .takeRetainedValue() as? [String: Any],
           let value = stats["Device Utilization %"] as? NSNumber {
            usage = max(usage, value.doubleValue)
        }
    }
    return usage
}

// MARK: - 메뉴 바 펫

enum Source: String {
    case cpu, gpu

    var label: String { rawValue.uppercased() }
    var defaultPet: Pet { self == .cpu ? .dog : .cat }
    // CPU는 예전 버전 설정 키를 그대로 씀
    func key(_ name: String) -> String { self == .cpu ? name : "gpu.\(name)" }
}

// 사용률 하나를 맡아서 메뉴 바에서 달리는 한 마리
final class PetItem: NSObject, NSMenuDelegate {
    let source: Source
    private let read: () -> Double
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let usageMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let percentMenuItem = NSMenuItem(title: "메뉴 바에 % 표시", action: #selector(togglePercent), keyEquivalent: "")
    private let loginMenuItem = NSMenuItem(title: "로그인 시 자동 실행", action: #selector(toggleLogin), keyEquivalent: "")
    private var petMenuItems: [NSMenuItem] = []
    private var colorMenuItems: [NSMenuItem] = []
    private var pet: Pet
    private var palette: Palette?
    private var showPercent: Bool
    // 매 프레임 버튼을 다시 그리면 CPU를 많이 먹어서, 버튼 위에 레이어를 얹고 그림만 갈아 끼움
    private let animationLayer = CALayer()
    private var appearanceObservation: NSKeyValueObservation?
    // [평소, 바쁠 때]
    private var frames: [[CGImage]] = []
    private var framePosition = 0.0
    private var usage = 0.0
    private var speed = 0.0

    init(source: Source, read: @escaping () -> Double) {
        self.source = source
        self.read = read
        let defaults = UserDefaults.standard
        pet = Pet(rawValue: defaults.string(forKey: source.key("pet")) ?? "") ?? source.defaultPet
        palette = palettes.first { $0?.id == defaults.string(forKey: source.key("color")) } ?? nil
        showPercent = defaults.bool(forKey: source.key("showPercent"))
        super.init()
    }

    func start() {
        statusItem.autosaveName = "RunDog.\(source.rawValue)"
        statusItem.button?.toolTip = "\(source.label) 사용량"
        statusItem.button?.imagePosition = .imageLeft
        statusItem.button?.font = .monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        statusItem.button?.wantsLayer = true
        animationLayer.magnificationFilter = .nearest
        statusItem.button?.layer?.addSublayer(animationLayer)
        // 라이트/다크 메뉴 바가 바뀌면 모노 색을 다시 칠함
        appearanceObservation = statusItem.button?.observe(\.effectiveAppearance) { [weak self] _, _ in
            DispatchQueue.main.async { self?.rebuildFrames() }
        }
        rebuildFrames()
        buildMenu()

        _ = read()
        let timer = Timer(timeInterval: 1.5, repeats: true) { [weak self] _ in self?.updateUsage() }
        RunLoop.main.add(timer, forMode: .common)
        tick()
    }

    private func rebuildFrames() {
        guard let button = statusItem.button else { return }
        let dark = button.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        let mono = dark ? RGB(0xFFFFFF) : RGB(0x000000)
        let canvases = [false, true].map { pet.canvases(stress: $0) }
        frames = canvases.map { set in
            set.map { makeImage($0, palette: palette, mono: mono).cgImage(forProposedRect: nil, context: nil, hints: nil)! }
        }
        // 버튼에는 같은 크기의 빈 그림만 넣어 자리를 잡아 둠
        button.image = NSImage(size: NSSize(width: canvases[0][0].w, height: gridH))
        layoutAnimationLayer()
        animationLayer.contents = frames[0][0]
    }

    private func layoutAnimationLayer() {
        guard let button = statusItem.button, let cell = button.cell as? NSButtonCell else { return }
        button.layoutSubtreeIfNeeded()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        animationLayer.frame = cell.imageRect(forBounds: button.bounds)
        animationLayer.contentsScale = button.window?.backingScaleFactor ?? 2
        CATransaction.commit()
    }

    private func buildMenu() {
        let menu = NSMenu()
        menu.delegate = self
        usageMenuItem.title = "\(source.label): --"
        usageMenuItem.isEnabled = false
        menu.addItem(usageMenuItem)
        menu.addItem(.separator())

        let petMenu = NSMenu()
        for p in Pet.allCases {
            let item = NSMenuItem(title: p.title, action: #selector(selectPet(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = p.rawValue
            petMenu.addItem(item)
            petMenuItems.append(item)
        }
        let petItem = NSMenuItem(title: "캐릭터", action: nil, keyEquivalent: "")
        petItem.submenu = petMenu
        menu.addItem(petItem)

        let colorMenu = NSMenu()
        for p in palettes {
            let item = NSMenuItem(title: p?.title ?? "자동 (메뉴 바 색)", action: #selector(selectColor(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = p?.id ?? "auto"
            colorMenu.addItem(item)
            colorMenuItems.append(item)
        }
        let colorItem = NSMenuItem(title: "색상", action: nil, keyEquivalent: "")
        colorItem.submenu = colorMenu
        menu.addItem(colorItem)

        percentMenuItem.target = self
        menu.addItem(percentMenuItem)
        menu.addItem(.separator())

        loginMenuItem.target = self
        menu.addItem(loginMenuItem)
        let activityItem = NSMenuItem(title: "활성 상태 보기 열기", action: #selector(openActivityMonitor), keyEquivalent: "")
        activityItem.target = self
        menu.addItem(activityItem)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
        refreshMenuState()
    }

    // 메뉴를 열 때마다 체크 표시와 색상 견본을 현재 상태에 맞춤
    func menuWillOpen(_ menu: NSMenu) { refreshMenuState() }

    private func refreshMenuState() {
        for item in petMenuItems { item.state = item.representedObject as? String == pet.rawValue ? .on : .off }
        for (item, p) in zip(colorMenuItems, palettes) {
            item.state = p?.id == palette?.id ? .on : .off
            item.image = p.map { makeImage(pet.canvases(stress: false)[0], palette: $0) }
        }
        percentMenuItem.state = showPercent ? .on : .off
        loginMenuItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    private func updateUsage() {
        usage = read()
        usageMenuItem.title = String(format: "%@: %.1f%%", source.label, usage)
        statusItem.button?.title = showPercent ? String(format: " %.0f%%", usage) : ""
        layoutAnimationLayer()
    }

    // 사용률이 높을수록 한 바퀴(16프레임) 도는 시간이 짧아짐: 0% 1.6초 → 100% 약 0.12초.
    // 속도는 서서히 따라가고, 너무 빠르면 초당 24장으로 제한하고 프레임을 건너뜀
    private func tick() {
        let cycle = 1.6 / (1 + speed / 8)
        let interval = max(1.0 / 24, cycle / Double(frameCount))
        speed += (usage - speed) * min(1, interval * 2)
        framePosition = (framePosition + interval / cycle * Double(frameCount)).truncatingRemainder(dividingBy: Double(frameCount))
        // 40% 이상이면 바쁜 버전 (혀 내밀기, 땀, 효과선)
        let set = speed > 40 ? frames[1] : frames[0]
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        animationLayer.contents = set[Int(framePosition.rounded()) % frameCount]
        CATransaction.commit()

        let timer = Timer(timeInterval: interval, repeats: false) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
    }

    @objc private func selectPet(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let p = Pet(rawValue: raw) else { return }
        pet = p
        UserDefaults.standard.set(raw, forKey: source.key("pet"))
        rebuildFrames()
    }

    @objc private func selectColor(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        palette = palettes.first { $0?.id == id } ?? nil
        UserDefaults.standard.set(id, forKey: source.key("color"))
        rebuildFrames()
    }

    @objc private func togglePercent() {
        showPercent.toggle()
        UserDefaults.standard.set(showPercent, forKey: source.key("showPercent"))
        statusItem.button?.title = showPercent ? String(format: " %.0f%%", usage) : ""
        layoutAnimationLayer()
    }

    @objc private func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            NSAlert(error: error).runModal()
        }
    }

    @objc private func openActivityMonitor() {
        NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Utilities/Activity Monitor.app"))
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let cpuMonitor = CPUMonitor()
    private var items: [PetItem] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        // 먼저 만든 항목이 메뉴 바 오른쪽에 붙음 (⌘ 드래그로 순서 변경 가능)
        items = [
            PetItem(source: .cpu) { [cpuMonitor] in cpuMonitor.usage() },
            PetItem(source: .gpu, read: gpuUsage),
        ]
        items.forEach { $0.start() }
    }
}

if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--preview" {
    writePreview(to: CommandLine.arguments[2])
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
