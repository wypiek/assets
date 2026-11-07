#!/usr/bin/env python3
"""Build a PNG/PDF/HTML polyglot (with a GIF embedded as a PDF attachment).

Usage: build_polyglot.py first.png second.png gif_stage_image out.png redirect_url
"""
import io, struct, sys, zlib
from PIL import Image

png_src, pdf_src, gif_src, out, url = sys.argv[1:6]

def chunk(ctype, data):
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

# --- PNG part: re-encode the first image so we control the chunk layout ---
img = Image.open(png_src).convert("RGB")
w, h = img.size
raw = b"".join(b"\x00" + img.tobytes()[y * w * 3:(y + 1) * w * 3] for y in range(h))
ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
idat = chunk(b"IDAT", zlib.compress(raw, 9))
iend = chunk(b"IEND", b"")
sig = b"\x89PNG\r\n\x1a\n"

# --- PDF part ---
pdf_img = Image.open(pdf_src).convert("RGB")
pw, ph = pdf_img.size
jbuf = io.BytesIO(); pdf_img.save(jbuf, "JPEG", quality=92)
jpeg = jbuf.getvalue()
gbuf = io.BytesIO(); Image.open(gif_src).save(gbuf, "GIF", save_all=True)
gif = gbuf.getvalue()
# HTML redirect, read by browsers when the file is opened as .html
html = (f'<html><head><meta charset="iso-8859-1"><meta http-equiv="refresh" content="0;url={url}">'
        f'<script>location.replace({url!r})</script>'
        '<style>html,body{background:#fff;color:#fff;overflow:hidden}</style></head><body><!--\n').encode()

# Early chunk (within first 1024 bytes): PDF header + open a dummy stream that
# swallows the PNG bytes in between (CRC, IDAT, next chunk header).
head_prefix = html + b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Length "
head_suffix = b" >>\nstream\n"
swallowed = 4 + len(idat) + 8  # early-chunk CRC + IDAT + late chunk length/type
length_str = str(swallowed).encode().rjust(10)
early_data = head_prefix + length_str + head_suffix
early = chunk(b"pdFh", early_data)

pdf_start = len(sig) + len(ihdr) + 8 + len(html)  # where %PDF begins in the file
html_len = len(html)
# offset (relative to %PDF) where the late chunk's data starts
late_rel = len(early_data) - html_len + swallowed

objs = []
content = f"q {pw} 0 0 {ph} 0 0 cm /Im0 Do Q".encode()
objs.append(b"<< /Type /Catalog /Pages 3 0 R /Names << /EmbeddedFiles << /Names [(next-step.gif) 7 0 R] >> >> /PageMode /UseAttachments >>")
objs.append(b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>")
objs.append(f"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 {pw} {ph}] /Contents 5 0 R /Resources << /XObject << /Im0 6 0 R >> >> >>".encode())
objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
objs.append(f"<< /Type /XObject /Subtype /Image /Width {pw} /Height {ph} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n".encode() + jpeg + b"\nendstream")
objs.append(b"<< /Type /Filespec /F (next-step.gif) /UF (next-step.gif) /EF << /F 8 0 R >> >>")
objs.append(b"<< /Type /EmbeddedFile /Subtype /image#2Fgif /Length %d >>\nstream\n" % len(gif) + gif + b"\nendstream")

late = bytearray(b"\nendstream\nendobj\n")
offsets = {1: len(head_prefix) - len(b"1 0 obj\n<< /Length ") }
offsets[1] = early_data.index(b"1 0 obj") - html_len
for i, body in enumerate(objs, start=2):
    offsets[i] = late_rel + len(late)
    late += b"%d 0 obj\n" % i + body + b"\nendobj\n"
xref_pos = late_rel + len(late)
n = len(objs) + 2
late += b"xref\n0 %d\n0000000000 65535 f \n" % n
for i in range(1, n):
    late += b"%010d 00000 n \n" % offsets[i]
late += b"trailer\n<< /Size %d /Root 2 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos)
late_chunk = chunk(b"pdFt", bytes(late))

data = sig + ihdr + early + idat + late_chunk + iend
assert data[pdf_start:pdf_start + 5] == b"%PDF-"
assert pdf_start < 1024
open(out, "wb").write(data)
print(f"wrote {out}: {len(data)} bytes, %PDF at offset {pdf_start}")
