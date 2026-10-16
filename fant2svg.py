#!/usr/bin/env python3
"""fant2svg - convert Fantavision (Broderbund, Amiga) FORM FANT movies to animated SVG (SMIL).

Usage: fant2svg.py MOVIE [OUT.svg] [--step SECONDS] [--arrival] [--debug] [--no-sound]
  --step: base duration of one in-between step (default 0.05 s), plus FHDR speed/60 s

Format notes (reverse-engineered from the sample movies):
  FORM FANT
    FHDR (92)  : w 0x40, w 0x10, w depth, w width, w height, w speed,
                 32 x w palette ($0RGB), w nFrames, w nSounds, w nBitmaps, w hasBackground, pad
    FORM ILBM* : background (if hasBackground) then bitmaps (masking = transparent colour 0)
    FORM 8SVX + SEFX : sounds (ignored here, SVG has no audio)
    FRAM (88)  : 4 w ?, w tweens, w sndA, w sndB, w nPolys, 32 x w palette, 8 x 0
    POLY*      : 18 w header + n x (x,y) signed words
                 h0 nPoints, h1 flags/type, h2 colour index, h3..h6 bbox x1 y1 x2 y2,
                 h7 id, h8 256, h9 ?, h10 bitmap index, h11 bm width, h12 bm height,
                 h13 text length, h14 ?, h15 font size << 8
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
            mv['w'], mv['h'], mv['speed'] = W(b, 6), W(b, 8), W(b, 10)
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
    f = p['flags']
    if f & 0x7000 == 0x7000: return 'hidden'
    t = f & 7
    return {0: 'fill', 1: 'line', 2: 'outline', 3: 'text', 7: 'bitmap'}.get(t, 'fill')

# ---------------------------------------------------------------- SVG output
def resample(pts, n):
    m = len(pts)
    return [pts[(k * m) // n] for k in range(n)]

def fmt(t): return ('%.5f' % t).rstrip('0').rstrip('.') or '0'

def build(mv, step, arrival=False, debug=False, sound=True):
    frames = mv['frames']; K = len(frames)
    # duration (in steps) of each keyframe -> next keyframe segment
    seg = []
    for k in range(K):
        tw = frames[(k + 1) % K]['tweens'] if arrival else frames[k]['tweens']
        seg.append(max(tw, 0) + 1)
    # Looping: if the last keyframe still shows objects, morph it back into the first one
    # (a virtual copy of keyframe 0 placed at the very end of the timeline).
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
    a('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'viewBox="0 0 %d %d" width="%d" height="%d" preserveAspectRatio="none" '
      'shape-rendering="crispEdges">' % (W_, H_, W_ * 2, round(H_ * 2 * yscale)))
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

    # background colour (colour 0 of each keyframe palette)
    a('<rect x="-1000" y="-1000" width="%d" height="%d" fill="%s">%s</rect>' % (
        W_ + 2000, H_ + 2000, rgb(palette_at(0)[0]),
        discrete('fill', [(tstart[k], rgb(palette_at(k)[0])) for k in range(K)])))

    bms = list(mv['ilbm'])
    if mv['hasbg'] and bms:
        bg = bms.pop(0)
        png = png_bytes(bg, mv['palette'], False)
        a('<image x="0" y="0" width="%d" height="%d" preserveAspectRatio="none" style="image-rendering:pixelated" '
          'href="data:image/png;base64,%s"/>' % (bg['w'], bg['h'], base64.b64encode(png).decode()))
    bm_png = [base64.b64encode(png_bytes(b, mv['palette'], True)).decode() for b in bms]

    a('<g fill-rule="evenodd" stroke-linejoin="round">')
    nslots = max(len(f['polys']) for f in frames)
    elements = []  # (slot, start step) for z-order: keep slot order
    for i in range(nslots):
        # split into runs of consecutive compatible keyframes
        runs = []; cur = None
        for k in range(K):
            p = frames[k]['polys'][i] if i < len(frames[k]['polys']) else None
            if p is None or (debug is False and kind(p) == 'hidden'):
                cur = None; continue
            prev = cur[-1][1] if cur else None
            if prev and kind(prev) == kind(p) and (kind(p) != 'bitmap' or prev['h'][10] == p['h'][10]) \
                    and (kind(p) != 'text' or prev.get('text') == p.get('text')):
                cur.append((k, p))
            else:
                cur = [(k, p)]; runs.append(cur)
        for run in runs:
            k0, kl = run[0][0], run[-1][0]
            s0, s1 = tstart[k0], tstart[kl + 1]
            if s0 >= total: continue
            kd = kind(run[0][1])
            vis = discrete('visibility', [(0, 'hidden'), (s0, 'visible'), (s1, 'hidden')]) \
                if (s0 > 0 or s1 < total) else ''
            # colour: index tweened step by step between keyframes
            cols = []
            for j, (k, p) in enumerate(run):
                nxt = run[j + 1][1] if j + 1 < len(run) else None
                pal = palette_at(k)
                if nxt is None or nxt['color'] == p['color']:
                    cols.append((tstart[k], rgb(pal[p['color'] & 31])))
                else:
                    S = seg[k]
                    for st in range(S):
                        ci = round(p['color'] + (nxt['color'] - p['color']) * st / S)
                        cols.append((tstart[k] + st, rgb(pal[ci & 31])))
            c0 = cols[0][1]
            canim_attr = 'stroke' if kd in ('line', 'outline') else 'fill'
            canim = discrete(canim_attr, cols)
            if kd in ('fill', 'line', 'outline', 'hidden'):
                N = max(p['n'] for _, p in run)
                keys = [(tstart[k], ' '.join('%d,%d' % xy for xy in resample(p['pts'], N))) for k, p in run]
                tag = 'polyline' if kd == 'line' else 'polygon'
                if kd == 'hidden':
                    style = 'fill="none" stroke="#f0f" stroke-dasharray="2" stroke-width="0.5"'
                elif kd == 'fill':
                    style = 'fill="%s"' % c0
                else:
                    style = 'fill="none" stroke="%s" stroke-width="1"' % c0
                a('<%s points="%s" %s>%s%s%s</%s>' % (tag, keys[0][1], style, vis,
                  linear('points', keys), canim if kd != 'hidden' else '', tag))
            elif kd == 'text':
                p = run[0][1]
                x1, y1, x2, y2 = p['bbox']
                fs = (p['h'][15] >> 8) or (y2 - y1)
                keysx = [(tstart[k], str(q['bbox'][0])) for k, q in run]
                keysy = [(tstart[k], str(q['bbox'][3] - 2)) for k, q in run]
                txt = (p.get('text', '') or '').replace('&', '&amp;').replace('<', '&lt;')
                a('<text x="%d" y="%d" font-family="\'Poiret One\',\'Limelight\',Georgia,serif" font-size="%d" '
                  'textLength="%d" lengthAdjust="spacingAndGlyphs" fill="%s">%s%s%s%s</text>' % (
                    x1, y2 - 2, y2 - y1, x2 - x1, c0, txt, vis, linear('x', keysx), linear('y', keysy)) + canim)
                # canim placed outside text content would be invalid; fix below
                out[-1] = out[-1].replace('</text>' + canim, canim + '</text>') if canim else out[-1]
            elif kd == 'bitmap':
                p = run[0][1]
                idx = p['h'][10]
                if idx >= len(bm_png):
                    continue
                bw, bh = bms[idx]['w'], bms[idx]['h']
                flip = bool(p['flags'] & 0x2000) and not (p['flags'] & 0x1000)
                def bxy(q):
                    cx, cy = q['pts'][0]
                    return cx - q['h'][11] / 2, cy - q['h'][12] / 2
                kx = [(tstart[k], fmt(bxy(q)[0])) for k, q in run]
                ky = [(tstart[k], fmt(bxy(q)[1])) for k, q in run]
                x0, y0 = bxy(p)
                a('<image x="%s" y="%s" width="%d" height="%d" preserveAspectRatio="none" '
                  'style="image-rendering:pixelated" href="data:image/png;base64,%s">%s%s%s</image>' % (
                    fmt(x0), fmt(y0), p['h'][11], p['h'][12], bm_png[idx], vis, linear('x', kx), linear('y', ky)))
    a('</g>')
    if sound:
        a(sound_block(mv, [tstart[k] * step for k in range(len(mv['frames']))], T, W_))
    a('</svg>')
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

def sound_block(mv, ktime, T, width):
    """Button + script playing the 8SVX samples in sync with the SMIL timeline (Web Audio).
    Events: [time, channel, op(0 start,1 change,2 stop), sound, volume, sample rate, oneshot, length]"""
    if not mv['sounds']: return ''
    ev = []
    for k, fr in enumerate(mv['frames']):
        fx = list(fr['sefx'])
        for ch, code in enumerate((fr['hdr'][5], fr['hdr'][6])):
            if code == -1: continue
            fxe = fx.pop(0) if fx else None
            if code >= 0 and fxe and code < len(mv['sounds']):
                ev.append([ktime[k], ch, 0, code, fxe['volume'] / 64,
                           round(AMIGA_CLOCK / max(fxe['period'], 1)), int(fxe['oneshot']), fxe['length']])
            elif code == -2 and fxe:
                ev.append([ktime[k], ch, 1, 0, fxe['volume'] / 64,
                           round(AMIGA_CLOCK / fxe['period']) if fxe['period'] else 0, 0, 0])
            else:
                ev.append([ktime[k], ch, 2, 0, 0, 0, 0, 0])
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
    st = step + mv['speed'] / 60   # speed word read as extra delay in 1/60 s ticks
    svg, T, total = build(mv, st, arrival='--arrival' in sys.argv, debug='--debug' in sys.argv,
                         sound='--no-sound' not in sys.argv)
    open(dst, 'w').write(svg)
    print('%s: %d keyframes, %d steps, %.1fs -> %s (%d bytes)' % (src, len(mv['frames']), total, T, dst, len(svg)))
