# Photo Bridge

A photo exchange site for two phones that have nothing in common: an iPhone 4S
on iOS 9 (Safari only, no apps, **no working TLS trust**) and a modern iPhone.

Plain Python 3 standard library. No dependencies, no database, no build step.
Photos are files in a directory.

## Why it looks like this

The 4S rejects every TLS chain we can serve it, so the site must answer on
**plain HTTP**. That single fact drives the design:

- PIN login + HttpOnly session cookie (HMAC-signed, 30 days) instead of OAuth
  or anything that needs TLS.
- No thumbnails, no CDN, no external fonts — one CSS file, one JS file.
- Layout starts at 320 pt (iPhone 4S) and steps up at 375 pt (iPhone 11).
- ES5 JavaScript: no `fetch`, no `NodeList.forEach`, no arrow functions.
- No CSS grid or `object-fit`: iOS 9 Safari doesn't have them. Flexbox with
  `-webkit-` prefixes and a fixed-height crop instead.

## Run it

```bash
cd photobridge
PIN=2468 python3 app.py          # http://<your-ip>:8080
```

| Env | Default | Meaning |
|---|---|---|
| `PIN` | *(empty = completely open)* | Shared PIN; leave unset to disable auth entirely |
| `PORT` | `8080` | Listen port |
| `STORE` | `./uploads` | Where photos live; also holds `.secret` |
| `MAX_MB` | `100` | Per-request upload cap |

## Test

```bash
python3 test_app.py
```

Five checks: token round-trip, token tampering, filename traversal, and a live
HTTP round trip covering 403-without-cookie, login, upload, gallery, delete.

## Deploy

Any box with Docker works; the goal is one port the 4S can reach without TLS.

**One-shot on a fresh VPS** (Ubuntu/Debian, root):

```bash
PIN=246801 HOST_PORT=80 bash deploy/bootstrap.sh
```

It installs Docker, clones this repo to `/opt/ipbridge`, writes `.env`, starts
the container on port 80, and prints the URL. Open the port in your provider's
firewall/security list afterwards.

**Manual:**

```bash
printf 'PIN=246801\nHOST_PORT=80\n' > .env     # PIN is required, compose refuses to start without it
docker compose up -d --build
```

The `photos` volume is the part that matters — container filesystems are
ephemeral, so without it every redeploy deletes the photos. `deploy/nginx.conf`
is only needed if you want TLS on 443 for modern phones while keeping 80 plain
for the 4S.


## Security, plainly

- On the 4S leg, the PIN and every photo cross the network **in cleartext**.
  Anyone who can see that traffic can read both. Use it over a VPN or on a
  network you trust; do not treat the PIN as encryption.
- The PIN is rate-limited (6 tries / 5 min per IP) and compared in constant
  time, but it is a door, not a vault.
- With no PIN set the gallery is open to anyone who has the URL. That is a
  deliberate mode, not a bug: public hosts get found by scanners.
- Uploads are validated by extension and stored under a generated name; deletes
  accept a bare filename only, resolved inside `STORE`.
- Serve it to the internet and you own that exposure. Bind it to a VPN instead
  if the only clients are your own phones.
