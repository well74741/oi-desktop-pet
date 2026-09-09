# -*- coding: utf-8 -*-
"""Generate release icons for the aggregate AI panel and settings dialog."""

import math
import os

from PIL import Image, ImageDraw


SIZE = 256
SS = 4
CANVAS = SIZE * SS
BLUE = (74, 144, 226, 255)
TEAL = (47, 184, 192, 255)


def gradient(size):
    strip = Image.new("RGBA", (size, 1))
    for x in range(size):
        p = x / max(1, size - 1)
        strip.putpixel((x, 0), tuple(round(BLUE[i] * (1 - p) + TEAL[i] * p)
                                     for i in range(4)))
    return strip.resize((size, size))


def save(img, name):
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    img.save(os.path.join("assets", name), "PNG")


def webchat_icon():
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((24 * SS, 42 * SS, 232 * SS, 190 * SS),
                         radius=58 * SS, fill=255)
    md.polygon([(52 * SS, 168 * SS), (28 * SS, 226 * SS),
                (102 * SS, 182 * SS)], fill=255)
    img.paste(gradient(CANVAS), (0, 0), mask)
    d = ImageDraw.Draw(img)
    cx, cy, r, waist = 128 * SS, 116 * SS, 43 * SS, 11 * SS
    points = [(cx, cy - r), (cx + waist, cy - waist), (cx + r, cy),
              (cx + waist, cy + waist), (cx, cy + r),
              (cx - waist, cy + waist), (cx - r, cy),
              (cx - waist, cy - waist)]
    d.polygon(points, fill=(255, 255, 255, 246))
    d.ellipse((cx - 7 * SS, cy - 7 * SS, cx + 7 * SS, cy + 7 * SS),
              fill=(230, 245, 255, 246))
    return img


def settings_icon():
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((16 * SS, 16 * SS, 240 * SS, 240 * SS),
                                           radius=62 * SS, fill=255)
    img.paste(gradient(CANVAS), (0, 0), mask)
    gear = Image.new("L", (CANVAS, CANVAS), 0)
    d = ImageDraw.Draw(gear)
    cx, cy, root, tip = 128 * SS, 128 * SS, 59 * SS, 84 * SS
    teeth = 8
    half_width = math.pi / 18
    for i in range(teeth):
        center = (2 * math.pi * i) / teeth
        a0, a1 = center - half_width, center + half_width
        d.polygon([
            (cx + root * math.cos(a0), cy + root * math.sin(a0)),
            (cx + tip * math.cos(a0), cy + tip * math.sin(a0)),
            (cx + tip * math.cos(a1), cy + tip * math.sin(a1)),
            (cx + root * math.cos(a1), cy + root * math.sin(a1)),
        ], fill=255)
    d.ellipse((cx - root, cy - root, cx + root, cy + root), fill=255)
    d.ellipse((cx - 30 * SS, cy - 30 * SS,
               cx + 30 * SS, cy + 30 * SS), fill=0)
    white = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 250))
    img.paste(white, (0, 0), gear)
    return img


if __name__ == "__main__":
    save(webchat_icon(), "webchat_icon.png")
    save(settings_icon(), "settings_icon.png")
    print("icons created")
