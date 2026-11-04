# 114s

A photo exchange site for two phones that have nothing in common: an iPhone 4S
on iOS 9 (Safari only, no apps, **no working TLS trust**) and a modern iPhone.

Plain Python 3 standard library. No dependencies, no database, no build step.
Photos and videos are files in a directory.

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
- True-black OLED surfaces: `#000` page and header, near-black tiles, no
  colour cast. The icon is where the purple lives. It is colour, not requests:
  no extra images, fonts or scripts.
- Tapping a tile opens a full-screen viewer — swipe, the two buttons, or
  arrows/Esc on a laptop. Files dropped on the page upload too. Both are ES5
  and fall back to plain links with no JavaScript.
- `/?sort=oldest` flips the gallery order; the toggle beside the uploader
  links to it, so the order survives a reload and can be bookmarked.

## Run it

```bash
cd photobridge
python3 app.py                   # http://<your-ip>:8080
```

| Env | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | Listen port |
| `STORE` | `./uploads` | Where photos live |
| `MAX_MB` | unset | Optional ceiling for one file; unset means the free disk |

## Test

```bash
python3 test_app.py
```

Twelve checks: filename traversal, byte-range serving, the sort toggle, an
original download plus a readable streamed zip, remove-all refusing anything
but the word DELETE, the login routes being gone, delete rejecting traversal,
and a live round trip covering upload, gallery and delete. The upload path adds
four of its own: a 512 KB file survives byte for byte, a body that stops early
is refused and stores nothing, a chunked body (what the 4S sends) parses, and
an oversized request is refused with the connection closed instead of poisoned.

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


## Videos

`.mov` and `.mp4` take the same route as photos: same multipart upload, stored
byte for byte, never transcoded. The grid draws a play glyph rather than a
poster frame — a frame would need ffmpeg, and this project has no dependencies.
Tapping a clip hands it to iOS's player, which fullscreens it on the 4S; there
is no inline video on iOS 9.

Two things decide whether a clip actually plays:

- `/media/` answers **byte ranges** (`206`). iOS refuses to play video from a
  server that replies `200` to a range request, and ranges also keep a long
  clip out of memory when only part of it is being read.
- The 4S is an A5 on iOS 9 and **cannot decode HEVC**. A modern iPhone records
  HEVC 4K by default, so those clips will download but not play on the 4S.
  Record in *Settings → Camera → Formats → Most Compatible* (H.264) at 1080p
  or lower if the 4S has to play it. Fixing that server-side would mean
  re-encoding, which is exactly what this app refuses to do.

Uploads stream straight to disk, so a 2 GB clip costs disk space rather than
RAM, and the server never holds a whole video in memory. A request that stops
early is refused (`400`, nothing saved) instead of being stored at whatever
length arrived, and the page knows the size limit and refuses an oversized
pick before it starts. Videos fill a disk far faster than photos; the footer
shows the total, and nothing is deleted automatically.

## Getting files out

- `/download/<name>` sends the original bytes with `Content-Disposition:
  attachment`. The viewer's **Save** link points there. A laptop saves the file;
  iOS 13+ puts it in Files. Safari on iOS 9 has no save sheet for video, so
  there a clip still just plays.
- **Download all** — laptop widths only — streams the whole store as one zip
  from `/download-all`. Entries are stored, not compressed: JPEG and H.264 are
  already packed, so zipping costs no CPU and needs no temp file, but it is
  still every byte you have.
- **Remove all** in the footer asks you to type `DELETE`, then removes every
  file permanently. No login, no trash, no undo: anyone who can reach the page
  can fire it.

## Security, plainly

- There is no login. Anyone who can reach the URL can see, add and delete
  photos; that is the trade this app makes on purpose.
- On the 4S leg every photo crosses the network **in cleartext**. Use it on a
  network you trust or over a VPN; public hosts get found by scanners.
- Uploads are validated by extension and stored under a generated name; deletes
  accept a bare filename only, resolved inside `STORE`.
- Serve it to the internet and you own that exposure. Bind it to a VPN instead
  if the only clients are your own phones.
