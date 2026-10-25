"""▶️ NÁHLED – hrubé sestavení editu z tvých souborů na PC, k přehrání v prohlížeči (PC, mobil doma, přes Tailscale kdekoli).

Bere projekty z S:\\EDIT\\analyza\\<projekt> (to, co tam nechává Auto Edit):
  base.drt (tvůj střih: V1 hra, V2 webka), mic.wav (mikrofon podle střihů), plan_v4.json (velikost webky, punch),
  plan_bw.json (černobílé), plan_music.json (hudba), chapters.json + moods.json.
Zdrojová videa jsou obří (HEVC 23 Mb/s, webka 30 Mb/s) → jednou se z nich udělají lehké kopie (540p, ~1,2 Mb/s)
do S:\\EDIT\\_cache\\nahled. Nic se nenahrává na internet – prohlížeč si je bere přímo z tohohle PC.

Spuštění: S:\\NÁPADY\\BRAWL TRENDY\\NÁHLED.bat  (pak adresa v okně; heslo je v odkazu)
Bezpečnost: posílá JEN lehké kopie a písničky z plánu daného projektu, nic jiného z disku; bez hesla nic.
"""
import binascii
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET

PORT = 8790
ANALYZA = r"S:\EDIT\analyza"
CACHE = r"S:\EDIT\_cache\nahled"
TOOLS = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(TOOLS, "nahled_heslo.txt")   # jen na tomhle PC – NEnahrávat na GitHub
PLAYER = os.path.join(TOOLS, "nahled_prehravac.html")
SLOZKA_DISK = r"G:\Můj disk\Brawl Trendy – data"     # odkaz pro web (záložka Výroba)
TL_FPS = 60.0

os.makedirs(CACHE, exist_ok=True)
if not os.path.exists(TOKEN_FILE):
    open(TOKEN_FILE, "w").write(secrets.token_urlsafe(12))
TOKEN = open(TOKEN_FILE).read().strip()
FFMPEG = shutil.which("ffmpeg") or r"S:\DABING\_pro_vyvojare\.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"


# ---------------------------------------------------------------- data projektu
def _drt_seq(path):
    z = zipfile.ZipFile(path)
    seqs = [n for n in z.namelist() if n.startswith("SeqContainer/")]
    raw = z.read(max(seqs, key=lambda n: z.getinfo(n).file_size)).decode("utf-8")
    # tagy s „::“ nejsou platné XML (stejně jako v S:\EDIT\tools\drt_lib.py)
    return ET.fromstring(re.sub(r"(</?)([A-Za-z0-9_]+)::([A-Za-z0-9_]+)", lambda m: f"{m[1]}{m[2]}__CC__{m[3]}", raw))


def _fps(hexs):
    try:
        return struct.unpack("<d", binascii.unhexlify((hexs or "").strip()[:16]))[0] or TL_FPS
    except Exception:  # noqa: BLE001
        return TL_FPS


