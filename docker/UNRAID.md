# Unraid setup

Build the image from the repository on Unraid:

```sh
docker build -f docker/Dockerfile -t riitube-at-home:local .
```

In **Docker → Add Container**, use:

| Setting | Value |
| --- | --- |
| Name | `riitube-at-home` |
| Repository | `riitube-at-home:local` |
| Network | Bridge |
| Port | Host 301 → container 301, TCP |
| Path | Your library folder → `/data`, read/write |
| Variable | `RIIRIITUBE_URL=http://YOUR-UNRAID-IP:301` |
| WebUI | `http://[IP]:[PORT:301]/` |

Use a dedicated library folder with enough space for downloaded sources and
prepared videos. Give that folder UID/GID **10001:10001** ownership. This image
does not use `PUID` or `PGID` variables.

Open the WebUI, then follow **Wii Setup**. Enable container autostart after checking
that preparation and playback work.

To migrate an existing library, finish playback and preparation, stop the old
server, and copy its complete `/data` contents into the new library folder.
Keep the old library as a rollback copy until the new server is verified.
Never run both servers against the same writable folder.
