"""WYSIWYG PDF -> DXF: applies clip paths, dash patterns, drops invisible content.
Usage: python3 -I pdf2dxf.py in.pdf out.dxf
"""
import sys, math, io
import pikepdf
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen

PT2MM = 25.4 / 72.0

def mat_mul(a, b):  # a then b  (PDF row-vector convention)
    return [a[0]*b[0]+a[1]*b[2], a[0]*b[1]+a[1]*b[3],
            a[2]*b[0]+a[3]*b[2], a[2]*b[1]+a[3]*b[3],
            a[4]*b[0]+a[5]*b[2]+b[4], a[4]*b[1]+a[5]*b[3]+b[5]]

def apply(m, p):
    x, y = p
    return (m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5])

def bez(p0, p1, p2, p3):
    L = math.dist(p0, p1)+math.dist(p1, p2)+math.dist(p2, p3)
    n = max(8, int(L / 0.5))
    out = []
    for i in range(1, n+1):
        t = i/n; u = 1-t
        out.append((u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0],
                    u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]))
    return out

# ---------- convex clipping ----------
def orient(poly):
    a = sum(poly[i][0]*poly[(i+1) % len(poly)][1]-poly[(i+1) % len(poly)][0]*poly[i][1] for i in range(len(poly)))
    return poly if a > 0 else poly[::-1]

def clean(poly):
    out = []
    for p in poly:
        if not out or math.dist(out[-1], p) > 1e-9:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) < 1e-9:
        out.pop()
    return out

def seg_clip(a, b, clip):  # Cyrus-Beck against CCW convex polygon -> (t0,t1) or None
    t0, t1 = 0.0, 1.0
    n = len(clip)
    dx, dy = b[0]-a[0], b[1]-a[1]
    for i in range(n):
        p, q = clip[i], clip[(i+1) % n]
        ex, ey = q[0]-p[0], q[1]-p[1]
        # inside: cross(e, x-p) >= 0
        num = ex*(a[1]-p[1]) - ey*(a[0]-p[0])
        den = ex*dy - ey*dx
        if abs(den) < 1e-15:
            if num < 0: return None
            continue
        t = -num/den
        if den > 0: t0 = max(t0, t)
        else: t1 = min(t1, t)
        if t0 > t1: return None
    return (t0, t1)

def clip_polyline(pts, clips):
    """return list of polylines that lie inside all clips"""
    out, cur = [], []
    for a, b in zip(pts, pts[1:]):
        lo, hi = 0.0, 1.0
        for c in clips:
            r = seg_clip(a, b, c)
            if r is None: lo, hi = 1, 0; break
            lo, hi = max(lo, r[0]), min(hi, r[1])
        if hi - lo <= 1e-9:
            if len(cur) > 1: out.append(cur)
            cur = []; continue
        pa = (a[0]+(b[0]-a[0])*lo, a[1]+(b[1]-a[1])*lo)
        pb = (a[0]+(b[0]-a[0])*hi, a[1]+(b[1]-a[1])*hi)
        if cur and math.dist(cur[-1], pa) < 1e-6:
            cur.append(pb)
        else:
            if len(cur) > 1: out.append(cur)
            cur = [pa, pb]
        if hi < 1-1e-9:
            out.append(cur); cur = []
    if len(cur) > 1: out.append(cur)
    return out

def clip_polygon(poly, clip):  # Sutherland-Hodgman, clip CCW convex
    n = len(clip)
    for i in range(n):
        if not poly: return []
        p, q = clip[i], clip[(i+1) % n]
        ins = lambda x: (q[0]-p[0])*(x[1]-p[1]) - (q[1]-p[1])*(x[0]-p[0]) >= -1e-12
        def inter(s, e):
            x1, y1, x2, y2 = s[0], s[1], e[0], e[1]
            x3, y3, x4, y4 = p[0], p[1], q[0], q[1]
            d = (x1-x2)*(y3-y4)-(y1-y2)*(x3-x4)
            t = ((x1-x3)*(y3-y4)-(y1-y3)*(x3-x4))/d
            return (x1+t*(x2-x1), y1+t*(y2-y1))
        res = []
        for j in range(len(poly)):
            s, e = poly[j-1], poly[j]
            if ins(e):
                if not ins(s): res.append(inter(s, e))
                res.append(e)
            elif ins(s):
                res.append(inter(s, e))
        poly = res
    return clean(poly)

