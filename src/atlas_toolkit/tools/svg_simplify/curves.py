"""Bounded curve flattening hooks for the shared SVG parser. No global monkey-patching."""
import math

MAX_POINTS = 2_000_000


def _distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = max(0., min(1., ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den)) if den else 0.
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def cubic(a, b, c, d, tol):
    # Convex hull within a capsule around the finite chord bounds the entire Bézier.
    out, stack = [], [(a, b, c, d, 0)]
    while stack:
        a, b, c, d, depth = stack.pop()
        if max(_distance(b, a, d), _distance(c, a, d)) <= tol:
            out.append(d)
        else:
            if depth >= 50 or len(stack) + len(out) > MAX_POINTS:
                raise ValueError("Curve exceeds safe sampling budget; keep original curves or increase deviation.")
            ab, bc, cd = [tuple((x + y) / 2 for x, y in zip(p, q)) for p, q in ((a, b), (b, c), (c, d))]
            abc, bcd = [tuple((x + y) / 2 for x, y in zip(p, q)) for p, q in ((ab, bc), (bc, cd))]
            mid = tuple((x + y) / 2 for x, y in zip(abc, bcd))
            stack.extend([(mid, bcd, cd, d, depth + 1), (a, ab, abc, mid, depth + 1)])
    return out


def quad(a, b, c, tol):
    return cubic(a, tuple((x + 2 * y) / 3 for x, y in zip(a, b)),
                 tuple((x + 2 * y) / 3 for x, y in zip(c, b)), c, tol)


def arc(a, rx, ry, phi, large, sweep, b, tol):
    if a == b:
        return []
    rx, ry = abs(rx), abs(ry)
    if not rx or not ry:
        return [b]
    phi = math.radians(phi % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (a[0] - b[0]) / 2, (a[1] - b[1]) / 2
    x, y = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x / rx) ** 2 + (y / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    den = (rx * y) ** 2 + (ry * x) ** 2
    coef = math.sqrt(max(0., ((rx * ry) ** 2 - den) / den)) if den else 0.
    coef *= -1 if large == sweep else 1
    cx, cy = coef * rx * y / ry, -coef * ry * x / rx
    center = (cp * cx - sp * cy + (a[0] + b[0]) / 2, sp * cx + cp * cy + (a[1] + b[1]) / 2)
    u, v = ((x - cx) / rx, (y - cy) / ry), ((-x - cx) / rx, (-y - cy) / ry)
    theta = math.atan2(u[1], u[0])
    angle = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
    if not sweep and angle > 0:
        angle -= 2 * math.pi
    elif sweep and angle < 0:
        angle += 2 * math.pi
    # Linear interpolation error <= max|p''(theta)| * delta_theta² / 8.
    count = max(1, math.ceil(abs(angle) / min(math.pi / 2, math.sqrt(8 * tol / max(rx, ry)))))
    if count > MAX_POINTS:
        raise ValueError("Arc exceeds safe sampling budget; keep original curves or increase deviation.")
    out = []
    for k in range(1, count):
        t = theta + angle * k / count
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((center[0] + cp * x - sp * y, center[1] + sp * x + cp * y))
    return out + [b]


FLATTENERS = (cubic, quad, arc)
