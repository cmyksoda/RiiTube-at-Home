# RiiTube at Home

A LAN companion for WiiMC-SS. Search YouTube, prepare a video on your server,
and watch a complete, seekable copy on the Wii.

- Search with thumbnails and browse multiple result pages in WiiMC.
- Queue videos and check preparation progress from WiiMC or a browser.
- Prepare H.264/AAC video for Wii playback in MKV, with MP4 for browsers.
- Optionally burn in creator-provided English subtitles; automatic captions are skipped.

## Run

Install Docker, then clone this repository:

```sh
git clone https://github.com/cmyksoda/RiiTube-at-Home.git
cd RiiTube-at-Home/docker
cp .env.example .env
# Set LAN_IP in .env to your server's LAN address.
docker compose up -d --build
```

Open `http://YOUR-LAN-IP:301` and use **Wii Setup** to add the RiiTube folder to
WiiMC's `onlinemedia.xml`. Back up the XML and keep your existing providers.
Relaunch WiiMC, search or select a video, and choose **Prepare for Wii**.
Playback becomes available when preparation finishes.

Videos are stored in a persistent Docker volume. The server downloads and
converts the whole video first, so longer videos need time and disk space.
Keep the server awake and use a trusted home LAN.

See [Docker setup](docker/README.md) and [Unraid setup](docker/UNRAID.md) for details.

## Native setup

Requires Python 3, FFmpeg, Node.js 22 or newer, and Noto Sans for subtitles.
Create `.venv`, install `requirements.txt`, and run `./start.sh`.
Set `RIIRIITUBE_URL` to the address your Wii can reach; the native default port is 8877.

Run tests with `python3 -m unittest discover -s tests -v` in the installed environment.

## License

Original code is licensed under [GNU AGPLv3](LICENSE). Fonts and referenced
artwork retain their own notices; see [asset credits](assets/README.md).
Inspired by [Larsenv's RiiTube](https://github.com/Larsenv/RiiTube).