def area(poly):
    return abs(sum(poly[i][0]*poly[(i+1) % len(poly)][1]-poly[(i+1) % len(poly)][0]*poly[i][1] for i in range(len(poly))))/2

# ---------- dashes ----------
def dashify(pts, arr, phase, scale):
    arr = [float(x)*scale for x in arr]
    if not arr or sum(arr) <= 0: return [pts]
    if len(arr) % 2: arr = arr*2
    i, rem, on = 0, arr[0], True
    ph = phase*scale
    while ph > 0:  # consume phase
        if ph >= rem: ph -= rem; i = (i+1) % len(arr); rem = arr[i]; on = not on
        else: rem -= ph; ph = 0
    out, cur = [], [pts[0]] if on else []
    for a, b in zip(pts, pts[1:]):
        L = math.dist(a, b); pos = 0.0
        while L - pos > rem:
            pos += rem
            p = (a[0]+(b[0]-a[0])*pos/L, a[1]+(b[1]-a[1])*pos/L)
            if on: cur.append(p); out.append(cur); cur = []
            else: cur = [p]
            on = not on; i = (i+1) % len(arr); rem = arr[i]
        rem -= (L-pos)
        if on: cur.append(b)
    if on and len(cur) > 1: out.append(cur)
    return [c for c in out if len(c) > 1 and sum(math.dist(x, y) for x, y in zip(c, c[1:])) > 1e-6]