def _rd(proj, f, default=None):
    try:
        return json.load(open(os.path.join(ANALYZA, proj, f), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def proxy_name(path):
    st = os.stat(path)
    return hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:16]


_CACHE = {}


def projekt(proj):
    """Totéž co projekt_now, ale pamatuje si výsledek, dokud se soubory projektu nezmění (přehrávač se ptá často)."""
    folder = os.path.join(ANALYZA, proj)
    key = tuple(os.path.getmtime(os.path.join(folder, f)) if os.path.exists(os.path.join(folder, f)) else 0
                for f in ("base.drt", "plan_v4.json", "plan_bw.json", "plan_music.json", "chapters.json", "mic.wav"))
    hit = _CACHE.get(proj)
    if hit and hit[0] == key and time.time() - hit[2] < 5:
        d = hit[1]
    else:
        d = projekt_now(proj)
        _CACHE[proj] = (key, d, time.time())
    for f in d["_files"]:  # stav lehkých kopií vždy čerstvě
        f["ready"] = os.path.exists(os.path.join(CACHE, f["id"] + ".mp4"))
    d["soubory"] = [{k: f[k] for k in ("name", "id", "ready", "webka")} for f in d["_files"]]
    d["mic_ready"] = bool(d["mic"]) and os.path.exists(os.path.join(CACHE, proxy_name(d["_mic"]) + ".m4a"))
    return d


def projekt_now(proj):
    """Vše pro přehrávač: klipy V1/V2 [začátek, konec, soubor, čas ve zdroji] v sekundách timeliny + plány."""
    seq = _drt_seq(os.path.join(ANALYZA, proj, "base.drt"))
    files, fidx, stopy = [], {}, {}
    for kind, vec in (("V", "VideoTrackVec"),):
        for ti, el in enumerate(seq.find(vec).findall("Element"), 1):
            tr = el.find("Sm2TiTrack")
            kl = []
            for it in tr.find("Items").findall("Element"):
                c = it[0]
                p = c.findtext("MediaFilePath")
                if not p or not os.path.exists(p):
                    continue
                try:
                    s, d, i = float(c.findtext("Start")), float(c.findtext("Duration")), float(c.findtext("In") or 0)
                except Exception:  # noqa: BLE001
                    continue
                if p not in fidx:
                    fidx[p] = len(files)
                    files.append({"path": p, "name": os.path.basename(p), "id": proxy_name(p), "webka": "webka" in p.lower()})
                kl.append([round(s / TL_FPS, 3), round((s + d) / TL_FPS, 3), fidx[p], round(i / _fps(c.findtext("MediaFrameRate")), 3)])
            stopy[f"{kind}{ti}"] = sorted(kl)
    for f in files:
        f["ready"] = os.path.exists(os.path.join(CACHE, f["id"] + ".mp4"))
    v4 = _rd(proj, "plan_v4.json", {}) or {}
    cam = sorted((int(k), s) for k, s in (v4.get("cam") or {}).items())
    music = _rd(proj, "plan_music.json", []) or []
    mic = os.path.join(ANALYZA, proj, "mic.wav")
    delka = max([k[1] for t in stopy.values() for k in t] or [0])
    return {
        "projekt": proj, "delka": delka, "soubory": [{k: f[k] for k in ("name", "id", "ready", "webka")} for f in files],
        "v1": stopy.get("V1", []), "v2": stopy.get("V2", []),
        "intro": ((_rd(proj, "intro.json", {}) or {}).get("intro_end") or 0) / TL_FPS,
        "cam": [[round(f / TL_FPS, 3), s] for f, s in cam],
        "punch": [round(int(f) / TL_FPS, 3) for f, s in (v4.get("game") or {}).items() if s == "punch"],
        "bw": [[round(x["s"] / TL_FPS, 3), round((x["s"] + x.get("d", 60)) / TL_FPS, 3)] for x in _rd(proj, "plan_bw.json", []) or []],
        "hudba": [[round(x["s"], 3), round(x["e"], 3), i, round(x.get("src", 0), 3), x.get("vol", -20),
                   os.path.splitext(os.path.basename(x.get("song", "")))[0]] for i, x in enumerate(music) if os.path.exists(x.get("path", ""))],
        "kapitoly": [[c["s"], c["e"], m] for c, m in zip(_rd(proj, "chapters.json", []) or [], _rd(proj, "moods.json", []) or [])],
        "mic": os.path.exists(mic), "mic_ready": os.path.exists(os.path.join(CACHE, proxy_name(mic) + ".m4a")) if os.path.exists(mic) else False,
        "_files": files, "_music": music, "_mic": mic,
    }


def projekty():
    out = []
    for n in os.listdir(ANALYZA):
        b = os.path.join(ANALYZA, n, "base.drt")
        if os.path.exists(b):
            out.append((os.path.getmtime(b), n))
    return [n for _, n in sorted(out, reverse=True)]


# ---------------------------------------------------------------- lehké kopie (ffmpeg, grafika NVENC)
PRIPRAVA = {}  # projekt -> {"hotovo": n, "celkem": n, "bezi": bool, "chyba": str, "aktualni": str}


def _ffmpeg(args):
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, errors="replace")
    return r.returncode == 0, r.stderr[-500:]


def _proxy(src, dst, webka):
    tmp = dst + ".part.mp4"
    common = ["-vf", f"scale=-2:{360 if webka else 540}", "-r", "30", "-g", "15", "-movflags", "+faststart"]
    # webka: bez zvuku (mikrofon jde z mic.wav, ať není dvakrát); hra: zvuk hry mono
    audio = ["-an"] if webka else ["-c:a", "aac", "-b:a", "96k", "-ac", "1"]
    for enc in (["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "700k" if webka else "1200k"],
                ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]):
        hw = ["-hwaccel", "auto"] if "nvenc" in enc[1] else []
        ok, err = _ffmpeg([*hw, "-i", src, "-map", "0:v:0", *([] if webka else ["-map", "0:a:0?"]), *common, *enc, *audio, tmp])
        if ok:
            os.replace(tmp, dst)
            return True, ""
    return False, err


