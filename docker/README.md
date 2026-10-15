# Docker setup

From this directory, copy `.env.example` to `.env`, set `LAN_IP`, and run
`docker compose up -d --build`. The image includes Python, yt-dlp and EJS,
Node.js 22, FFmpeg, and Noto Sans. It runs as UID/GID **10001:10001**.

The default address is `http://YOUR-LAN-IP:301`. `PORT` changes the published port;
the container still listens on 301. `RIIRIITUBE_URL` must be the address the Wii
can reach, rather than a container address or `0.0.0.0`.

## Wii setup

Add this folder inside `<file>` in your existing `onlinemedia.xml`, replacing
the example address with your server's address:

```xml
<folder name="RiiTube at Home">
  <link name="Search YouTube" addr="http://192.168.1.50:301/wiimc/search.plx?q=" type="search" />
  <link name="Prepared videos and progress" addr="http://192.168.1.50:301/wiimc/library.plx" type="playlist" />
</folder>
```

The server's **Wii Setup** section supplies this configuration for your address.
Back up the XML and preserve other providers. Put it in `apps/wiimc` and beside
WiiMC-SS's `boot.dol`, then relaunch WiiMC.

## Manage

```sh
docker compose logs -f
docker compose stop
docker compose start
```

After pulling source updates, run `docker compose up -d --build`. Wait until
playback and preparation finish before restarting. Interrupted preparations
can be retried.

The `videos` volume contains the library, captions, thumbnails, and job history.
`docker compose down` keeps it; `docker compose down -v` deletes it.
For a bind mount, give the application folder UID/GID 10001 ownership.
Run one server per writable library.

The Docker build includes application source, the AGPLv3 license, and asset
credits. Downloaded videos, local configuration, and research folders are excluded.
