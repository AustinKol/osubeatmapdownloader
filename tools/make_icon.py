"""Render assets/icon.ico (the pink download circle from the UI) with Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
pink, white = (255, 102, 170, 255), (255, 255, 255, 255)
d.ellipse([20, 20, S - 20, S - 20], fill=pink)
r, w = 307, 82
d.ellipse([S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r], outline=white, width=w)
# download arrow
c, top, bot, arm = S / 2, 368, 624, 102
d.line([c, top, c, bot], fill=white, width=72)
d.line([c - arm, bot - arm, c, bot + 8], fill=white, width=72, joint="curve")
d.line([c + arm, bot - arm, c, bot + 8], fill=white, width=72, joint="curve")
for x, y in [(c, top), (c - arm, bot - arm), (c + arm, bot - arm), (c, bot + 8)]:
    d.ellipse([x - 36, y - 36, x + 36, y + 36], fill=white)

out = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
print("wrote", out)