def priprav(proj):
    st = PRIPRAVA.get(proj)
    if st and st.get("bezi"):
        return
    d = projekt(proj)
    todo = [f for f in d["_files"] if not os.path.exists(os.path.join(CACHE, f["id"] + ".mp4"))]
    mic = d["_mic"]
    mic_dst = os.path.join(CACHE, proxy_name(mic) + ".m4a") if os.path.exists(mic) else None
    st = PRIPRAVA[proj] = {"hotovo": 0, "celkem": len(todo) + (1 if mic_dst and not os.path.exists(mic_dst) else 0),
                           "bezi": True, "chyba": "", "aktualni": ""}

    def run():
        try:
            if mic_dst and not os.path.exists(mic_dst):
                st["aktualni"] = "mikrofon"
                ok, err = _ffmpeg(["-i", mic, "-c:a", "aac", "-b:a", "96k", "-ac", "1", mic_dst + ".part.m4a"])
                if ok:
                    os.replace(mic_dst + ".part.m4a", mic_dst)
                else:
                    st["chyba"] = "mikrofon: " + err
                st["hotovo"] += 1
            for f in todo:
                st["aktualni"] = f["name"]
                ok, err = _proxy(f["path"], os.path.join(CACHE, f["id"] + ".mp4"), f["webka"])
                if not ok:
                    st["chyba"] = f"{f['name']}: {err}"
                st["hotovo"] += 1
        finally:
            st["bezi"] = False
            st["aktualni"] = ""
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- HTTP
def adresy():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:  # noqa: BLE001
        pass
    lan = [ip for ip in ips if not ip.startswith("100.")]
    ts = [ip for ip in ips if ip.startswith("100.")]  # Tailscale má adresy 100.x.y.z
    return lan, ts


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _ok_token(self, q):
        if q.get("k", [""])[0] == TOKEN:
            return True
        return f"nk={TOKEN}" in (self.headers.get("Cookie") or "")

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        size = os.path.getsize(path)
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        a, b = 0, size - 1
        if rng:
            a = int(rng.group(1)) if rng.group(1) else max(0, size - int(rng.group(2)))
            b = int(rng.group(2)) if rng.group(1) and rng.group(2) else size - 1
            b = min(b, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(a)
            left = b - a + 1
            try:
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._ok_token(q):
            return self._send(403, "<h2>Náhled: chybí heslo – otevři odkaz z okna NÁHLED nebo ze záložky Výroba.</h2>", "text/html; charset=utf-8")
        cookie = {"Set-Cookie": f"nk={TOKEN}; Path=/; Max-Age=31536000; SameSite=Lax"}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, open(PLAYER, encoding="utf-8").read(), "text/html; charset=utf-8", cookie)
            if u.path == "/api/projekty":
                return self._send(200, json.dumps(projekty(), ensure_ascii=False))
            proj = q.get("p", [""])[0]
            if proj and proj not in projekty():
                return self._send(404, "{}")
            if u.path == "/api/projekt":
                d = projekt(proj)
                d["priprava"] = {k: v for k, v in PRIPRAVA.get(proj, {}).items()}
                return self._send(200, json.dumps({k: v for k, v in d.items() if not k.startswith("_")}, ensure_ascii=False))
            if u.path == "/api/priprav":
                priprav(proj)
                return self._send(200, '{"ok":true}')
            if u.path.startswith("/px/"):  # lehká kopie – jen soubory z timeliny projektu
                fid = u.path[4:].removesuffix(".mp4")
                d = projekt(proj)
                if not any(f["id"] == fid for f in d["_files"]):
                    return self._send(404, "{}")
                p = os.path.join(CACHE, fid + ".mp4")
                return self._file(p, "video/mp4") if os.path.exists(p) else self._send(404, "{}")
            if u.path == "/mic":
                d = projekt(proj)
                p = os.path.join(CACHE, proxy_name(d["_mic"]) + ".m4a") if d["mic"] else ""
                return self._file(p, "audio/mp4") if p and os.path.exists(p) else self._send(404, "{}")
            if u.path.startswith("/hudba/"):  # písnička z plánu hudby projektu
                i = int(u.path[7:])
                m = projekt(proj)["_music"]
                p = m[i].get("path", "") if 0 <= i < len(m) else ""
                return self._file(p, "audio/mpeg") if p and os.path.exists(p) else self._send(404, "{}")
        except Exception as e:  # noqa: BLE001
            return self._send(500, json.dumps({"chyba": str(e)}, ensure_ascii=False))
        return self._send(404, "{}")


def oznam(lan, ts):
    """Odkaz pro záložku 🛠️ Výroba na webu (přes Google Disk)."""
    try:
        if os.path.isdir(SLOZKA_DISK):
            d = {"t": int(time.time() * 1000), "port": PORT, "k": TOKEN,
                 "lan": [f"http://{ip}:{PORT}/?k={TOKEN}" for ip in lan], "tailscale": [f"http://{ip}:{PORT}/?k={TOKEN}" for ip in ts],
                 "pc": f"http://localhost:{PORT}/?k={TOKEN}"}
            tmp = os.path.join(SLOZKA_DISK, "~vyr_nahled.tmp")
            open(tmp, "w", encoding="utf-8").write(json.dumps(d))
            os.replace(tmp, os.path.join(SLOZKA_DISK, "vyroba_nahled.json"))
    except Exception:  # noqa: BLE001
        pass


def main():
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H)
    lan, ts = adresy()
    print("=" * 64)
    print("  ▶️  NÁHLED editu běží – nech tohle okno otevřené")
    print("=" * 64)
    print(f"  Na tomhle PC:      http://localhost:{PORT}/?k={TOKEN}")
    for ip in lan:
        print(f"  Mobil doma (Wi-Fi): http://{ip}:{PORT}/?k={TOKEN}")
    for ip in ts:
        print(f"  Mobil kdekoli (Tailscale): http://{ip}:{PORT}/?k={TOKEN}")
    if not ts:
        print("  (Mimo domov: nainstaluj Tailscale na PC i mobil – viz NÁVOD NÁHLED.txt)")
    print("  Odkaz je i v záložce 🛠️ Výroba na webu.")
    oznam(lan, ts)
    threading.Thread(target=lambda: [time.sleep(600) or oznam(*adresy()) for _ in iter(int, 1)], daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
