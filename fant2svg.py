#!/usr/bin/env python3
"""fant2svg - convert Fantavision (Broderbund, Amiga) FORM FANT movies to animated SVG (SMIL).

Usage: fant2svg.py MOVIE [OUT.svg] [--step SECONDS] [--arrival] [--debug] [--no-sound]
  --step: duration of one in-between step (default 0.05 s)

Format notes (from the sample movies and a disassembly of FantaPlayer, 3/9/88):
  FORM FANT
    FHDR (92)  : w $40, w $10, w depth, w width, w height, w background colour index,
                 32 x w palette ($0RGB), w nFrames, w nSounds, w nBitmaps, w hasBackground, pad
    FORM ILBM* : background (if hasBackground) then bitmaps
    FORM 8SVX + SEFX : sounds and their default settings
    FRAM (88)  : w mode (1 = repeat block), l block end frame, b repeat count, b ?, w tweens,
                 w sound A, w sound B, w nPolys, 32 x w palette, 3 x w ?, w 0
    SEFX       : one per sound command of the keyframe (channel A, then B)
    POLY*      : 18 w header + n x (x,y) signed words
                 h0 nPoints, h1 type (low byte) | flags $1000 mask $2000 trail $4000 border,
                 $7000 = invisible; h2 colour, h3..h6 bbox, h7 id, h8 size << 8 | ?,
                 h9 border colour (low byte), h10 bitmap index, h11/h12 bitmap width/height,
                 h13 text length, h15 font size << 8
    CSTR       : text string then font name, for each text object of the frame
"""
import struct, sys, io, base64, zlib

def W(b, o): return struct.unpack('>h', b[o:o+2])[0]
def UW(b, o): return struct.unpack('>H', b[o:o+2])[0]

def rgb(c):
    r, g, b = (c >> 8) & 15, (c >> 4) & 15, c & 15
    return '#%x%x%x' % (r, g, b)

# ---------------------------------------------------------------- ILBM -> PNG
def byterun1(src, size):
    out = bytearray(); i = 0
    while len(out) < size and i < len(src):
        n = src[i]; i += 1
        if n < 128:
            out += src[i:i+n+1]; i += n + 1
        elif n > 128:
            out += bytes([src[i]]) * (257 - n); i += 1
    return bytes(out[:size])

