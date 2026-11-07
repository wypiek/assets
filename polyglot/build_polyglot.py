#!/usr/bin/env python3
"""Build a PNG/PDF/HTML polyglot.

  .png  -> shows first.png
  .pdf  -> shows second.png
  .html -> shows a "Now remove the letter l from html." message
  .htm  -> shows the "how this works" explainer

Usage: build_polyglot.py first.png second.png out.png
Also writes how-it-works.html (the explainer on its own) next to out.png.
"""
import io, os, struct, sys, zlib
from PIL import Image

png_src, pdf_src, out = sys.argv[1:4]
here = os.path.dirname(os.path.abspath(__file__))

def chunk(ctype, data):
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

# --- PNG part: re-encode the first image so we control the chunk layout ---
img = Image.open(png_src).convert("RGB")
w, h = img.size
raw = b"".join(b"\x00" + img.tobytes()[y * w * 3:(y + 1) * w * 3] for y in range(h))
sig = b"\x89PNG\r\n\x1a\n"
ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
idat = chunk(b"IDAT", zlib.compress(raw, 9))
iend = chunk(b"IEND", b"")

# --- HTML bootstrap (must fit, with the PDF header, in the first 1024 bytes) ---
# Hides the binary junk, then swaps the whole page for one of two payloads
# depending on whether the file name ends in .htm or .html.
boot = (b'<html><head><meta charset="iso-8859-1"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Almost there</title>'
        b'<style>body{font-size:0;color:transparent;margin:0}textarea{display:none}</style></head><body>'
        b'<textarea hidden>')  # everything up to </textarea> (PDF header, PNG pixels) is hidden text

# --- Early chunk: HTML bootstrap + PDF header + a dummy stream that
# swallows the PNG bytes in between (CRC, IDAT, next chunk header). ---
head_prefix = boot + b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Length "
head_suffix = b" >>\nstream\n"
swallowed = 4 + len(idat) + 8
early_data = head_prefix + str(swallowed).encode().rjust(10) + head_suffix
early = chunk(b"pdFh", early_data)

pdf_start = len(sig) + len(ihdr) + 8 + len(boot)
idat_off = len(sig) + len(ihdr) + len(early)
pdft_off = idat_off + len(idat)
late_rel = len(early_data) - len(boot) + swallowed  # late data start, relative to %PDF

# --- HTML payloads ---

tmpl = open(os.path.join(here, "explainer_template.html"), encoding="utf-8").read()
for k, v in {"PDF_START": pdf_start, "PDFH_OFF": 33, "IDAT_OFF": idat_off, "PDFT_OFF": pdft_off,
             "LENGTH": swallowed}.items():
    tmpl = tmpl.replace("{{" + k + "}}", str(v))
explainer_full = tmpl

body = tmpl[tmpl.index("<body>") + 6:tmpl.index("</body>")]
page_html = (
    "</textarea><style>"
    "#m{font:bold 8vmin sans-serif;color:#000;min-height:100vh;display:flex;align-items:center;"
    "justify-content:center;text-align:center;padding:1em;box-sizing:border-box}"
    "#x{font:16px/1.5 sans-serif;color:#000;max-width:46em;margin:1em auto;padding:0 1em}"
    "#x pre{overflow-x:auto}#x table{border-collapse:collapse}"
    "#x table{font-size:16px;color:#000}#x td,#x th{border:1px solid #999;padding:.25em .5em;text-align:left}"
    "</style>"
    "<div id='m'><p>Now remove the letter <span style='color:red'>l</span> from html.</p></div>"
    "<div id='x' style='display:none'>" + body + "</div>"
    "<script>if(/\\.htm$/i.test(location.pathname)){document.getElementById('m').style.display='none';"
    "document.getElementById('x').style.display='block';document.title='How special.png works'}</script>"
    "<textarea hidden>"  # hide the rest of the file (JPEG, xref)
)
assert "textarea" not in body.lower()
payload_stream = page_html.encode("ascii", "xmlcharrefreplace")

# --- PDF objects ---
pdf_img = Image.open(pdf_src).convert("RGB")
pw, ph = pdf_img.size
jbuf = io.BytesIO(); pdf_img.save(jbuf, "JPEG", quality=92)
jpeg = jbuf.getvalue()
content = f"q {pw} 0 0 {ph} 0 0 cm /Im0 Do Q".encode()

def stream(dict_bytes, data):
    return dict_bytes.replace(b">>", b" /Length %d >>" % len(data), 1) + b"\nstream\n" + data + b"\nendstream"

objs = [  # (number, body) in file order; the HTML payload comes first, before any image data
    (7, stream(b"<< >>", b"\n" + payload_stream)),
    (2, b"<< /Type /Catalog /Pages 3 0 R >>"),
    (3, b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>"),
    (4, f"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 {pw} {ph}] /Contents 5 0 R /Resources << /XObject << /Im0 6 0 R >> >> >>".encode()),
    (5, stream(b"<< >>", content)),
    (6, stream(f"<< /Type /XObject /Subtype /Image /Width {pw} /Height {ph} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode >>".encode(), jpeg)),
]

late = bytearray(b"\nendstream\nendobj\n")
offsets = {1: early_data.index(b"1 0 obj") - len(boot)}
for num, body in objs:
    offsets[num] = late_rel + len(late)
    late += b"%d 0 obj\n" % num + body + b"\nendobj\n"
xref_pos = late_rel + len(late)
n = max(offsets) + 1
late += b"xref\n0 %d\n0000000000 65535 f \n" % n
for i in range(1, n):
    late += b"%010d 00000 n \n" % offsets[i]
late += b"trailer\n<< /Size %d /Root 2 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos)
late_chunk = chunk(b"pdFt", bytes(late))

data = sig + ihdr + early + idat + late_chunk + iend
assert data[pdf_start:pdf_start + 5] == b"%PDF-"
assert pdf_start < 1024
assert data[pdft_off + 4:pdft_off + 8] == b"pdFt"
open(out, "wb").write(data)
open(os.path.join(os.path.dirname(os.path.abspath(out)), "how-it-works.html"), "w", encoding="utf-8").write(explainer_full)
print(f"wrote {out}: {len(data)} bytes, %PDF at offset {pdf_start}")