# ---------- interpreter ----------
def run(pdf_path):
    pdf = pikepdf.open(pdf_path)
    page = pdf.pages[0]
    res = page.obj.Resources
    fonts = {}
    if '/Font' in res:
        for name, f in res.Font.items():
            fd = f.get('/FontDescriptor')
            if fd is not None and '/FontFile2' in fd:
                fonts[name] = TTFont(io.BytesIO(bytes(fd.FontFile2.read_bytes())))
    mb = [float(x) for x in page.obj.MediaBox]
    page_clip = orient([(mb[0], mb[1]), (mb[2], mb[1]), (mb[2], mb[3]), (mb[0], mb[3])])

    st = dict(ctm=[1, 0, 0, 1, 0, 0], clips=[page_clip], lw=1.0, dash=([], 0),
              sc=('K', (0, 0, 0, 1)), fc=('k', (0, 0, 0, 1)))
    stack = []
    path = []; cur = None; start = None; pending_clip = False
    ents = []  # (kind, layer-key, pts, closed)
    stats = dict(hidden_fills=0, hidden_strokes=0, clipped_pieces=0)
    tm = None; font = None; fsize = 1

    def flush_paint(stroke, fill):
        nonlocal path, pending_clip
        subs = [s for s in path if len(s) > 1]
        ctm = st['ctm']
        if fill:
            for s in subs:
                poly = clean(s)
                if len(poly) < 3: continue
                full = area(poly)
                for c in st['clips']:
                    poly = clip_polygon(poly, c)
                    if len(poly) < 3: break
                if len(poly) < 3 or area(poly) < 1e-6:
                    stats['hidden_fills'] += 1; continue
                if area(poly) < full*0.999: stats['clipped_pieces'] += 1
                ents.append(('fill', st['fc'], poly, True, st['lw']))
        if stroke:
            scale = math.sqrt(abs(ctm[0]*ctm[3]-ctm[1]*ctm[2]))
            for s in subs:
                closed = getattr(s, 'closed', False)
                pts = list(s) + ([s[0]] if closed and math.dist(s[0], s[-1]) > 1e-9 else [])
                pieces = dashify(pts, *st['dash'], scale) if st['dash'][0] else [pts]
                any_vis = False
                for pc in pieces:
                    for v in clip_polyline(pc, st['clips']):
                        any_vis = True
                        cl = (not st['dash'][0]) and closed and math.dist(v[0], v[-1]) < 1e-6 and len(v) == len(pts)
                        ents.append(('stroke', st['sc'], v[:-1] if cl else v, cl, st['lw']))
                if not any_vis: stats['hidden_strokes'] += 1
        if pending_clip:
            for s in subs:
                poly = clean(s)
                if len(poly) >= 3: st['clips'] = st['clips'] + [orient(poly)]
            pending_clip = False
        path = []

    class Sub(list):
        closed = False

    for operands, op in pikepdf.parse_content_stream(page):
        op = str(op); o = operands
        f = lambda i: float(o[i])
        if op == 'q':
            stack.append(dict(st)); continue
        if op == 'Q':
            st = stack.pop(); continue
        if op == 'cm':
            st['ctm'] = mat_mul([f(i) for i in range(6)], st['ctm']); continue
        if op == 'w': st['lw'] = f(0); continue
        if op == 'd': st['dash'] = ([float(x) for x in o[0]], float(o[1])); continue
        if op in ('K', 'k', 'G', 'g', 'RG', 'rg'):
            key = 'sc' if op in ('K', 'G', 'RG') else 'fc'
            st[key] = (op.upper(), tuple(float(x) for x in o)); continue
        if op == 'm':
            cur = (f(0), f(1)); start = cur; s = Sub([apply(st['ctm'], cur)]); path.append(s); continue
        if op == 'l':
            cur = (f(0), f(1)); path[-1].append(apply(st['ctm'], cur)); continue
        if op in ('c', 'v', 'y'):
            if op == 'c': p1, p2, p3 = (f(0), f(1)), (f(2), f(3)), (f(4), f(5))
            elif op == 'v': p1, p2, p3 = cur, (f(0), f(1)), (f(2), f(3))
            else: p1, p2, p3 = (f(0), f(1)), (f(2), f(3)), (f(2), f(3))
            for p in bez(cur, p1, p2, p3): path[-1].append(apply(st['ctm'], p))
            cur = p3; continue
        if op == 'h':
            if path: path[-1].closed = True
            cur = start; continue
        if op == 're':
            x, y, w, h = f(0), f(1), f(2), f(3)
            s = Sub(apply(st['ctm'], p) for p in [(x, y), (x+w, y), (x+w, y+h), (x, y+h)])
            s.closed = True; path.append(s); cur = (x, y); continue
        if op in ('W', 'W*'): pending_clip = True; continue
        if op == 'n': flush_paint(False, False); continue
        if op in ('S',): flush_paint(True, False); continue
        if op in ('s',):
            if path: path[-1].closed = True
            flush_paint(True, False); continue
        if op in ('f', 'F', 'f*'): flush_paint(False, True); continue
        if op in ('B', 'B*'): flush_paint(True, True); continue
        if op in ('b', 'b*'):
            if path: path[-1].closed = True
            flush_paint(True, True); continue
        if op == 'BT': tm = [1, 0, 0, 1, 0, 0]; continue
        if op == 'Tf': font = fonts.get(str(o[0])); fsize = f(1); continue
        if op == 'Tm': tm = [f(i) for i in range(6)]; continue
        if op == 'Tj' and font is not None:
            cmap = font.getBestCmap() or {}
            gs = font.getGlyphSet(); upem = font['head'].unitsPerEm
            x_adv = 0.0
            for ch in bytes(o[0]):
                gname = cmap.get(ch) or cmap.get(0xF000+ch)
                if gname is None:
                    go = font.getGlyphOrder()
                    gname = go[ch] if ch < len(go) else '.notdef'
                pen = RecordingPen(); gs[gname].draw(pen)
                m = mat_mul([fsize/upem, 0, 0, fsize/upem, x_adv*fsize/upem, 0], mat_mul(tm, st['ctm']))
                c0 = None
                for cmd, args in pen.value:
                    if cmd == 'moveTo':
                        c0 = args[0]; s = Sub([apply(m, c0)]); path.append(s)
                    elif cmd == 'lineTo':
                        c0 = args[0]; path[-1].append(apply(m, c0))
                    elif cmd == 'qCurveTo':
                        pts = list(args)
                        if pts[-1] is None: pts = pts[:-1]
                        # implied on-curve points
                        segs = []
                        for k in range(len(pts)-1):
                            if k < len(pts)-2:
                                mid = ((pts[k][0]+pts[k+1][0])/2, (pts[k][1]+pts[k+1][1])/2)
                                segs.append((pts[k], mid))
                            else:
                                segs.append((pts[k], pts[k+1]))
                        for ctrl, end in segs:
                            p1 = (c0[0]+2/3*(ctrl[0]-c0[0]), c0[1]+2/3*(ctrl[1]-c0[1]))
                            p2 = (end[0]+2/3*(ctrl[0]-end[0]), end[1]+2/3*(ctrl[1]-end[1]))
                            for p in bez(c0, p1, p2, end): path[-1].append(apply(m, p))
                            c0 = end
                    elif cmd == 'curveTo':
                        for p in bez(c0, *args): path[-1].append(apply(m, p))
                        c0 = args[-1]
                    elif cmd in ('closePath', 'endPath'):
                        path[-1].closed = True
                x_adv += gs[gname].width
            flush_paint(False, True)
            continue
    return ents, stats, mb