def ilbm_decode(form):
    o = 12; bmhd = body = None
    while o + 8 <= len(form):
        cid = form[o:o+4]; sz = struct.unpack('>I', form[o+4:o+8])[0]
        if cid == b'BMHD': bmhd = form[o+8:o+28]
        if cid == b'BODY': body = form[o+8:o+8+sz]
        o += 8 + sz + (sz & 1)
    w, h = UW(bmhd, 0), UW(bmhd, 2)
    planes, masking, comp = bmhd[8], bmhd[9], bmhd[10]
    tcol = UW(bmhd, 12)
    rb = ((w + 15) // 16) * 2
    nplanes = planes + (1 if masking == 1 else 0)
    data = byterun1(body, rb * nplanes * h) if comp == 1 else body
    pix = [[0] * w for _ in range(h)]
    for y in range(h):
        for p in range(planes):
            row = data[(y * nplanes + p) * rb:(y * nplanes + p + 1) * rb]
            for x in range(w):
                if row[x >> 3] & (0x80 >> (x & 7)):
                    pix[y][x] |= 1 << p
    return dict(w=w, h=h, pix=pix, masking=masking, tcol=tcol)

def png_bytes(img, palette, transparent):
    w, h = img['w'], img['h']
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            c = img['pix'][y][x]; v = palette[c % len(palette)]
            r, g, b = ((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17
            a = 0 if (transparent and c == img['tcol']) else 255
            raw += bytes((r, g, b, a))
    def chunk(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))

# ---------------------------------------------------------------- 8SVX / SEFX
AMIGA_CLOCK = 3579545  # NTSC Paula clock: sample rate = clock / period

def svx_decode(form):
    o = 12; rate = 8000; body = b''
    while o + 8 <= len(form):
        cid = form[o:o+4]; sz = struct.unpack('>I', form[o+4:o+8])[0]
        if cid == b'VHDR': rate = UW(form, o + 20)
        if cid == b'BODY': body = form[o+8:o+8+sz]
        o += 8 + sz + (sz & 1)
    return dict(rate=rate, data=body)

def sefx_decode(b):
    w = [UW(b, 2*i) for i in range(6)]
    return dict(volume=w[0] & 0xff, vflags=w[0] >> 8, oneshot=(w[2] == 256), period=w[3],
                length=(w[4] << 16) | w[5], name=b[12:32].split(b'\0')[0].decode('latin1'))

# ---------------------------------------------------------------- FANT parse
def parse(data):
    assert data[:4] == b'FORM' and data[8:12] == b'FANT', 'not a FORM FANT file'
    mv = dict(frames=[], ilbm=[], sounds=[])
    o = 12; pending_text = []
    while o + 8 <= len(data):
        cid = data[o:o+4]; sz = struct.unpack('>I', data[o+4:o+8])[0]
        b = data[o+8:o+8+sz]
        if cid == b'FHDR':
            mv['w'], mv['h'], mv['bgcolor'] = W(b, 6), W(b, 8), W(b, 10)
            mv['palette'] = [UW(b, 12 + 2*i) for i in range(32)]
            mv['nframes'], mv['nsounds'], mv['nbitmaps'], mv['hasbg'] = [UW(b, 76 + 2*i) for i in range(4)]
        elif cid == b'FORM' and b[:4] == b'ILBM':
            mv['ilbm'].append(ilbm_decode(data[o:o+8+sz]))
        elif cid == b'FORM' and b[:4] == b'8SVX':
            mv['sounds'].append(svx_decode(data[o:o+8+sz]))
        elif cid == b'SEFX':
            ev = sefx_decode(b)
            if mv['frames']: mv['frames'][-1]['sefx'].append(ev)
            else: mv.setdefault('sounddefs', []).append(ev)
        elif cid == b'FRAM':
            fr = dict(hdr=[W(b, 2*i) for i in range(8)], tweens=W(b, 8),
                      palette=[UW(b, 16 + 2*i) for i in range(32)], polys=[], sefx=[])
            mv['frames'].append(fr); pending_text = []
        elif cid == b'POLY':
            h = [W(b, 2*i) for i in range(18)]
            n = h[0]
            pts = [(W(b, 36 + 4*i), W(b, 38 + 4*i)) for i in range(n)]
            p = dict(n=n, flags=h[1] & 0xffff, color=h[2], bbox=h[3:7], id=h[7], h=h, pts=pts)
            mv['frames'][-1]['polys'].append(p)
            if (p['flags'] & 7) == 3: pending_text.append(p)
        elif cid == b'CSTR':
            s = b.split(b'\0')[0].decode('latin1')
            for p in pending_text:
                if 'text' not in p: p['text'] = s; break
                if 'font' not in p: p['font'] = s; break
        o += 8 + sz + (sz & 1)
    return mv

def kind(p):
    """Object type, as dispatched by FantaPlayer's draw routine (low byte of the flags word)."""
    f = p['flags']
    if f == 0x7000: return 'hidden'          # never drawn by the player
    return {0: 'fill', 1: 'line', 2: 'outline', 3: 'text', 4: 'dots', 5: 'squares',
            6: 'stamps', 7: 'bitmap'}.get(f & 0xff, 'fill')

FLAG_MASK = 0x1000     # bitmap drawn through a mask (colour 0 transparent)
FLAG_TRAIL = 0x2000    # object is also painted into the background ("comet" trail)
FLAG_BORDER = 0x4000   # second colour: polygon border / line joining dots or stamps

# ---------------------------------------------------------------- SVG output
def resample(pts, n):
    m = len(pts)
    return [pts[(k * m) // n] for k in range(n)]

def lerp_pts(a, b, t):
    n = max(len(a), len(b)); a, b = resample(a, n), resample(b, n)
    return [(round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)) for (x0, y0), (x1, y1) in zip(a, b)]

def wrap_text(text, width):
    """Word-wrap like a text box: explicit newlines, then words up to <width> characters per line."""
    out = []
    for para in text.replace('\r', '\n').split('\n'):
        line = ''
        for word in para.split(' '):
            cand = (line + ' ' + word) if line else word
            if len(cand) <= width or not line:
                line = cand
            else:
                out.append(line); line = word
        out.append(line.rstrip())
    return out

def fmt(t): return ('%.5f' % t).rstrip('0').rstrip('.') or '0'

def sequence(frames):
    """Frame order, unrolling the 'repeat' blocks (FRAM word 0 = 1: repeat this keyframe up to
    keyframe <long at offset 2>, <byte at offset 6> times)."""
    seq, i, loops = [], 0, {}
    while i < len(frames) and len(seq) < 10000:
        seq.append(i); h = frames[i]['hdr']
        if h[0] == 1 and i not in loops:
            tgt = ((h[1] & 0xffff) << 16) | (h[2] & 0xffff)
            loops[i] = [tgt, max(((h[3] >> 8) & 0xff) - 1, 0)]
        back = [s for s, (tgt, left) in loops.items() if tgt == i and left > 0]
        if back:
            loops[back[0]][1] -= 1; i = back[0]
        else:
            i += 1
    return seq

def build(mv, step, arrival=False, debug=False, sound=True):
    order = sequence(mv['frames'])
    frames = [mv['frames'][j] for j in order]; K = len(frames)
    seg = []
    for k in range(K):
        tw = frames[(k + 1) % K]['tweens'] if arrival else frames[k]['tweens']
        seg.append(max(tw, 0) + 1)
    # Looping: if the last keyframe still shows objects, morph it back into the first one.
    if frames[-1]['polys']:
        frames = frames + [frames[0]]; seg.append(0); K += 1
    tstart = [0]
    for s in seg: tstart.append(tstart[-1] + s)
    total = tstart[-1]
    T = total * step
    kt = lambda steps: steps / total
    W_, H_ = mv['w'], mv['h']
    yscale = 1.2 if H_ <= 200 else 1.0
    out = []
    a = out.append
    # Outer SVG: fills the browser window, keeps the display aspect ratio (letterboxed in black).
    # Inner SVG: Amiga pixel coordinates, stretched to the pixel aspect ratio and clipped.
    DH = round(H_ * yscale)
    a('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'viewBox="0 0 %d %d" width="100%%" height="100%%" style="background:#000">' % (W_, DH))
    a('<svg x="0" y="0" width="%d" height="%d" viewBox="0 0 %d %d" preserveAspectRatio="none" '
      'shape-rendering="crispEdges">' % (W_, DH, W_, H_))
    a('<title>%s (Fantavision)</title>' % mv.get('name', 'movie'))
    a('<desc>%d keyframes, %d steps, %.1f s loop. Converted by fant2svg.</desc>' % (K, total, T))
    anim = 'dur="%ss" repeatCount="indefinite"' % fmt(T)

    def discrete(attr, changes):
        """changes: list of (step, value); emits discrete <animate> (or nothing if constant)."""
        ch = []
        for s, v in changes:
            if ch and ch[-1][1] == v: continue
            if ch and ch[-1][0] == s: ch[-1] = (s, v); continue
            ch.append((s, v))
        if len(ch) <= 1: return ''
        if ch[0][0] != 0: ch.insert(0, (0, ch[0][1]))
        return ('<animate attributeName="%s" calcMode="discrete" values="%s" keyTimes="%s" %s/>'
                % (attr, ';'.join(str(v) for _, v in ch), ';'.join(fmt(kt(s)) for s, _ in ch), anim))

    def linear(attr, keys):
        """keys: list of (step, valuestring) ascending; holds before/after."""
        if len(set(v for _, v in keys)) <= 1: return ''
        ks = list(keys)
        if ks[0][0] > 0: ks.insert(0, (0, ks[0][1]))
        if ks[-1][0] < total: ks.append((total, ks[-1][1]))
        return ('<animate attributeName="%s" calcMode="linear" values="%s" keyTimes="%s" %s/>'
                % (attr, ';'.join(v for _, v in ks), ';'.join(fmt(kt(s)) for s, _ in ks), anim))

    def palette_at(k): return frames[k]['palette']
    def first(v, *alts): return v

    # background: cleared with colour <FHDR word 5> of the current palette (if < 32)
    bgi = mv['bgcolor'] if 0 <= mv['bgcolor'] < 32 else 0
    a('<rect x="-10" y="-10" width="%d" height="%d" fill="%s">%s</rect>' % (
        W_ + 20, H_ + 20, rgb(palette_at(0)[bgi]),
        discrete('fill', [(tstart[k], rgb(palette_at(k)[bgi])) for k in range(K)])))

    # bitmaps are drawn with the palette of the first keyframe
    pal0 = mv['frames'][0]['palette'] if any(mv['frames'][0]['palette']) else mv['palette']
    bms = list(mv['ilbm'])
    if mv['hasbg'] and bms:
        bg = bms.pop(0)
        a('<image x="0" y="0" width="%d" height="%d" preserveAspectRatio="none" style="image-rendering:pixelated" '
          'href="data:image/png;base64,%s"/>' % (bg['w'], bg['h'], base64.b64encode(png_bytes(bg, pal0, False)).decode()))
    masked = set(p['h'][10] for f in mv['frames'] for p in f['polys']
                 if kind(p) in ('bitmap', 'stamps') and p['flags'] & FLAG_MASK)
    # bitmaps are stored padded to a multiple of 16 pixels: crop them to the size the object gives
    def bmkey(p):
        i = p['h'][10]
        return (i, min(p['h'][11], bms[i]['w']) or bms[i]['w'], min(p['h'][12], bms[i]['h']) or bms[i]['h'])
    used = sorted(set(bmkey(p) for f in mv['frames'] for p in f['polys']
                      if kind(p) in ('bitmap', 'stamps') and 0 <= p['h'][10] < len(bms)))
    bmid = lambda p: 'bm%d_%d_%d' % bmkey(p)
    if used:
        a('<defs>')
        for i, w, h in used:
            img = dict(bms[i], w=w, h=h, pix=[row[:w] for row in bms[i]['pix'][:h]])
            a('<image id="bm%d_%d_%d" width="%d" height="%d" preserveAspectRatio="none" style="image-rendering:pixelated" '
              'href="data:image/png;base64,%s"/>' % (i, w, h, w, h,
                base64.b64encode(png_bytes(img, pal0, i in masked)).decode()))
        a('</defs>')
    has_bm = lambda p: 0 <= p['h'][10] < len(bms)

    def col(k, ci): return rgb(palette_at(k)[ci & 31])

    def colour_changes(run, field):
        """Colour index tweened step by step between keyframes, as (step, '#rgb') changes."""
        cols = []
        for j, (k, p) in enumerate(run):
            nxt = run[j + 1][1] if j + 1 < len(run) else None
            c0 = field(p)
            if nxt is None or field(nxt) == c0:
                cols.append((tstart[k], col(k, c0)))
            else:
                S = seg[k]
                for st in range(S):
                    cols.append((tstart[k] + st, col(k, round(c0 + (field(nxt) - c0) * st / S))))
        return cols

    color1 = lambda p: p['color']
    color2 = lambda p: p['h'][9] & 0xff

    # runs of consecutive compatible keyframes, per object slot (objects are matched by position)
    nslots = max(len(f['polys']) for f in frames)
    runs_by_slot = []
    for i in range(nslots):
        runs = []; cur = None
        for k in range(K):
            p = frames[k]['polys'][i] if i < len(frames[k]['polys']) else None
            if p is None or (not debug and kind(p) == 'hidden'):
                cur = None; continue
            prev = cur[-1][1] if cur else None
            if prev and kind(prev) == kind(p) and (kind(p) not in ('bitmap', 'stamps') or prev['h'][10] == p['h'][10]) \
                    and (kind(p) != 'text' or prev.get('text') == p.get('text')):
                cur.append((k, p))
            else:
                cur = [(k, p)]; runs.append(cur)
        runs_by_slot.append(runs)

    def shape(kd, pts, p, fill, extra=''):
        """Static SVG for one object (used for trails)."""
        P = ' '.join('%d,%d' % xy for xy in pts)
        if kd == 'fill':
            stroke = ' stroke="%s"' % extra if extra else ''
            return '<polygon points="%s" fill="%s"%s/>' % (P, fill, stroke)
        if kd in ('line', 'outline'):
            return '<%s points="%s" fill="none" stroke="%s"/>' % ('polyline' if kd == 'line' else 'polygon', P, fill)
        if kd in ('dots', 'squares'):
            sz = max((p['h'][8] >> 8) & 0xff, 1)
            return ''.join('<rect x="%s" y="%s" width="%d" height="%d" fill="%s"/>' % (
                fmt(x - sz / 2 if sz > 1 else x), fmt(y - sz / 2 if sz > 1 else y), sz, sz, fill) for x, y in pts)
        if kd in ('bitmap', 'stamps') and has_bm(p):
            return ''.join('<use href="#%s" x="%s" y="%s"/>' % (bmid(p), fmt(x - p['h'][11] / 2), fmt(y - p['h'][12] / 2))
                           for x, y in (pts if kd == 'stamps' else pts[:1]))
        return ''

    # ---- trails: objects flagged 0x2000 are also painted, step by step, into the background
    trails = []
    for runs in runs_by_slot:
        for run in runs:
            kd = kind(run[0][1])
            for j, (k, p) in enumerate(run):
                if not p['flags'] & FLAG_TRAIL or kd in ('text', 'hidden'): continue
                nxt = run[j + 1][1] if j + 1 < len(run) else None
                S = seg[k] if nxt is not None else 1
                for st in range(S):
                    s = tstart[k] + st
                    if s >= total: break
                    t = st / S
                    pts = lerp_pts(p['pts'], nxt['pts'], t) if nxt is not None else p['pts']
                    ci = round(p['color'] + ((nxt['color'] - p['color']) * t if nxt is not None else 0))
                    c2 = col(k, color2(p)) if p['flags'] & FLAG_BORDER else ''
                    el = shape(kd, pts, p, col(k, ci), c2)
                    if el:
                        vis = discrete('visibility', [(0, 'hidden'), (s, 'visible')]) if s > 0 else ''
                        trails.append('<g>%s%s</g>' % (el, vis) if vis else el)
    if trails:
        a('<g fill-rule="evenodd" stroke-linejoin="round">'); out.extend(trails); a('</g>')

    # ---- objects
    a('<g fill-rule="evenodd" stroke-linejoin="round">')
    for runs in runs_by_slot:
        for run in runs:
            k0, kl = run[0][0], run[-1][0]
            s0, s1 = tstart[k0], tstart[kl + 1]
            if s0 >= total: continue
            p0 = run[0][1]
            kd = kind(p0)
            vis = discrete('visibility', [(0, 'hidden'), (s0, 'visible'), (s1, 'hidden')]) \
                if (s0 > 0 or s1 < total) else ''
            cols = colour_changes(run, color1)
            c0 = cols[0][1]
            border = bool(p0['flags'] & FLAG_BORDER)
            cols2 = colour_changes(run, color2) if border else None
            N = max(p['n'] for _, p in run)
            ptkeys = [(tstart[k], resample(p['pts'], N)) for k, p in run]
            keys = [(s, ' '.join('%d,%d' % xy for xy in pts)) for s, pts in ptkeys]
            if kd in ('fill', 'line', 'outline', 'hidden'):
                tag = 'polyline' if kd == 'line' else 'polygon'
                if kd == 'hidden':
                    style, canim = 'fill="none" stroke="#f0f" stroke-dasharray="2" stroke-width="0.5"', ''
                elif kd == 'fill':
                    style = 'fill="%s"' % c0
                    canim = discrete('fill', cols)
                    if border:
                        style += ' stroke="%s" stroke-width="1"' % cols2[0][1]
                        canim += discrete('stroke', cols2)
                else:
                    style = 'fill="none" stroke="%s" stroke-width="1"' % c0
                    canim = discrete('stroke', cols)
                a('<%s points="%s" %s>%s%s%s</%s>' % (tag, keys[0][1], style, vis,
                  linear('points', keys), canim, tag))
            elif kd in ('dots', 'squares', 'stamps'):
                if kd == 'stamps' and not has_bm(p0): continue
                g = []
                if border:   # line joining the points, drawn after them in the second colour
                    pass
                sz = max((p0['h'][8] >> 8) & 0xff, 1)
                for j in range(N):
                    if kd == 'stamps':
                        ox, oy = p0['h'][11] / 2, p0['h'][12] / 2
                        kx = [(s, fmt(pts[j][0] - ox)) for s, pts in ptkeys]
                        ky = [(s, fmt(pts[j][1] - oy)) for s, pts in ptkeys]
                        g.append('<use href="#%s" x="%s" y="%s">%s%s</use>' % (
                            bmid(p0), kx[0][1], ky[0][1], linear('x', kx), linear('y', ky)))
                    elif kd == 'dots' and sz > 1:
                        kx = [(s, str(pts[j][0])) for s, pts in ptkeys]
                        ky = [(s, str(pts[j][1])) for s, pts in ptkeys]
                        g.append('<circle cx="%s" cy="%s" r="%s">%s%s</circle>' % (
                            kx[0][1], ky[0][1], fmt(sz / 2), linear('cx', kx), linear('cy', ky)))
                    else:
                        off = sz / 2 if sz > 1 else 0
                        kx = [(s, fmt(pts[j][0] - off)) for s, pts in ptkeys]
                        ky = [(s, fmt(pts[j][1] - off)) for s, pts in ptkeys]
                        g.append('<rect x="%s" y="%s" width="%d" height="%d">%s%s</rect>' % (
                            kx[0][1], ky[0][1], sz, sz, linear('x', kx), linear('y', ky)))
                if border:
                    g.append('<polygon points="%s" fill="none" stroke="%s" stroke-width="1">%s%s</polygon>' % (
                        keys[0][1], cols2[0][1], linear('points', keys), discrete('stroke', cols2)))
                a('<g fill="%s">%s%s%s</g>' % (c0, vis, discrete('fill', cols) if kd != 'stamps' else '', ''.join(g)))
            elif kd == 'text':
                x1, y1, x2, y2 = p0['bbox']
                font = (p0.get('font') or 'topaz.font').lower()
                size = (p0['h'][15] >> 8) & 0xff or (y2 - y1)
                mono = font.startswith(('topaz', 'small'))
                cw = size if font.startswith('topaz') else size * (1.0 if mono else 0.62)
                lines = wrap_text(p0.get('text', '') or '', max(int((x2 - x1 + 1) // cw), 1))
                lh = size + 1
                family = "'Topaz','Topaz New',monospace" if mono else "'Arial Black','Impact',sans-serif"
                g = []
                for j, line in enumerate(lines):
                    if not line: continue
                    tl = min(len(line) * cw, x2 - x1 + 1)
                    g.append('<text x="%d" y="%s" font-size="%s" textLength="%s" lengthAdjust="spacingAndGlyphs">%s</text>' % (
                        x1, fmt(y1 + (j + 1) * lh - size * 0.18), fmt(size * 1.1), fmt(tl),
                        line.replace('&', '&amp;').replace('<', '&lt;')))
                tr = [(tstart[k], '%d,%d' % (q['bbox'][0] - x1, q['bbox'][1] - y1)) for k, q in run]
                move = linear('transform', tr).replace('<animate ', '<animateTransform type="translate" ') if len(set(v for _, v in tr)) > 1 else ''
                a('<g font-family="%s" fill="%s" xml:space="preserve">%s%s%s%s</g>' % (
                    family, c0, vis, move, discrete('fill', cols), ''.join(g)))
            elif kd == 'bitmap':
                if not has_bm(p0): continue
                def bxy(q):
                    cx, cy = q['pts'][0]
                    return cx - q['h'][11] / 2, cy - q['h'][12] / 2
                kx = [(tstart[k], fmt(bxy(q)[0])) for k, q in run]
                ky = [(tstart[k], fmt(bxy(q)[1])) for k, q in run]
                a('<use href="#%s" x="%s" y="%s">%s%s%s</use>' % (
                    bmid(p0), kx[0][1], ky[0][1], vis, linear('x', kx), linear('y', ky)))
    a('</g>')
    if sound:
        a(sound_block(mv, [(tstart[k] * step, order[k]) for k in range(len(order))], T, W_))
    a('</svg></svg>')
    return '\n'.join(out), T, total

# ---------------------------------------------------------------- sound (Web Audio)
SOUND_JS = r"""
(function(){
var svg=document.documentElement, btn=document.getElementById('fv-sound'), lab=document.getElementById('fv-sound-label');
var BASE=22050, ctx=null, master=null, bufs={}, on=false, sched={}, chans=[{},{}], timer=null;
function buf(i,len){var k=i+':'+len; if(bufs[k])return bufs[k];
  var r=atob(SND[i]),n=Math.max(1,Math.min(len,r.length)),b=ctx.createBuffer(1,n,BASE),d=b.getChannelData(0);
  for(var j=0;j<n;j++){var v=r.charCodeAt(j);d[j]=(v>127?v-256:v)/128;} return bufs[k]=b;}
function stop(c,at){if(c.s){try{c.s.stop(at);}catch(x){} c.s=null;}}
function play(e,at){var c=chans[e[1]], op=e[2];
  if(op==0){stop(c,at); var s=ctx.createBufferSource(), g=ctx.createGain();
    s.buffer=buf(e[3],e[7]); s.loop=!e[6]; s.playbackRate.setValueAtTime(e[5]/BASE,at);
    g.gain.setValueAtTime(e[4],at); s.connect(g); g.connect(master); s.start(at); c.s=s; c.g=g;}
  else if(op==1&&c.s){c.g.gain.linearRampToValueAtTime(e[4],at); if(e[5]>0)c.s.playbackRate.linearRampToValueAtTime(e[5]/BASE,at);}
  else if(op==2)stop(c,at);}
function tick(){var now=svg.getCurrentTime(), L=Math.floor(now/T), t0=ctx.currentTime;
  for(var l=L;l<=L+1;l++)for(var i=0;i<EV.length;i++){var k=l+':'+i; if(sched[k])continue;
    var at=t0+(l*T+EV[i][0]-now); if(at<t0-0.1){sched[k]=1;continue;} if(at>t0+0.3)continue;
    sched[k]=1; play(EV[i],Math.max(at,t0));}}
btn.addEventListener('click',function(){
  if(!ctx){var A=window.AudioContext||window.webkitAudioContext; if(!A)return; ctx=new A(); master=ctx.createGain(); master.connect(ctx.destination);}
  if(!on){ctx.resume(); svg.setCurrentTime(0); sched={}; chans=[{},{}]; timer=setInterval(tick,40); tick(); on=true; lab.textContent='\u266A on';}
  else{clearInterval(timer); stop(chans[0],0); stop(chans[1],0); on=false; lab.textContent='\u266A off';}
});
})();
"""

def sound_block(mv, keys, T, width):
    """Button + script playing the 8SVX samples in sync with the SMIL timeline (Web Audio).
    Events: [time, channel, op(0 start,1 change,2 stop), sound, volume, sample rate, oneshot, length]"""
    if not mv['sounds']: return ''
    ev = []
    for t, fi in keys:
        fr = mv['frames'][fi]
        fx = list(fr['sefx'])
        for ch, code in enumerate((fr['hdr'][5], fr['hdr'][6])):
            if code == -1: continue
            fxe = fx.pop(0) if fx else None
            if code >= 0 and fxe and code < len(mv['sounds']):
                ev.append([t, ch, 0, code, fxe['volume'] / 64,
                           round(AMIGA_CLOCK / max(fxe['period'], 1)), int(fxe['oneshot']), fxe['length']])
            elif code == -2 and fxe:
                ev.append([t, ch, 1, 0, fxe['volume'] / 64,
                           round(AMIGA_CLOCK / fxe['period']) if fxe['period'] else 0, 0, 0])
            else:
                ev.append([t, ch, 2, 0, 0, 0, 0, 0])
    if not ev: return ''
    ev += [[T - 0.001, 0, 2, 0, 0, 0, 0, 0], [T - 0.001, 1, 2, 0, 0, 0, 0, 0]]  # silence at loop end
    ev.sort(key=lambda e: e[0])
    evs = ','.join('[%s,%d,%d,%d,%s,%d,%d,%d]' % (fmt(e[0]), e[1], e[2], e[3], fmt(e[4]), e[5], e[6], e[7]) for e in ev)
    snd = ','.join('"%s"' % base64.b64encode(x['data']).decode() for x in mv['sounds'])
    return ('<g id="fv-sound" style="cursor:pointer" font-family="sans-serif" font-size="8">'
            '<title>Sound on/off (restarts the movie)</title>'
            '<rect x="%d" y="3" width="34" height="12" rx="3" fill="#000" fill-opacity="0.55" stroke="#fff" stroke-opacity="0.6" stroke-width="0.5"/>'
            '<text id="fv-sound-label" x="%d" y="12" fill="#fff" text-anchor="middle">\u266a off</text></g>\n'
            '<script><![CDATA[\nvar T=%s, EV=[%s], SND=[%s];%s]]></script>' % (
                width - 37, width - 20, fmt(T), evs, snd, SOUND_JS))

if __name__ == '__main__':
    args = [x for x in sys.argv[1:] if not x.startswith('--')]
    step = 1 / 20
    if '--step' in sys.argv:
        step = float(sys.argv[sys.argv.index('--step') + 1]); args.remove(sys.argv[sys.argv.index('--step') + 1])
    src = args[0]; dst = args[1] if len(args) > 1 else src + '.svg'
    mv = parse(open(src, 'rb').read())
    mv['name'] = src.rsplit('/', 1)[-1]
    svg, T, total = build(mv, step, arrival='--arrival' in sys.argv, debug='--debug' in sys.argv,
                         sound='--no-sound' not in sys.argv)
    open(dst, 'w').write(svg)
    print('%s: %d keyframes, %d steps, %.1fs -> %s (%d bytes)' % (src, len(mv['frames']), total, T, dst, len(svg)))
