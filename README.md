# 114s

A photo exchange site for two phones that have nothing in common: an iPhone 4S
on iOS 9 (Safari only, no apps, **no working TLS trust**) and a modern iPhone.

Plain Python 3 standard library. No dependencies, no database, no build step.
Photos are files in a directory.

## Why it looks like this

The 4S rejects every TLS chain we can serve it, so the site must answer on
**plain HTTP**. That single fact drives the design:

- No login, no cookies: anyone who reaches the URL gets the gallery.
- No thumbnails, no CDN, no external requests — one CSS file, one JS file,
  and Space Grotesk self-hosted in `static/` (the 4S can't reach Google Fonts:
  it rejects the TLS chain, and iOS 9 has no WOFF2).
- Layout starts at 320 pt (iPhone 4S) and steps up at 375 pt (iPhone 11).
- ES5 JavaScript: no `fetch`, no `NodeList.forEach`, no arrow functions.
- No CSS grid or `object-fit`: iOS 9 Safari doesn't have them. Flexbox with
  `-webkit-` prefixes and a fixed-height crop instead.

## Run it

```bash
cd photobridge
python3 app.py                   # http://<your-ip>:8080
```

| Env | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | Listen port |
| `STORE` | `./uploads` | Where photos live |
| `MAX_MB` | `100` | Per-request upload cap |

## Test

```bash
python3 test_app.py
```

Four checks: filename traversal, the login routes being gone, delete rejecting
traversal, and a live round trip covering upload, gallery and delete.

## Deploy

Any box with Docker works; the goal is one port the 4S can reach without TLS.

**One-shot on a fresh VPS** (Ubuntu/Debian, root):

```bash
HOST_PORT=80 bash deploy/bootstrap.sh
```

It installs Docker, clones this repo to `/opt/ipbridge`, writes `.env`, starts
the container on port 80, and prints the URL. Open the port in your provider's
firewall/security list afterwards.

**Manual:**

```bash
printf 'HOST_PORT=80\n' > .env                 # optional: compose defaults to 8080
docker compose up -d --build
```

The `photos` volume is the part that matters — container filesystems are
ephemeral, so without it every redeploy deletes the photos. `deploy/nginx.conf`
is only needed if you want TLS on 443 for modern phones while keeping 80 plain
for the 4S.


## Security, plainly

- There is no login. Anyone who can reach the URL can see, add and delete
  photos; that is the trade this app makes on purpose.
- On the 4S leg every photo crosses the network **in cleartext**. Use it on a
  network you trust or over a VPN; public hosts get found by scanners.
- Uploads are validated by extension and stored under a generated name; deletes
  accept a bare filename only, resolved inside `STORE`.
- Serve it to the internet and you own that exposure. Bind it to a VPN instead
  if the only clients are your own phones.