# ---------- DXF (R12, mm) ----------
def layer_for(e):
    kind, col, pts, closed, lw = e
    gray = col[1][-1] if col[0] == 'K' and len(col[1]) == 4 else None
    if kind == 'stroke':
        if lw >= 1.5: return 'CUT'
        if gray is not None and gray < 0.5: return 'PATTERN'
        return 'FOLD'
    if gray is not None and gray < 0.5: return 'PATTERN'
    return 'TEXT'

LAYERS = {'CUT': 7, 'FOLD': 1, 'PATTERN': 8, 'TEXT': 5}

def write_dxf(ents, out, mb):
    L = []
    w = lambda c, v: L.extend([str(c), str(v)])
    w(0, 'SECTION'); w(2, 'HEADER')
    w(9, '$ACADVER'); w(1, 'AC1009')
    w(9, '$INSUNITS'); w(70, 4)
    w(9, '$MEASUREMENT'); w(70, 1)
    w(9, '$EXTMIN'); w(10, 0); w(20, 0); w(30, 0)
    w(9, '$EXTMAX'); w(10, round(mb[2]*PT2MM, 3)); w(20, round(mb[3]*PT2MM, 3)); w(30, 0)
    w(0, 'ENDSEC')
    w(0, 'SECTION'); w(2, 'TABLES')
    w(0, 'TABLE'); w(2, 'LTYPE'); w(70, 1)
    w(0, 'LTYPE'); w(2, 'CONTINUOUS'); w(70, 0); w(3, 'Solid line'); w(72, 65); w(73, 0); w(40, 0.0)
    w(0, 'ENDTAB')
    w(0, 'TABLE'); w(2, 'LAYER'); w(70, len(LAYERS)+1)
    for name, col in [('0', 7)] + list(LAYERS.items()):
        w(0, 'LAYER'); w(2, name); w(70, 0); w(62, col); w(6, 'CONTINUOUS')
    w(0, 'ENDTAB'); w(0, 'ENDSEC')
    w(0, 'SECTION'); w(2, 'ENTITIES')
    fx = lambda v: f'{v*PT2MM:.4f}'
    counts = {}
    for e in ents:
        lay = layer_for(e); _, _, pts, closed, _ = e
        counts[lay] = counts.get(lay, 0)+1
        if len(pts) == 2 and not closed:
            w(0, 'LINE'); w(8, lay)
            w(10, fx(pts[0][0])); w(20, fx(pts[0][1])); w(30, 0)
            w(11, fx(pts[1][0])); w(21, fx(pts[1][1])); w(31, 0)
        else:
            w(0, 'POLYLINE'); w(8, lay); w(66, 1); w(10, 0); w(20, 0); w(30, 0); w(70, 1 if closed else 0)
            for p in pts:
                w(0, 'VERTEX'); w(8, lay); w(10, fx(p[0])); w(20, fx(p[1])); w(30, 0)
            w(0, 'SEQEND'); w(8, lay)
    w(0, 'ENDSEC'); w(0, 'EOF')
    open(out, 'w').write('\n'.join(L)+'\n')
    return counts

if __name__ == '__main__':
    ents, stats, mb = run(sys.argv[1])
    counts = write_dxf(ents, sys.argv[2], mb)
    print('stats', stats, 'entities per layer', counts)
