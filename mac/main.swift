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

// test로 안쪽인지 판단하는 도형. height는 입체 스타일에서 쓰는 볼록한 높이 (안쪽이면 +, 없으면 납작하게 칠함)
struct Shape {
    let test: (Double, Double) -> Bool
    var height: ((Double, Double) -> Double)? = nil
    func callAsFunction(_ x: Double, _ y: Double) -> Bool { test(x, y) }
}

// 입체 스타일에서 반짝이는 재질 (눈, 코, 혀, 리본)
let glossyInks: Set<Ink> = [.dark, .sweat, .tongue, .hole]

// 입체 스타일용으로 칠한 순서를 기록: 무늬(밝은 털, 볼터치)는 색만 바꾸고, 부위·앞발·눈·코·혀는 볼록한 모양을 가짐.
// box는 도형이 걸친 픽셀 범위라 그 밖은 검사하지 않음
struct PaintOp {
    let ink: Ink
    let shape: Shape
    let solid: Bool
    let onlyEmpty: Bool
    var box: (x0: Double, y0: Double, x1: Double, y1: Double)
}

struct Canvas {
    let w: Int
    let h = gridH
    var ink: [Ink]
    // 몸통·머리·다리 같은 부위 번호. 나중에 그린 부위가 앞에 있어서 경계에 테두리가 생김
    var part: [Int]
    var partCount = 0
    var ops: [PaintOp] = []

    init(w: Int) {
        self.w = w
        ink = [Ink](repeating: .none, count: w * gridH)
        part = [Int](repeating: 0, count: w * gridH)
    }

    // 픽셀 중심 좌표로 도형 안쪽을 칠함 (y는 위쪽이 +). 무늬는 아래 부위 번호를 유지
    mutating func paint(_ value: Ink, newPart: Bool? = nil, onlyEmpty: Bool = false, _ shape: Shape) {
        let isPart = newPart ?? (value == .body || value == .far)
        if isPart { partCount += 1 }
        var box = (x0: Double.infinity, y0: Double.infinity, x1: -Double.infinity, y1: -Double.infinity)
        for y in 0..<h {
            for x in 0..<w where shape(Double(x) + 0.5, Double(y) + 0.5) {
                box = (min(box.x0, Double(x)), min(box.y0, Double(y)), max(box.x1, Double(x + 1)), max(box.y1, Double(y + 1)))
                let i = y * w + x
                if onlyEmpty && ink[i] != .none { continue }
                if isPart || ink[i] == .none { part[i] = partCount }
                ink[i] = value
            }
        }
        // 픽셀 중심에 안 걸려도 반 픽셀 안쪽 가장자리는 입체에서 칠해질 수 있어서 1칸 넓힘
        if box.x0 <= box.x1 {
            ops.append(PaintOp(ink: value, shape: shape, solid: isPart || (newPart == false && value == .body) || glossyInks.contains(value),
                               onlyEmpty: onlyEmpty, box: (box.x0 - 1, box.y0 - 1, box.x1 + 1, box.y1 + 1)))
        }
    }

    mutating func dot(_ value: Ink, _ p: P) {
        let r = value == .hole ? 0.38 : 0.45
        ops.append(PaintOp(ink: value, shape: ellipse(p.x, p.y, r, r), solid: value != .hole, onlyEmpty: false,
                           box: (p.x - r, p.y - r, p.x + r, p.y + r)))
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

// 0 안팎으로 매끄럽게 이어지는 볼록한 높이 (안쪽 +, 바깥 -)
private func dome(_ q: Double, _ r: Double) -> Double { r * (q > 0 ? sqrt(q) : -sqrt(-q)) }

func ellipse(_ cx: Double, _ cy: Double, _ rx: Double, _ ry: Double, tilt: Double = 0) -> Shape {
    let c = cos(tilt), s = sin(tilt)
    func q(_ x: Double, _ y: Double) -> Double {
        let dx = x - cx, dy = y - cy
        let u = dx * c + dy * s, v = -dx * s + dy * c
        return (u * u) / (rx * rx) + (v * v) / (ry * ry)
    }
    // 짧은 반지름만큼 솟은 타원체
    return Shape(test: { x, y in q(x, y) <= 1 }, height: { x, y in dome(1 - q(x, y), min(rx, ry)) })
}

func capsule(_ a: P, _ b: P, _ r: Double) -> Shape {
    let vx = b.x - a.x, vy = b.y - a.y
    let len2 = vx * vx + vy * vy
    func dist2(_ x: Double, _ y: Double) -> Double {
        let t = len2 > 0 ? max(0, min(1, ((x - a.x) * vx + (y - a.y) * vy) / len2)) : 0
        let dx = x - (a.x + vx * t), dy = y - (a.y + vy * t)
        return dx * dx + dy * dy
    }
    // 둥근 막대 (원기둥 + 반구)
    return Shape(test: { x, y in dist2(x, y) <= r * r }, height: { x, y in dome(1 - dist2(x, y) / (r * r), r) })
}

func triangle(_ a: P, _ b: P, _ c: P) -> Shape {
    func side(_ p: P, _ q: P, _ x: Double, _ y: Double) -> Double { (q.x - p.x) * (y - p.y) - (q.y - p.y) * (x - p.x) }
    // 가장자리에서 안쪽으로 들어간 거리만큼 둥글게 부풀림 (귀)
    let sign: Double = side(a, b, c.x, c.y) > 0 ? 1 : -1
    let edges = [(a, b), (b, c), (c, a)].map { p, q -> (P, Double, Double) in
        let len = hypot(q.x - p.x, q.y - p.y)
        return (p, (q.x - p.x) / len, (q.y - p.y) / len)
    }
    let round = 0.9
    return Shape(test: { x, y in
        let d1 = side(a, b, x, y), d2 = side(b, c, x, y), d3 = side(c, a, x, y)
        return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))
    }, height: { x, y in
        let d = edges.map { p, ex, ey in sign * (ex * (y - p.y) - ey * (x - p.x)) }.min()!
        return d <= 0 ? d : sqrt(round * round - pow(round - min(d, round), 2))
    })
}

