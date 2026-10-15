#!/usr/bin/env python3
"""Exercise an isolated container, including real downloads and both caption choices."""

import json
import sys
import time
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

base = sys.argv[1].rstrip('/')


def get(path, headers=None):
    return urlopen(Request(base + path, headers=headers or {}), timeout=60)


with get('/') as response:
    page = response.read().decode()
    assert 'RiiTube at Home' in page
    assert 'Add hardsubs if available?' in page
with get('/static/riitube-logo.svg') as response:
    assert b'<svg' in response.read()
with get('/onlinemedia.xml') as response:
    folder = ET.fromstring(response.read()).find('folder')
    assert folder.attrib['name'] == 'RiiTube at Home'
    assert all(entry.attrib['addr'].startswith(base + '/') for entry in folder)
with get('/api/search?q=Ahoy%20trackers') as response:
    assert json.load(response), 'Search returned no results'
print('Page, logo, LAN playlists and live search passed.', flush=True)

for vid, hardsubs in [('jNQXAC9IVRw', False), ('roBkg-iPrbw', True)]:
    request = Request(
        base + '/api/prepare',
        method='POST',
        data=json.dumps({'video': vid, 'hardsubs': hardsubs}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urlopen(request, timeout=10) as response:
        assert response.status == 202
    started = time.monotonic()
    last_report = ''
    while True:
        with get('/api/jobs') as response:
            job = next(item for item in json.load(response) if item['id'] == vid)
        report = f"{vid}: {job['status']} — {job['detail']}"
        if report != last_report:
            print(report, flush=True)
            last_report = report
        if job['status'] in {'ready', 'error'}:
            break
        if time.monotonic() - started > 1200:
            raise TimeoutError('Preparation exceeded the smoke-test time budget')
        time.sleep(10)
    assert job['status'] == 'ready', job
    assert job['hardsubs'] is hardsubs
    assert job['wii_format'] == 'mkv'
    if hardsubs:
        assert job['captions'] == {'kind': 'manual', 'language': 'en-GB'}
    else:
        assert job['captions'] is None
    with get(f'/wiimc/video/{vid}.plx') as response:
        playlist = response.read().decode()
        assert f'{base}/media/{vid}.mkv' in playlist
        assert '(English hardsubs)' not in playlist
    with get(f'/media/{vid}.mkv', {'Range': 'bytes=0-1023'}) as response:
        assert response.status == 206
        assert response.headers['Accept-Ranges'] == 'bytes'
        assert len(response.read()) == 1024
    with get(f'/thumb/{vid}.jpg') as response:
        assert response.headers.get_content_type() == 'image/jpeg'
        assert response.read().startswith(b'\xff\xd8')
print(
    'Both caption choices, download/encode, MKV playback routes, thumbnails and range requests passed.',
    flush=True,
)
