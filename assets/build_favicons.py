#!/usr/bin/env python3
"""Render the RiiTube favicon suite using existing artwork, Inkscape and Pillow."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'static'
SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)
reference = ET.parse(ROOT / 'assets/youtube-2006-reference.svg').getroot()
parents = {child: parent for parent in reference.iter() for child in parent}


def path_in_document(identifier):
    original = next(e for e in reference.iter() if e.get('id') == identifier)
    transforms = []
    ancestor = parents.get(original)
    while ancestor is not None:
        if ancestor.get('transform'):
            transforms.insert(0, ancestor.get('transform'))
        ancestor = parents.get(ancestor)
    group = ET.Element(f'{{{SVG}}}g', transform=' '.join(transforms))
    path = deepcopy(original)
    path.attrib.pop('style', None)
    group.append(path)
    return group


icon = ET.Element(f'{{{SVG}}}svg', viewBox='0 0 512 512', width='512', height='512')
ET.SubElement(icon, f'{{{SVG}}}title').text = 'RiiTube at Home'
ET.SubElement(icon, f'{{{SVG}}}rect', width='512', height='512', fill='white')
# Retain the 2005 favicon's stacked proportions, with clean paths at larger sizes.
rii = ET.SubElement(icon, f'{{{SVG}}}g', transform='translate(140 8) scale(5.021645 4.740741) translate(-3.05 -8.2)', fill='#111111')
wordmark = ET.parse(OUT / 'riitube-logo.svg').getroot()
letters_rii = deepcopy(next(e for e in wordmark.iter() if e.get('id') == 'rii'))
letters_rii.attrib.pop('style', None)
rii.append(letters_rii)
tube = ET.SubElement(icon, f'{{{SVG}}}g', transform='translate(32 224) scale(0.676592 0.581423) translate(-442.429 0)')
badge = path_in_document('path17')
badge.set('fill', '#ff1515')
tube.append(badge)
letters = path_in_document('rect3947-6')
letters.set('fill', '#ffffff')
tube.append(letters)
ET.indent(icon)
ET.ElementTree(icon).write(OUT / 'favicon.svg', encoding='unicode', xml_declaration=True)

pinned = deepcopy(icon)
pinned.remove(pinned.find(f'{{{SVG}}}rect'))
for e in pinned.iter():
    if e.get('fill') == '#111111' or e.get('fill') == '#ff1515':
        e.set('fill', '#000000')
# A mask must have transparent letter cutouts, rather than white silhouettes.
mask = ET.SubElement(pinned, f'{{{SVG}}}defs')
m = ET.SubElement(mask, f'{{{SVG}}}mask', id='letter-cutouts', maskUnits='userSpaceOnUse', x='0', y='0', width='512', height='512')
ET.SubElement(m, f'{{{SVG}}}rect', width='512', height='512', fill='white')
cutouts = deepcopy(pinned[2])
cutouts.remove(cutouts[0])
cutouts[0].set('fill', 'black')
m.append(cutouts)
pinned[2].remove(pinned[2][1])
pinned[2].set('mask', 'url(#letter-cutouts)')
# Apply the mask in document coordinates around the transformed badge.
pinned[2].attrib.pop('mask')
badge_group = pinned[2]
pinned.remove(badge_group)
wrapper = ET.SubElement(pinned, f'{{{SVG}}}g', mask='url(#letter-cutouts)')
wrapper.append(badge_group)
ET.ElementTree(pinned).write(OUT / 'safari-pinned-tab.svg', encoding='unicode', xml_declaration=True)

with tempfile.TemporaryDirectory() as tmp:
    rendered = Path(tmp) / 'icon.png'
    subprocess.run(['inkscape', str(OUT / 'favicon.svg'), '--export-width=1024', '--export-filename=' + str(rendered)], check=True, capture_output=True)
    large = Image.open(rendered).convert('RGBA')
    sizes = [16, 32, 48, 64, 96, 128, 180, 192, 256, 512]
    images = {size: large.resize((size, size), Image.Resampling.LANCZOS) for size in sizes}
    for size in [16, 32, 48, 64, 96, 128, 256]:
        images[size].save(OUT / f'favicon-{size}x{size}.png')
    images[64].save(OUT / 'favicon.png')
    images[180].save(OUT / 'apple-touch-icon.png')
    for size in [192, 512]:
        images[size].save(OUT / f'android-chrome-{size}x{size}.png')
    large.resize((150, 150), Image.Resampling.LANCZOS).save(OUT / 'mstile-150x150.png')
    maskable = Image.new('RGBA', (512, 512), 'white')
    maskable.paste(large.resize((328, 328), Image.Resampling.LANCZOS), (92, 92))
    maskable.save(OUT / 'android-chrome-maskable-512x512.png')
    images[256].save(OUT / 'favicon.ico', sizes=[(s, s) for s in [16, 32, 48, 64, 128, 256]], append_images=[images[s] for s in [16, 32, 48, 64, 128]])

manifest = {
    'name': 'RiiTube at Home', 'short_name': 'RiiTube', 'id': '/',
    'start_url': '/', 'scope': '/', 'display': 'standalone',
    'background_color': '#ffffff', 'theme_color': '#cc0000',
    'icons': [
        {'src': f'android-chrome-{s}x{s}.png', 'sizes': f'{s}x{s}', 'type': 'image/png', 'purpose': 'any'}
        for s in [192, 512]
    ] + [{'src': 'android-chrome-maskable-512x512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}],
}
(OUT / 'site.webmanifest').write_text(json.dumps(manifest, indent=2) + '\n')
(OUT / 'browserconfig.xml').write_text('<?xml version="1.0" encoding="utf-8"?>\n<browserconfig><msapplication><tile><square150x150logo src="/static/mstile-150x150.png"/><TileColor>#ffffff</TileColor></tile></msapplication></browserconfig>\n')