// 속이 빈 사각 테 (안경알 테두리)
func frame(_ cx: Double, _ cy: Double, _ hw: Double, _ hh: Double, thickness: Double) -> Shape {
    return Shape(test: { x, y in
        let dx = abs(x - cx), dy = abs(y - cy)
        return dx <= hw && dy <= hh && (dx > hw - thickness || dy > hh - thickness)
    })
}

func union(_ shapes: [Shape]) -> Shape {
    let heights = shapes.compactMap(\.height)
    return Shape(test: { x, y in shapes.contains { $0(x, y) } },
                 height: heights.isEmpty ? nil : { x, y in heights.map { $0(x, y) }.max()! })
}

func intersect(_ a: Shape, _ b: Shape) -> Shape {
    var shape = Shape(test: { x, y in a(x, y) && b(x, y) })
    if let ha = a.height, let hb = b.height { shape.height = { x, y in min(ha(x, y), hb(x, y)) } }
    return shape
}

// 몽글몽글한 털 뭉치: 타원 둘레에 작은 원을 빙 둘러 붙임.
// 입체로는 알갱이마다 볼록하면 구슬 목걸이처럼 보여서, 전체를 큰 돔 하나로 보고 가장자리만 몽글하게
func fluff(_ cx: Double, _ cy: Double, _ rx: Double, _ ry: Double, bumps: Int, r: Double) -> Shape {
    var shape = union([ellipse(cx, cy, rx, ry)] + (0..<bumps).map { k in
        let a = 2 * Double.pi * Double(k) / Double(bumps)
        return ellipse(cx + rx * cos(a), cy + ry * sin(a), r, r)
    })
    let outer = ellipse(cx, cy, rx + r, ry + r).height!, bumpy = shape.height!
    shape.height = { x, y in max(outer(x, y), 0.5 * bumpy(x, y)) }
    return shape
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
    let shoulder = P(19.5, 6.2 + bob), hip = P(9.5, 6.2 + bob)
    func dogLeg(_ root: P, _ phase: Double, hind: Bool) -> Shape {
        gallopLeg(root, w, phase: phase, hind: hind, amp: 0.9, upper: 2.6, lower: 2.6, r: 1.1, paw: (1.4, 0.9))
    }

    var c = Canvas(w: 32)
    c.paint(.far, dogLeg(P(shoulder.x - 1.4, shoulder.y), 0.2, hind: false))
    c.paint(.far, dogLeg(P(hip.x - 1.4, hip.y), 0.7, hind: true))
    let tailTip = P(5.5 + wag, 14.5 + bob)
    c.paint(.body, curve([P(8, 9 + bob), P(5, 10.5 + bob), tailTip], from: 1.4, to: 1))
    c.paint(.light, ellipse(tailTip.x, tailTip.y - 0.3, 1.2, 1.2))
    // 동글동글한 몸통 + 밝은 가슴털
    c.paint(.body, ellipse(14.5, 7.9 + bob, 6.6, 3.5, tilt: 0.05 * sin(w)))
    c.paint(.light, ellipse(19.2, 6.6 + bob, 2.4, 1.6))
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
    let shoulder = P(19.5 + stretch, 6 + bob), hip = P(8.8 - stretch, 6 + bob)
    func catLeg(_ root: P, _ phase: Double, hind: Bool) -> Shape {
        gallopLeg(root, w, phase: phase, hind: hind, amp: 1, upper: 2.5, lower: 2.7, r: 0.95, paw: (1.2, 0.8))
    }

    var c = Canvas(w: 32)
    c.paint(.far, catLeg(P(shoulder.x - 1.4, shoulder.y), 0.2, hind: false))
    c.paint(.far, catLeg(P(hip.x - 1.4, hip.y), 0.7, hind: true))
    // 길게 S자로 휘는 꼬리 + 줄무늬 꼬리 끝
    let tailTip = P(4 + 1.2 * sin(w), 15.5 + bob)
    c.paint(.body, curve([P(7.5, 8 + bob), P(2.5, 8.5 + bob), P(1.8, 13 + bob), tailTip], from: 1.2, to: 0.9))
    c.paint(.stripe, ellipse(tailTip.x - 0.2, tailTip.y - 0.4, 1.2, 1.2))
    c.paint(.body, ellipse(14, 7.6 + bob, 7 + stretch, 3.1, tilt: 0.05 * sin(w)))
    // 등 줄무늬
    for sx in [10.5, 13.5, 16.5] {
        c.paint(.stripe, capsule(P(sx + stretch * 0.5, 10.2 + bob), P(sx - 0.5 + stretch * 0.5, 8.9 + bob), 0.6))
    }
    c.paint(.light, ellipse(19.4 + stretch, 6.2 + bob, 2.3, 1.4))
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
    c.paint(.helmet, newPart: true, Shape(test: { x, y in y > helmetY && ellipse(10, 13.8 + head, 7.2, 4.2)(x, y) }))
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

// 옛날 택시 미터기 화면에서 달리던 말. 다리가 길쭉해서 네 발이 하나씩 따로 보이는 갤럽
func horseFrame(_ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    let bob = 0.5 * sin(2 * w)
    let hb = bob + 0.4 * sin(2 * w - 1)
    let flow = sin(2 * w - 1.5)
    let shoulder = P(20, 7.4 + bob), hip = P(10.2, 7.6 + bob)
    func horseLeg(_ root: P, _ phase: Double, hind: Bool) -> Shape {
        gallopLeg(root, w, phase: phase, hind: hind, amp: 0.8, upper: 3, lower: 3.1, r: 0.9, paw: (0.95, 0.65))
    }

    var c = Canvas(w: 32)
    c.paint(.far, horseLeg(P(shoulder.x - 1.4, shoulder.y), 0.2, hind: false))
    c.paint(.far, horseLeg(P(hip.x - 1.4, hip.y), 0.7, hind: true))
    // 휘날리는 꼬리
    c.paint(.ear, newPart: true, curve([P(7, 10.4 + bob), P(4, 11 + bob + 0.4 * flow), P(1.5, 8.6 + bob + flow)], from: 1.2, to: 0.7))
    // 둥근 엉덩이 + 몸통 + 가슴
    c.paint(.body, union([ellipse(10.6, 9.5 + bob, 3.7, 3.1), ellipse(15, 9.4 + bob, 6.4, 2.8), ellipse(19.6, 9.2 + bob, 3.1, 3)]))
    c.paint(.body, horseLeg(shoulder, 0, hind: false))
    c.paint(.body, horseLeg(hip, 0.5, hind: true))
    // 굵은 목 + 길쭉한 머리 + 쫑긋한 귀
    c.paint(.body, curve([P(19.6, 9.8 + bob), P(21.8, 12.3 + hb), P(23.6, 13.9 + hb)], from: 2.3, to: 1.7))
    c.paint(.body, union([capsule(P(24, 14.2 + hb), P(28, 12 + hb), 1.6), ellipse(28.3, 11.9 + hb, 1.8, 1.45),
                          triangle(P(22.9, 14.8 + hb), P(23.4, 17.2 + hb), P(24.7, 15.3 + hb))]))
    // 바람에 날리는 갈기
    c.paint(.ear, curve([P(23.6, 15.6 + hb), P(21.2, 14.4 + hb + 0.4 * flow), P(18.4, 11.8 + bob + 0.6 * flow)], from: 1.2, to: 0.8))
    c.paint(.dark, ellipse(25.5, 13.9 + hb, 0.75, 1))
    c.dot(.hole, P(25.3, 14.4 + hb))
    c.paint(.dark, ellipse(29.5, 12.2 + hb, 0.45, 0.45))
    return c
}

// 앉아서 꼬리를 흔들며 앞발을 번갈아 콩콩 드는 강아지 (정면). 견종마다 귀·무늬·꼬리·털이 다름.
// 바쁠수록 더 신나게 흔들고, 40%를 넘으면 혀를 내밀고 헥헥
func breedFrame(_ breed: Pet, _ t: Double, stress: Bool) -> Canvas {
    let w = 2 * Double.pi * t
    // 눈·코가 좌우 대칭으로 또렷하게 찍히도록 머리는 가운데 고정, 위아래도 1픽셀씩만 움직임
    let bb = 0.0
    let hx = 10.0, hy = 10.5 + (sin(2 * w) > 0.2 ? 1 : 0)
    let wag = sin(4 * w)
    let flop = 0.5 * sin(2 * w - 1.6)   // 늘어진 귀는 머리보다 한 박자 늦게 출렁
    let lift = [sin(w) > 0.3 ? 1.0 : 0, sin(w) < -0.3 ? 1.0 : 0]
    let marked = [Pet.shiba, .corgi, .husky].contains(breed)   // 흰 주둥이·가슴·양말 무늬

    var c = Canvas(w: 20)
    // 꼬리 (몸 뒤)
    switch breed {
    case .shiba, .husky:
        // 등 위로 돌돌 말린 꼬리
        c.paint(.body, ellipse(16.2 + 0.8 * wag, 5.8 + bb, 2.2, 2))
        c.paint(.light, ellipse(16.4 + 0.8 * wag, 5.6 + bb, 0.9, 0.8))
    case .pomeranian, .maltese:
        // 복슬복슬한 깃털 꼬리
        c.paint(.body, fluff(16 + 0.9 * wag, 6 + bb, 1.6, 1.3, bumps: 7, r: 1))
    case .poodle:
        // 끝에 방울이 달린 꼬리
        c.paint(.body, union([capsule(P(13.5, 4 + bb), P(16 + 0.8 * wag, 6.4 + bb), 0.6), fluff(16.2 + 0.9 * wag, 6.8 + bb, 0.9, 0.9, bumps: 6, r: 0.8)]))
    default:
        break   // 코기는 꼬리 대신 엉덩이를 씰룩
    }
    // 뒷발 + 앉은 몸통 + 가슴
    for fx in [5.9, 14.1] {
        c.paint(.body, ellipse(fx, 1.4, 1.9, 1.3))
        if marked { c.paint(.light, ellipse(fx, 1.3, 1.2, 0.7)) }
    }
    c.paint(.body, ellipse(10, 4.4 + bb, 4.6, 3.9, tilt: breed == .corgi ? 0.12 * wag : 0))
    if marked { c.paint(.light, ellipse(10, 4.4 + bb, 2.4, 2.8)) }
    // 번갈아 콩콩 드는 앞발
    for (lx, up) in zip([8.2, 11.8], lift) {
        let paw = P(lx, 1.2 + up)
        c.paint(.body, newPart: false, union([capsule(P(lx, 5 + bb), paw, 1.05), ellipse(lx, paw.y, 1.3, 0.95)]))
        if marked { c.paint(.light, ellipse(lx, paw.y, 0.9, 0.6)) }
    }

    // 머리. 뾰족 귀는 머리와 한 덩어리로 그려서 사이에 선이 안 생기게 함
    func pointyEars(_ base: Double, _ tip: Double, _ spread: Double) -> Shape {
        union([triangle(P(hx - spread, hy + base), P(hx - spread + 0.9, hy + tip), P(hx - 1.7, hy + base + 2.1)),
               triangle(P(hx + spread, hy + base), P(hx + spread - 0.9, hy + tip), P(hx + 1.7, hy + base + 2.1))])
    }
    func innerEars(_ base: Double, _ tip: Double, _ spread: Double) -> Shape {
        union([triangle(P(hx - spread + 0.9, hy + base + 1.3), P(hx - spread + 1.3, hy + tip - 1.3), P(hx - 2.8, hy + base + 2.2)),
               triangle(P(hx + spread - 0.9, hy + base + 1.3), P(hx + spread - 1.3, hy + tip - 1.3), P(hx + 2.8, hy + base + 2.2))])
    }
    var eyeX = 3.0, eyeY = hy - 0.5
    let head = ellipse(hx, hy, 6.1, 4.8)
    switch breed {
    case .shiba, .husky:
        c.paint(.body, union([head, pointyEars(1.8, 6.6, 5.6)]))
        c.paint(.light, innerEars(1.8, 6.6, 5.6))
        if breed == .shiba {
            // 볼과 턱이 하얀 우라지로 + 하얀 눈썹 점
            c.paint(.light, intersect(head, ellipse(hx, hy - 2.6, 4.6, 2.6)))
            for x in [7.5, 12.5] { c.dot(.light, P(x, eyeY + 2.5)) }
        } else {
            // 허스키 가면: 하얀 아랫얼굴 + 이마 가운데 흰 줄 + 눈 위 흰 점
            c.paint(.light, intersect(head, union([ellipse(hx, hy - 2.3, 5.2, 2.8), capsule(P(hx, hy + 3.4), P(hx, hy - 1), 0.7)])))
            for s in [-1.0, 1.0] { c.paint(.light, ellipse(hx + s * 2.7, hy + 2, 0.9, 0.6)) }
        }
    case .corgi:
        // 아주 큰 귀 + 이마의 흰 줄 + 흰 주둥이
        c.paint(.body, union([head, pointyEars(1.2, 7.4, 6.1)]))
        c.paint(.light, innerEars(1.2, 7.4, 6.1))
        c.paint(.light, intersect(head, union([ellipse(hx, hy - 2.5, 3.6, 2.4), capsule(P(hx, hy + 4.6), P(hx, hy - 1), 0.8)])))
    case .pomeranian:
        // 동그란 털 뭉치 + 털 위로 살짝 나온 작은 귀
        c.paint(.body, union([fluff(hx, hy - 0.4, 6, 4.8, bumps: 16, r: 1.2), pointyEars(3.4, 6.4, 3.9)]))
        c.paint(.light, ellipse(hx, hy - 2.4, 2.6, 1.6))
    case .maltese:
        // 길게 늘어진 귀(머리 뒤) + 동그란 하얀 머리 + 정수리 리본
        for s in [-1.0, 1.0] {
            c.paint(.body, union([capsule(P(hx + s * 4.8, hy + 1.6), P(hx + s * 5.9, hy - 3 + flop), 2), ellipse(hx + s * 5.9, hy - 3.5 + flop, 2, 1.4)]))
        }
        c.paint(.body, union([ellipse(hx, hy, 5.3, 4.6), ellipse(hx, hy + 4.2, 1.6, 1.2)]))
        c.paint(.tongue, union([triangle(P(hx - 2.2, hy + 4.4), P(hx - 2.2, hy + 6.4), P(hx, hy + 5.4)),
                                triangle(P(hx + 2.2, hy + 4.4), P(hx + 2.2, hy + 6.4), P(hx, hy + 5.4))]))
    case .poodle:
        // 동그란 머리 방울 + 곱슬곱슬 늘어진 귀
        c.paint(.body, fluff(hx, hy + 4.2, 3, 1.8, bumps: 9, r: 1))
        c.paint(.body, ellipse(hx, hy - 0.2, 5, 4.5))
        for s in [-1.0, 1.0] {
            c.paint(.ear, newPart: true, fluff(hx + s * 5.2, hy - 1.6 + flop, 1.3, 2.4, bumps: 8, r: 1))
        }
    default:
        break
    }
    // 얼굴: 볼터치, 반짝이는 눈, 코, 바쁠 때 혀
    // 2x2 눈(왼쪽 위에 하이라이트), 2x1 코. 허스키는 파란 눈
    for s in [-1.0, 1.0] {
        let ex = hx + s * eyeX
        c.paint(.blush, ellipse(hx + s * 5, eyeY - 1.5, 1, 0.5))
        c.paint(breed == .husky ? .sweat : .dark, newPart: false, ellipse(ex, eyeY, 0.9, 1))
        if breed == .husky { c.dot(.dark, P(ex + 0.5, eyeY - 0.5)) }
        c.dot(.hole, P(ex - 0.5, eyeY + 0.5))
    }
    let noseY = eyeY - 1.5
    c.paint(.dark, ellipse(hx, noseY, 1, 0.5))
    if stress {
        c.paint(.tongue, capsule(P(hx, noseY - 1), P(hx, noseY - 2), 0.75))
    }
    return c
}

enum Pet: String, CaseIterable {
    case dog, cat, horse, shiba, corgi, pomeranian, maltese, poodle, husky, monkey, dancer, scooter, face

    var title: String {
        switch self {
        case .dog: return "강아지"
        case .cat: return "고양이"
        case .horse: return "택시 미터기 말"
        case .shiba: return "시바견"
        case .corgi: return "웰시코기"
        case .pomeranian: return "포메라니안"
        case .maltese: return "말티즈"
        case .poodle: return "토이푸들"
        case .husky: return "허스키"
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
            case .horse: return horseFrame(t, stress: stress)
            case .shiba, .corgi, .pomeranian, .maltese, .poodle, .husky: return breedFrame(self, t, stress: stress)
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

// MARK: - 입체 스타일
// 도형마다 볼록한 높이가 있어서, 빛을 받는 쪽은 밝게·반대쪽은 어둡게, 앞 부위는 뒤 부위에 그림자를 드리움.
// 레티나 픽셀 하나를 4x4로 쪼개 칠해서 가장자리가 부드러움. 테두리 선 대신 가장자리만 살짝 어둡게

let solidPets: Set<Pet> = [.shiba, .corgi, .pomeranian, .maltese, .poodle, .husky]

// 자동 색상일 때 견종 고유색
func breedPalette(_ pet: Pet) -> Palette {
    let id: String
    switch pet {
    case .maltese: id = "cream"
    case .poodle: id = "choco"
    case .husky: id = "gray"
    default: id = "cheese"
    }
    return palettes.compactMap { $0 }.first { $0.id == id }!
}

typealias Vec = (x: Double, y: Double, z: Double)

private func unit(_ x: Double, _ y: Double, _ z: Double) -> Vec {
    let n = sqrt(x * x + y * y + z * z)
    return (x / n, y / n, z / n)
}

private let sun = unit(-0.4, 0.45, 1)  // 왼쪽 위 앞에서 오는 빛 (y는 위쪽이 +)
private let sunHalf = unit(sun.x, sun.y, sun.z + 1)

private func mix(_ a: Vec, _ b: Vec, _ t: Double) -> Vec {
    (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t)
}

private func vec(_ c: RGB) -> Vec { (Double(c.r), Double(c.g), Double(c.b)) }

// (x, y) 지점의 색. 칠한 순서대로 훑어서 맨 앞 재질과 모양을 정한 뒤 빛을 계산
private func solidSample(_ ops: [PaintOp], _ x: Double, _ y: Double, _ p: Palette) -> Vec? {
    var ink = Ink.none, under = Ink.none, surface: Shape?, front = 0
    for (k, op) in ops.enumerated() where op.box.x0 <= x && x <= op.box.x1 && op.box.y0 <= y && y <= op.box.y1 && op.shape(x, y) {
        if op.onlyEmpty && ink != .none { continue }
        if op.solid || ink == .none { surface = op.shape; front = k + 1 }
        if op.ink == .blush { under = ink }
        ink = op.ink
    }
    guard ink != .none else { return nil }
    if ink == .hole { return (255, 255, 255) }

    var n: Vec = (0, 0, 1)
    if let h = surface?.height {
        let e = 0.04
        n = unit(-(h(x + e, y) - h(x - e, y)) / (2 * e), -(h(x, y + e) - h(x, y - e)) / (2 * e), 1)
    }
    // 앞에 있는 부위가 빛을 가리면 그림자 (머리 밑 몸통, 앞발 옆 가슴 등)
    var shade = 1.0
    for dist in [0.63, 1.4] {
        let qx = x + sun.x * dist, qy = y + sun.y * dist
        if ops[front...].contains(where: { op in
            op.solid && !glossyInks.contains(op.ink) && op.box.x0 <= qx && qx <= op.box.x1 && op.box.y0 <= qy && qy <= op.box.y1 && op.shape(qx, qy)
        }) { shade *= 0.8 }
    }

    func base(_ ink: Ink) -> Vec {
        switch ink {
        case .far: return vec(p.fill.darker(0.72))
        case .whisker: return vec(p.outline)
        case .dark: return vec(RGB(0x24180F))
        case .tongue: return vec(RGB(0xFF6B85))
        case .sweat: return vec(RGB(0x5DB4F5))
        case .light: return vec(p.light)
        case .ear, .stripe: return vec(p.ear)
        case .blush: return vec(RGB(0xFF8FA6))
        default: return vec(p.fill)
        }
    }
    var col = ink == .blush ? mix(base(under), base(.blush), 0.75) : base(ink)
    let ndl = n.x * sun.x + n.y * sun.y + n.z * sun.z
    let ndh = max(0, n.x * sunHalf.x + n.y * sunHalf.y + n.z * sunHalf.z)
    let light = (0.6 + 0.6 * max(0, (ndl + 0.25) / 1.25)) * shade
    let spec = glossyInks.contains(ink) ? 0.85 * pow(ndh, 50) : 0.1 * pow(ndh, 12)
    col = mix((min(255, col.x * light), min(255, col.y * light), min(255, col.z * light)), vec(p.outline), pow(1 - n.z, 2) * 0.35)
    let glow = 255 * spec * shade
    return (min(255, col.x + glow), min(255, col.y + glow), min(255, col.z + glow))
}

// 위쪽 줄부터 RGBA (알파는 곱하지 않은 값)
func solidPixels(_ canvas: Canvas, palette: Palette, scale: Int = 2, ss: Int = 4) -> [UInt8] {
    let w = canvas.w * scale, h = canvas.h * scale, s = Double(scale)
    var out = [UInt8](repeating: 0, count: w * h * 4)
    let outline = vec(palette.outline)
    for py in 0..<h {
        let uy0 = Double(py) / s, uy1 = Double(py + 1) / s
        for px in 0..<w {
            let ux0 = Double(px) / s, ux1 = Double(px + 1) / s
            // 이 픽셀 근처(그림자 거리 포함)에 걸치는 도형만 추려서 검사 횟수를 줄임
            let near = canvas.ops.filter { $0.box.x0 <= ux1 + 1.5 && $0.box.x1 >= ux0 - 1.5 && $0.box.y0 <= uy1 + 1.5 && $0.box.y1 >= uy0 - 1.5 }
            if near.isEmpty { continue }
            var acc: Vec = (0, 0, 0), hits = 0
            for sy in 0..<ss {
                let y = (Double(py) + (Double(sy) + 0.5) / Double(ss)) / s
                for sx in 0..<ss {
                    if let c = solidSample(near, (Double(px) + (Double(sx) + 0.5) / Double(ss)) / s, y, palette) {
                        hits += 1
                        acc = (acc.x + c.x, acc.y + c.y, acc.z + c.z)
                    }
                }
            }
            guard hits > 0 else { continue }
            var col: Vec = (acc.x / Double(hits), acc.y / Double(hits), acc.z / Double(hits))
            // 바깥 가장자리는 테두리 색을 섞어서 밝은 메뉴 바에서도 윤곽이 보이게
            if hits < ss * ss { col = mix(col, outline, 0.45) }
            let i = ((h - 1 - py) * w + px) * 4
            out[i] = UInt8(col.x); out[i + 1] = UInt8(col.y); out[i + 2] = UInt8(col.z)
            out[i + 3] = UInt8(255 * hits / (ss * ss))
        }
    }
    return out
}

func makeSolidImage(_ canvas: Canvas, palette: Palette) -> NSImage {
    let scale = 2
    let pixels = solidPixels(canvas, palette: palette, scale: scale)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: canvas.w * scale, pixelsHigh: canvas.h * scale,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: canvas.w, height: canvas.h)
    let data = rep.bitmapData!
    for py in 0..<rep.pixelsHigh {
        for px in 0..<rep.pixelsWide {
            let i = (py * rep.pixelsWide + px) * 4, o = py * rep.bytesPerRow + px * 4
            // 미리 곱한 알파(premultiplied) 형식이라 색에도 알파를 곱함
            let a = Double(pixels[i + 3]) / 255
            data[o] = UInt8(Double(pixels[i]) * a); data[o + 1] = UInt8(Double(pixels[i + 1]) * a)
            data[o + 2] = UInt8(Double(pixels[i + 2]) * a); data[o + 3] = pixels[i + 3]
        }
    }
    let image = NSImage(size: rep.size)
    image.addRepresentation(rep)
    return image
}

// `RunDog --preview out.png`: 캐릭터별로 [모노, 치즈, 초코+스트레스] 3줄씩 확대한 시트를 PNG로 저장.
// 견종 강아지는 [입체, 입체+스트레스] 2줄을 더 그림 (입체는 레티나 해상도)
func writePreview(to path: String) {
    let zoom = 8, pad = 4, cols = 8
    let background = RGB(0xECECEC)
    let looks: [(String?, Bool)] = [(nil, false), ("cheese", false), ("choco", true)]
    var rows: [([Canvas], Palette?, [[UInt8]]?)] = []
    // PREVIEW_PETS=monkey,dancer 처럼 일부 캐릭터만 볼 수 있음
    let only = ProcessInfo.processInfo.environment["PREVIEW_PETS"]?.split(separator: ",").map(String.init)
    for pet in Pet.allCases where only?.contains(pet.rawValue) ?? true {
        for (id, stress) in looks {
            let frames = pet.canvases(stress: stress)
            rows.append((stride(from: 0, to: frameCount, by: frameCount / cols).map { frames[$0] }, palettes.first { $0?.id == id } ?? nil, nil))
        }
        if solidPets.contains(pet) {
            for stress in [false, true] {
                let frames = stride(from: 0, to: frameCount, by: frameCount / cols).map { pet.canvases(stress: stress)[$0] }
                rows.append((frames, nil, frames.map { solidPixels($0, palette: breedPalette(pet)) }))
            }
        }
    }
    let cellW = (32 + pad) * zoom, cellH = (gridH + pad) * zoom
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: cellW * cols, pixelsHigh: cellH * rows.count,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let data = rep.bitmapData!
    for py in 0..<rep.pixelsHigh {
        for px in 0..<rep.pixelsWide {
            let (frames, palette, solid) = rows[py / cellH]
            let canvas = frames[px / cellW]
            let x = (px % cellW) / zoom - pad / 2, y = gridH - 1 - ((py % cellH) / zoom - pad / 2)
            var c = background
            if let solid, (0..<canvas.w).contains(x) && (0..<gridH).contains(y) {
                // 레티나 픽셀(확대 4배)을 배경 위에 알파로 섞음
                let sx = (px % cellW) / (zoom / 2) - pad, sy = (py % cellH) / (zoom / 2) - pad
                let i = (sy * canvas.w * 2 + sx) * 4, pixels = solid[px / cellW], a = Double(pixels[i + 3]) / 255
                func blend(_ v: UInt8, _ b: UInt8) -> UInt8 { UInt8(Double(v) * a + Double(b) * (1 - a)) }
                c.r = blend(pixels[i], background.r); c.g = blend(pixels[i + 1], background.g); c.b = blend(pixels[i + 2], background.b)
            } else if (0..<canvas.w).contains(x) && (0..<gridH).contains(y) {
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
    private let solidMenuItem = NSMenuItem(title: "입체 (견종 강아지)", action: #selector(toggleSolid), keyEquivalent: "")
    private var petMenuItems: [NSMenuItem] = []
    private var colorMenuItems: [NSMenuItem] = []
    private var pet: Pet
    private var palette: Palette?
    private var solid: Bool
    private var showPercent: Bool
    // 매 프레임 버튼을 다시 그리면 CPU를 많이 먹어서, 버튼 위에 레이어를 얹고 그림만 갈아 끼움
    private let animationLayer = CALayer()
    private var appearanceObservation: NSKeyValueObservation?
    // [평소, 바쁠 때]
    private var frames: [[CGImage]] = []
    private var frameIndex = 0
    private var usage = 0.0
    private var speed = 0.0

    init(source: Source, read: @escaping () -> Double) {
        self.source = source
        self.read = read
        let defaults = UserDefaults.standard
        pet = Pet(rawValue: defaults.string(forKey: source.key("pet")) ?? "") ?? source.defaultPet
        palette = palettes.first { $0?.id == defaults.string(forKey: source.key("color")) } ?? nil
        solid = defaults.bool(forKey: source.key("solid"))
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
            set.map { image(for: $0, palette: palette, mono: mono).cgImage(forProposedRect: nil, context: nil, hints: nil)! }
        }
        // 버튼에는 같은 크기의 빈 그림만 넣어 자리를 잡아 둠
        button.image = NSImage(size: NSSize(width: canvases[0][0].w, height: gridH))
        layoutAnimationLayer()
        animationLayer.contents = frames[0][0]
    }

    // 입체는 견종 강아지만 (자동 색상이면 견종 고유색으로 칠함)
    private func image(for canvas: Canvas, palette: Palette?, mono: RGB? = nil) -> NSImage {
        if solid && solidPets.contains(pet) { return makeSolidImage(canvas, palette: palette ?? breedPalette(pet)) }
        return makeImage(canvas, palette: palette, mono: mono)
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
            // 달리는 동물 / 앉아 있는 강아지들 / 그 밖의 캐릭터로 묶음
            if p == .shiba || p == .monkey { petMenu.addItem(.separator()) }
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
        colorMenu.addItem(.separator())
        solidMenuItem.target = self
        colorMenu.addItem(solidMenuItem)
        colorMenu.autoenablesItems = false
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
        let swatch = pet.canvases(stress: false)[0]
        for (item, p) in zip(colorMenuItems, palettes) {
            item.state = p?.id == palette?.id ? .on : .off
            item.image = p.map { image(for: swatch, palette: $0) }
        }
        solidMenuItem.state = solid ? .on : .off
        solidMenuItem.isEnabled = solidPets.contains(pet)
        percentMenuItem.state = showPercent ? .on : .off
        loginMenuItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    private func updateUsage() {
        usage = read()
        usageMenuItem.title = String(format: "%@: %.1f%%", source.label, usage)
        statusItem.button?.title = showPercent ? String(format: " %.0f%%", usage) : ""
        layoutAnimationLayer()
    }

    // 사용률이 높을수록 한 바퀴(16프레임) 도는 시간이 짧아짐: 0% 1.6초 → 100% 약 0.27초.
    // 초당 30장을 넘으면 매번 같은 칸 수(정수)만큼 건너뜀. 칸 수가 들쭉날쭉하거나
    // 한 번에 너무 많이 건너뛰면 다리가 순간이동하는 것처럼 보여서 최고 속도도 여기에 맞춤
    private func tick() {
        let cycle = 1.6 / (1 + speed / 20)
        let step = max(1, Int((1.0 / 30 * Double(frameCount) / cycle).rounded(.up)))
        let interval = cycle / Double(frameCount) * Double(step)
        speed += (usage - speed) * min(1, interval * 2)
        frameIndex = (frameIndex + step) % frameCount
        // 40% 이상이면 바쁜 버전 (혀 내밀기, 땀, 효과선)
        let set = speed > 40 ? frames[1] : frames[0]
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        animationLayer.contents = set[frameIndex]
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

    @objc private func toggleSolid() {
        solid.toggle()
        UserDefaults.standard.set(solid, forKey: source.key("solid"))
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
