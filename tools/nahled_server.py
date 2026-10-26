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


_VFR = {}
_MAPA = {}
ORIG_CT = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".mkv": "video/webm", ".webm": "video/webm"}


def mapa(path, ifps):
    """Proměnlivá frekvence: [[čas podle snímku (n/ifps), skutečná časová značka]] co 60 snímků – aby originál na PC
    ukazoval stejný snímek jako Resolve (ten jede po snímcích). Čte jen značky paketů (pár vteřin), nic nedekóduje."""
    k = proxy_name(path)
    if k not in _MAPA:
        _MAPA[k] = None
        try:
            fp = os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe")
            out = subprocess.run([fp, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, creationflags=0x4000 if os.name == "nt" else 0).stdout
            pts = sorted(float(x) for x in out.split() if x and x != "N/A")
            if pts:
                _MAPA[k] = [[round(n / ifps, 4), round(pts[n], 4)] for n in range(0, len(pts), 60)] + [[round((len(pts) - 1) / ifps, 4), round(pts[-1], 4)]]
        except Exception:  # noqa: BLE001
            pass
    return _MAPA[k]


def vfr(path):
    """Má video proměnlivou snímkovou frekvenci (záznam z telefonu)? Resolve takové video bere jako pevných N fps
    podle POŘADÍ snímku (snímek 66151 = 66151/61 s), kdežto prohlížeč podle časových značek → jiný moment hry.
    (r_frame_rate 90000/1 vs. průměr 61,23 u Screenrecorderu.)"""
    k = proxy_name(path)
    if k not in _VFR:
        try:
            info = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
            m = re.search(r"Video:.*?([\d.]+) fps, ([\d.]+)(k?) tbr", info)
            fps, tbr = float(m.group(1)), float(m.group(2)) * (1000 if m.group(3) else 1)
            _VFR[k] = abs(fps - tbr) > 0.5  # „61.23 fps, 90k tbr“ = proměnlivá
        except Exception:  # noqa: BLE001
            _VFR[k] = False
    return _VFR[k]


_CACHE = {}


def projekt(proj):
    """Totéž co projekt_now, ale pamatuje si výsledek, dokud se soubory projektu nezmění (přehrávač se ptá často)."""
    folder = os.path.join(ANALYZA, proj)
    key = tuple(os.path.getmtime(os.path.join(folder, f)) if os.path.exists(os.path.join(folder, f)) else 0
                for f in ("base.drt", "plan_v4.json", "plan_bw.json", "plan_music.json", "chapters.json", "mic.wav")) + (
        max([os.path.getmtime(os.path.join(VYSTUP, f)) for f in os.listdir(VYSTUP) if f.endswith(".drt")] or [0]) if os.path.isdir(VYSTUP) else 0,)
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
    d["ovl"] = [ovl_info(p) for p in d.get("_ovl", [])]
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
                    # proměnlivá frekvence: kopie po snímcích jako v Resolve (jiné id → stará kopie podle časů se nepoužije)
                    ifps = _fps(c.findtext("MediaFrameRate")) if vfr(p) else None
                    files.append({"path": p, "name": os.path.basename(p), "id": proxy_name(p) + (f"i{ifps:g}" if ifps else ""),
                                  "webka": "webka" in p.lower(), "ifps": ifps, "orig": os.path.splitext(p)[1].lower() in ORIG_CT})
                kl.append([round(s / TL_FPS, 3), round((s + d) / TL_FPS, 3), fidx[p], round(i / _fps(c.findtext("MediaFrameRate")), 3)])
            stopy[f"{kind}{ti}"] = sorted(kl)
    for f in files:
        f["ready"] = os.path.exists(os.path.join(CACHE, f["id"] + ".mp4"))
    v4 = _rd(proj, "plan_v4.json", {}) or {}
    cam = sorted((int(k), s) for k, s in (v4.get("cam") or {}).items())
    music = _rd(proj, "plan_music.json", []) or []
    mic = os.path.join(ANALYZA, proj, "mic.wav")
    delka = max([k[1] for t in stopy.values() for k in t] or [0])
    vrstvy, zisk, ext = sestaveno(proj, {f["path"]: i for i, f in enumerate(files)})
    return {
        "vrstvy": vrstvy, "zisk": zisk, **{k: v for k, v in ext.items() if not k.startswith("_")}, "_zvuky": ext.get("_zvuky", []),
        "_ovl": ext.get("_ovl", []),
        "projekt": proj, "delka": delka, "soubory": [{k: f[k] for k in ("name", "id", "ready", "webka")} for f in files],
        "v1": stopy.get("V1", []), "v2": stopy.get("V2", []),
        "intro": ((_rd(proj, "intro.json", {}) or {}).get("intro_end") or 0) / TL_FPS,
        "cam": [[round(f / TL_FPS, 3), s] for f, s in cam],
        "punch": [round(int(f) / TL_FPS, 3) for f, s in (v4.get("game") or {}).items() if s == "punch"],
        "bw": [[round(x["s"] / TL_FPS, 3), round((x["s"] + x.get("d", 60)) / TL_FPS, 3)] for x in _rd(proj, "plan_bw.json", []) or []],
        "hudba": [[round(x["s"], 3), round(x["e"], 3), i, round(x.get("src", 0), 3), x.get("vol", -20),
                   os.path.splitext(os.path.basename(x.get("song", "")))[0]] for i, x in enumerate(music) if os.path.exists(x.get("path", ""))],
        "kapitoly": [[c["s"], c["e"], m] for c, m in zip(_rd(proj, "chapters.json", []) or [], _rd(proj, "moods.json", []) or [])],
        "cile": _cile(proj),
        "mic": os.path.exists(mic), "mic_ready": os.path.exists(os.path.join(CACHE, proxy_name(mic) + ".m4a")) if os.path.exists(mic) else False,
        "_files": files, "_music": music, "_mic": mic,
    }


def _cile(proj):
    """Kam systém (ai_targets.py) míří: šipka / červená čára / bílé označení / zoom na věc – [od, do, druh, text]
    pro řádky timeliny v náhledu (skok na místo a kontrola, jestli to míří na správnou věc)."""
    todo = {str(x.get("id")): x for x in _rd(proj, "targets_todo.json", []) or []}
    out = []
    for k, v in (_rd(proj, "targets.json", {}) or {}).items():
        x = todo.get(k)
        if not isinstance(v, dict) or not x:
            continue
        druh = "sipka" if v.get("arrow") else "cara" if v.get("line") or v.get("line_width") else "bile" if v.get("mask") \
            else "zoom" if v.get("kind") in ("cil", "postupny") else ""
        if druh:
            s = x["s"] / TL_FPS
            out.append([round(s, 2), round(s + x.get("d", 30) / TL_FPS, 2), druh, (v.get("line_text") or x.get("vec") or x.get("text") or "")[:40]])
    return sorted(out)


VYSTUP = r"S:\EDIT\vystup"
OVL_EXT = {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def ovl_info(p):
    """Překryv: obrázek (jde rovnou) nebo video se zeleným pozadím → průhledná kopie .webm (VP9 s alfou)."""
    img = os.path.splitext(p)[1].lower() in IMG_EXT
    oid = proxy_name(p)
    return {"name": os.path.basename(p), "id": oid, "img": img, "ready": img or os.path.exists(os.path.join(CACHE, oid + ".webm"))}


def _key_color(p):
    """Barva pozadí (roh obrazu v 1. vteřině) – zelená plocha pro klíčování."""
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", "0.5", "-i", p, "-frames:v", "1", "-vf", "scale=4:2",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        b = r.stdout
        return "0x%02X%02X%02X" % (b[0], b[1], b[2]) if len(b) >= 3 else "0x00FF00"
    except Exception:  # noqa: BLE001
        return "0x00FF00"


def _ovl_proxy(src, dst):
    tmp = dst + ".part.webm"
    kc = _key_color(src)
    r, g, b = int(kc[2:4], 16), int(kc[4:6], 16), int(kc[6:8], 16)
    key = f"chromakey={kc}:0.2:0.08," if g > 150 and r < 140 and b < 140 else ""  # klíčovat jen opravdu zelené pozadí
    ok, err = _ffmpeg(["-i", src, "-an", "-vf", f"{key}scale=-2:540,format=yuva420p", "-r", "30",
                       "-c:v", "libvpx-vp9", "-b:v", "900k", "-deadline", "realtime", "-cpu-used", "8", "-auto-alt-ref", "0", tmp])
    if ok:
        os.replace(tmp, dst)
    return ok, err


def _fx(c, L, uchyty=None):
    """Transformace klipu ze sestaveného DRT: zoom (42), posun X/Y (40/41) – hodnota nebo keyframy [(snímek, hodnota)],
    ořez (skupina 6: 54 vlevo, 55 vpravo, 56 nahoře, 57 dole), hlasitost (skupina 124, param 95 v dB).
    uchyty (dict): sem se doplní Bezierovy úchyty keyframů {(skupina, param): {snímek: (lΔsn, lΔhod, pΔsn, pΔhod)}} –
    keyframe s příznakem 1 (flags 13) je má v datech hned za hodnotou; bez nich Resolve jede lineárně (ověřeno renderem)."""
    h = c.findtext("EffectFiltersBA")
    out = {}
    if not h:
        return out
    try:
        top = L.pb_parse(L.blob_decode(h))
    except Exception:  # noqa: BLE001
        return out
    for t in top:
        if t[0] != 1 or t[1] != 2:
            continue
        g = L.pb_parse(t[2])
        gid = next((x[2] for x in g if x[0] == 1), None)
        for p in g:
            if p[0] != 9 or p[1] != 2 or not p[2]:
                continue
            sub = L.pb_parse(p[2])
            pid = next((q[2] for q in sub if q[0] == 1), None)
            kf = next((q for q in sub if q[0] == 10 and q[1] == 2), None)
            val = None
            if kf is not None:
                try:
                    # 2D hodnota (kotevní bod, příznak 0x20): x = první double, y = posledních 8 bajtů
                    kfs = L.kf_parse(bytes(kf[2]))
                    val = [(k["frame"], (L.kf_value(k), struct.unpack_from("<d", k["data"], len(k["data"]) - 8)[0])
                            if k["flags"] & 0x20 else L.kf_value(k)) for k in kfs]
                    if uchyty is not None:
                        hh = {k["frame"]: struct.unpack_from("<4d", k["data"], 8) for k in kfs
                              if k["flags"] & 1 and not k["flags"] & 0x20 and len(k["data"]) >= 40}
                        if hh:
                            uchyty[(gid, pid)] = hh
                except Exception:  # noqa: BLE001
                    val = None
            else:
                for q in sub:
                    if q[0] == 3 and q[1] == 2:
                        for r in L.pb_parse(q[2]):
                            if r[0] == 1 and r[1] == 2:
                                for x in L.pb_parse(r[2]):
                                    if x[0] == 2 and x[1] == 1:
                                        val = struct.unpack("<d", x[2])[0]
                                    elif x[0] == 7 and x[1] == 2 and len(x[2]) == 16:  # kotevní bod (x, y)
                                        val = struct.unpack(">dd", x[2])  # (tady big-endian: 0x3fd36c09… = 0,3035)
            if val is not None:
                out[(gid, pid)] = val
    return out


def _tf(c, L, t0, i0, mf):
    """Celá transformace klipu pro přehrávač: zoom X/Y (42/43), posun (40/41), rotace ve stupních (47, proti směru hodin),
    kotva (48) a ořez L/P/N/D (skupina 6) – hodnoty nebo keyframy převedené na časy timeliny.
    Keyframe = [čas, hodnota] (lineárně) nebo [čas, hodnota, lΔs, lΔhod, pΔs, pΔhod] (Bezier s úchyty z Resolve)."""
    uch = {}
    fx = _fx(c, L, uch)

    def tv(key, dflt, k=None):
        v = fx.get(key, dflt)
        pick = (lambda x: x[k] if isinstance(x, tuple) else (x if k == 0 else 0.0)) if k is not None else (lambda x: x)
        if not isinstance(v, list):
            return round(pick(v), 5)
        hh = uch.get(key, {})
        return [[round(t0 + (f - i0) / mf, 4), round(pick(x), 5)]
                + ([round(hh[f][0] / mf, 4), round(hh[f][1], 6), round(hh[f][2] / mf, 4), round(hh[f][3], 6)] if f in hh else [])
                for f, x in v]
    z = tv((4, 42), 1.0)
    out = {"z": z, "x": tv((4, 40), 0.0), "y": tv((4, 41), 0.0), "ax": tv((4, 48), (0.0, 0.0), 0), "ay": tv((4, 48), (0.0, 0.0), 1),
           "c": [tv((6, k), 0.0) for k in (54, 55, 56, 57)]}
    zy = tv((4, 43), fx.get((4, 42), 1.0))
    if zy != z:
        out["zy"] = zy
    r = tv((4, 47), 0.0)
    if r:
        out["r"] = r
    return out


def _barva(c):
    """Barva generátoru Solid Color: ve skupině 10 je QColor (1 | alfa | R | G | B po 16 bitech) – např. 01 ffff ffff 0000 0404."""
    try:
        for t in L_.pb_parse(L_.blob_decode(c.findtext("EffectFiltersBA"))):
            m = re.search(rb"\x32\x0b\x01(..)(..)(..)(..)", bytes(t[2]), re.S)
            if m and next((x[2] for x in L_.pb_parse(t[2]) if x[0] == 1), None) == 10:
                a, r, g, b = (int.from_bytes(m.group(k), "big") >> 8 for k in (1, 2, 3, 4))
                return "#%02x%02x%02x" % (r, g, b), round(a / 255, 3)
    except Exception:  # noqa: BLE001
        pass
    return "#ffffff", 1.0


def _compoundy(p):
    """Obsah compound clipů ze .drt: DbId compound clipu (MediaRef na timeline) -> seznam generátorů uvnitř."""
    import zipfile
    out = {}
    try:
        z = zipfile.ZipFile(p)
        mp = z.read("MediaPool/Master/MpFolder.xml").decode("utf-8", "replace")
        seq_of = {m.group(1): m.group(2) for m in re.finditer(
            r'<Sm2MpCompoundClip DbId="([^"]+)">.*?<Sm2Sequence DbId="([^"]+)"', mp, re.S)}
        if not seq_of:
            return out
        by_seq = {}
        for n in z.namelist():
            if not n.startswith("SeqContainer/"):
                continue
            x = z.read(n).decode("utf-8", "replace")
            if len(x) > 2_000_000:  # hlavní timeline – tu ne
                continue
            try:
                root = ET.fromstring(re.sub(r"<(/?)([A-Za-z0-9_]+)::", r"<\1\2__", x))
            except Exception:  # noqa: BLE001
                continue
            for tr in root.iter("Sm2TiTrack"):
                if tr.findtext("Type") == "0":
                    by_seq.setdefault(tr.findtext("Sequence"), []).extend(g for g in tr.iter("Sm2TiGenerator"))
        for cid, sid in seq_of.items():
            if by_seq.get(sid):
                out[cid] = by_seq[sid]
    except Exception:  # noqa: BLE001
        pass
    return out


L_ = None


def sestaveno(proj, fidx):
    """Ze SKUTEČNĚ sestaveného editu (S:\\EDIT\\vystup\\AUTO EDIT N.drt, nejnovější k tomuto projektu) vezme vrstvy videa
    s tvými zdroji (hra, webka) i se zoomy, posuny a ořezy, a hlasitost hry a mikrofonu. Bez sestavení → ([], {})."""
    base = os.path.join(ANALYZA, proj, "base.drt")
    try:
        sys.path.insert(0, r"S:\EDIT\tools")
        import drt_lib as L
    except Exception:  # noqa: BLE001
        return [], {}
    global L_
    L_ = L
    cand = []
    for f in os.listdir(VYSTUP) if os.path.isdir(VYSTUP) else []:
        p = os.path.join(VYSTUP, f)
        if f.lower().endswith(".drt") and not f.startswith("_") and os.path.getmtime(p) >= os.path.getmtime(base):
            cand.append((os.path.getmtime(p), p))
    for _, p in sorted(cand, reverse=True):
        try:
            d = L.Drt(p)
        except Exception:  # noqa: BLE001
            continue
        vrstvy, hits = [], 0
        ext = {"upravy": [], "shaky": [], "cb": [], "glow": [], "prechody": [], "zvuky": [], "_zvuky": [], "overlaye": [], "_ovl": [], "napisy": [],
               "tvary": []}
        comp = _compoundy(p)
        for ti, tr in enumerate(d.tracks("video"), 1):
            kl = []
            for c in L.Drt.items(tr):
                mp = c.findtext("MediaFilePath")
                # 🟥 červené čáry / křížky / podtržení: Solid Color (i uvnitř compound clipu) – barva, zoom X/Y, rotace, ořez
                gens = [(c, None)] if c.tag == "Sm2TiGenerator" and c.findtext("PrettyType") == "Solid Color" else \
                    [(g, c) for g in comp.get(c.findtext("MediaRef") or "", [])] if not mp and c.findtext("MediaRef") in comp else []
                if gens:
                    try:
                        s, du = float(c.findtext("Start")), float(c.findtext("Duration"))
                        i_out = float(c.findtext("In") or 0)
                    except Exception:  # noqa: BLE001
                        continue
                    t0 = s / TL_FPS
                    for g, outer in gens:
                        col, alpha = _barva(g)
                        # vnitřní generátor: jeho snímky běží od začátku compound clipu (minus In vnějšího klipu)
                        gs = float(g.findtext("Start") or 0) if outer is not None else 0.0
                        inner = _tf(g, L, t0 + (gs - i_out) / TL_FPS, 0.0, TL_FPS)
                        tvar = {"col": col, "a": alpha, "fx": inner}
                        if outer is not None:
                            tvar["out"] = _tf(outer, L, t0, i_out, TL_FPS)
                            tvar["stin"] = b"dropshadow" in (L.blob_decode(outer.findtext("EffectFiltersBA") or "") if outer.findtext("EffectFiltersBA") else b"")
                        ext["tvary"].append([round(t0, 3), round((s + du) / TL_FPS, 3), ti, tvar])
                    continue
                if mp and mp not in fidx and os.path.exists(mp) and os.path.splitext(mp)[1].lower() in OVL_EXT:
                    # 🖼️ překryvy: šipky, „lajk odběr“ (zelené pozadí → průhledná kopie), obrázky
                    try:
                        s, du, i = float(c.findtext("Start")), float(c.findtext("Duration")), float(c.findtext("In") or 0)
                    except Exception:  # noqa: BLE001
                        continue
                    mf, t0 = _fps(c.findtext("MediaFrameRate")), s / TL_FPS
                    if mp not in ext["_ovl"]:
                        ext["_ovl"].append(mp)
                    # šipky: i rotace (např. −233°) a oddělený zoom X/Y – jinak by šipka mířila úplně jinam
                    ext["overlaye"].append([round(t0, 3), round((s + du) / TL_FPS, 3), ext["_ovl"].index(mp), round(i / mf, 3),
                                            _tf(c, L, t0, i, mf), ti])
                    continue
                if mp not in fidx:
                    nm0 = (c.findtext("Name") or "").strip()
                    # ✍️ nápisy / samolepky (Fusion kompozice v adjustment clipu: NO, MANY, RIZZ…) – jen text, napodobený
                    if c.findtext("PrettyType") == "Adjustment Clip" and nm0 and not re.match(
                            r"(?i)^(zoom|click|rage|čb|cb|glow|úvod|uvod|další|solid|compound)", nm0) and len(nm0) <= 24:
                        try:
                            s, du = float(c.findtext("Start")), float(c.findtext("Duration"))
                            ext["napisy"].append([round(s / TL_FPS, 3), round((s + du) / TL_FPS, 3), nm0, ti])
                        except Exception:  # noqa: BLE001
                            pass
                    # 🎬 efekty, které náhled umí napodobit: přechody, ZOOM 2 (pyramidy), shaky, černobílé, GLOW
                    try:
                        s, du = float(c.findtext("Start")), float(c.findtext("Duration"))
                    except Exception:  # noqa: BLE001
                        continue
                    nm, a, b = (c.findtext("Name") or ""), round(s / TL_FPS, 3), round((s + du) / TL_FPS, 3)
                    if c.tag == "Sm2TiTransition":
                        # skutečný typ je v FieldsBlob („…Transitions/Edit/Parallax Pan/Out 1 - Left“) – jméno může být
                        # přejmenované (u tebe „DALŠÍ“ = Parallax Pan Out Left)
                        typ = nm
                        try:
                            mm = re.search(rb"Transitions/Edit/([ -~]{3,80})", bytes.fromhex((c.findtext("FieldsBlob") or "").strip()))
                            if mm:
                                typ = re.sub(r"\s+", " ", mm.group(1).decode("ascii", "replace")).strip()
                        except Exception:  # noqa: BLE001
                            pass
                        if not any(x[0] == a and x[1] == b for x in ext["prechody"]):  # stejný přechod na V1 i V2
                            ext["prechody"].append([a, b, nm[:40], typ[:60]])
                        continue
                    up = nm.upper()
                    if up.startswith("CLICK"):
                        ext["shaky"].append([a, b, 1])
                    elif up.startswith("RAGE"):
                        ext["shaky"].append([a, b, 2.2])
                    elif up.startswith("ČB") or up.startswith("CB"):
                        ext["cb"].append([a, b])
                    elif up.startswith("GLOW"):
                        ext["glow"].append([a, b])
                    if c.findtext("PrettyType") == "Adjustment Clip" or up.startswith("ZOOM") or up.startswith("CLICK"):
                        i0, mf, t0 = float(c.findtext("In") or 0), _fps(c.findtext("MediaFrameRate")), s / TL_FPS
                        f = _tf(c, L, t0, i0, mf)
                        f.pop("c", None)  # ořez adjustment clipu náhled nedělá
                        if f["z"] != 1.0 or f["x"] or f["y"] or f.get("r") or "zy" in f:
                            ext["upravy"].append([a, b, ti, f])
                    continue
                try:
                    s, du, i = float(c.findtext("Start")), float(c.findtext("Duration")), float(c.findtext("In") or 0)
                except Exception:  # noqa: BLE001
                    continue
                mf, t0 = _fps(c.findtext("MediaFrameRate")), s / TL_FPS
                # keyframy (snímky zdroje) -> časy timeliny; kotva = zlomek šířky / výšky od středu, ořez L/P/N/D
                kl.append([round(t0, 3), round((s + du) / TL_FPS, 3), fidx[mp], round(i / mf, 3), _tf(c, L, t0, i, mf)])
            if kl:
                hits += len(kl)
                vrstvy.append({"stopa": ti, "klipy": sorted(kl)})
        if hits < 10:
            continue  # sestavení jiného projektu
        zisk = {}
        for ti, tr in enumerate(d.tracks("audio"), 1):
            vols = sorted(v for c in L.Drt.items(tr) for v in [_fx(c, L).get((124, 95))] if isinstance(v, float))
            if vols:
                zisk[f"A{ti}"] = round(vols[len(vols) // 2], 1)
            # 🔊 zvukové efekty a hudba přesně z editu (vše kromě tvého záznamu hry a webky – ty jdou zvlášť)
            for c in L.Drt.items(tr):
                mp = c.findtext("MediaFilePath")
                if not mp or mp in fidx or not os.path.exists(mp):
                    continue
                try:
                    s, du, i = float(c.findtext("Start")), float(c.findtext("Duration")), float(c.findtext("In") or 0)
                except Exception:  # noqa: BLE001
                    continue
                mf, t0 = _fps(c.findtext("MediaFrameRate")), s / TL_FPS
                vol = _fx(c, L).get((124, 95), 0.0)
                if isinstance(vol, list):  # zesílení / zeslabení (keyframy) -> časy timeliny
                    vol = [[round(t0 + (f - i) / mf, 3), round(x, 2)] for f, x in vol]
                if mp not in ext["_zvuky"]:
                    ext["_zvuky"].append(mp)
                ext["zvuky"].append([round(t0, 3), round((s + du) / TL_FPS, 3), ext["_zvuky"].index(mp), round(i / mf, 3),
                                     vol if isinstance(vol, list) else round(vol, 2), ti, os.path.splitext(os.path.basename(mp))[0][:40]])
        ext["zvuky"].sort()
        zisk["soubor"] = os.path.basename(p)
        return vrstvy, zisk, ext
    return [], {}, {}


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
    # nižší priorita: převod nesmí brát výkon hře / OBS (BELOW_NORMAL_PRIORITY_CLASS)
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, errors="replace",
                       creationflags=0x4000 if os.name == "nt" else 0)
    return r.returncode == 0, r.stderr[-500:]


def obs_nahrava():
    """OBS právě nahrává / streamuje (běží jeho muxer) → těžké převody počkají, ať nevypadávají snímky v nahrávce."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs-ffmpeg-mux.exe", "/NH"], capture_output=True, text=True,
                             errors="replace", creationflags=0x08000000).stdout
        return "obs-ffmpeg-mux" in out.lower()
    except Exception:  # noqa: BLE001
        return False


def _pockej_na_obs(st):
    while obs_nahrava():
        st["aktualni"] = "⏸ čekám – OBS nahrává (převod by ti bral výkon)"
        time.sleep(20)


def _proxy(src, dst, webka, ifps=None):
    tmp = dst + ".part.mp4"
    # ifps: proměnlivá frekvence → obraz po snímcích (snímek N v čase N/ifps jako v Resolve), zvuk dál v reálném čase (taky jako Resolve)
    vf = (f"setpts=N/({ifps}*TB)," if ifps else "") + f"scale=-2:{360 if webka else 540}"
    common = ["-vf", vf, "-r", "30", "-g", "15", "-movflags", "+faststart"]
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
    ovl = [(p, o) for p, o in zip(d.get("_ovl", []), d.get("ovl", [])) if not o["ready"]]
    mic = d["_mic"]
    mic_dst = os.path.join(CACHE, proxy_name(mic) + ".m4a") if os.path.exists(mic) else None
    st = PRIPRAVA[proj] = {"hotovo": 0, "celkem": len(todo) + len(ovl) + (1 if mic_dst and not os.path.exists(mic_dst) else 0),
                           "bezi": True, "chyba": "", "aktualni": ""}

    def run():
        try:
            _pockej_na_obs(st)
            if mic_dst and not os.path.exists(mic_dst):
                st["aktualni"] = "mikrofon"
                ok, err = _ffmpeg(["-i", mic, "-c:a", "aac", "-b:a", "96k", "-ac", "1", mic_dst + ".part.m4a"])
                if ok:
                    os.replace(mic_dst + ".part.m4a", mic_dst)
                else:
                    st["chyba"] = "mikrofon: " + err
                st["hotovo"] += 1
            for f in todo:
                _pockej_na_obs(st)
                st["aktualni"] = f["name"]
                ok, err = _proxy(f["path"], os.path.join(CACHE, f["id"] + ".mp4"), f["webka"], f.get("ifps"))
                if not ok:
                    st["chyba"] = f"{f['name']}: {err}"
                st["hotovo"] += 1
            for p, o in ovl:  # šipky, lajk odběr: zelené pozadí pryč (průhledné .webm)
                _pockej_na_obs(st)
                st["aktualni"] = o["name"]
                ok, err = _ovl_proxy(p, os.path.join(CACHE, o["id"] + ".webm"))
                if not ok:
                    st["chyba"] = f"{o['name']}: {err}"
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

    def _lokal(self):
        """Dotaz z tohohle počítače – i když jde přes jeho adresu v síti (odkaz „Doma (Wi-Fi)“ otevřený na PC = 192.168.x.x)."""
        ip = self.client_address[0].removeprefix("::ffff:")
        if ip in ("127.0.0.1", "::1"):
            return True
        lan, ts = adresy()
        return ip in lan or ip in ts

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
                r = {k: v for k, v in d.items() if not k.startswith("_")}
                # 💻 na tomhle PC jde hrát rovnou z originálů (kopie jsou jen kvůli Wi-Fi / mobilu)
                lokal = self._lokal()
                r["lokal"] = lokal
                if lokal:
                    r["soubory"] = [dict(s, orig=f["orig"], mapa=mapa(f["path"], f["ifps"]) if f.get("ifps") and not f["ready"] else None)
                                    for s, f in zip(r["soubory"], d["_files"])]
                    r["mic_ready"] = r["mic_ready"] or bool(d["mic"])
                return self._send(200, json.dumps(r, ensure_ascii=False))
            if u.path.startswith("/orig/"):  # originál – jen pro tenhle počítač
                f = next((f for f in projekt(proj)["_files"] if f["id"] == u.path[6:]), None)
                if not self._lokal() or not f or not f["orig"]:
                    return self._send(404, "{}")
                return self._file(f["path"], ORIG_CT[os.path.splitext(f["path"])[1].lower()])
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
                if p and os.path.exists(p):
                    return self._file(p, "audio/mp4")
                if d["mic"] and self._lokal():  # na PC i bez kopie rovnou mic.wav
                    return self._file(d["_mic"], "audio/wav")
                return self._send(404, "{}")
            if u.path.startswith("/ov/"):  # překryv: průhledná kopie videa, nebo obrázek – jen soubory z editu
                i = int(u.path[4:])
                d = projekt(proj)
                if not 0 <= i < len(d["_ovl"]):
                    return self._send(404, "{}")
                p, o = d["_ovl"][i], d["ovl"][i]
                if o["img"]:
                    ct = {".png": "image/png", ".webp": "image/webp"}.get(os.path.splitext(p)[1].lower(), "image/jpeg")
                    return self._file(p, ct)
                q = os.path.join(CACHE, o["id"] + ".webm")
                return self._file(q, "video/webm") if os.path.exists(q) else self._send(404, "{}")
            if u.path.startswith("/zvuk/"):  # zvukový efekt / hudba ze sestaveného editu (jen soubory, které edit používá)
                i = int(u.path[6:])
                z = projekt(proj).get("_zvuky", [])
                p = z[i] if 0 <= i < len(z) else ""
                ct = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp4": "video/mp4", ".aac": "audio/aac",
                      ".ogg": "audio/ogg", ".mov": "video/quicktime"}.get(os.path.splitext(p)[1].lower(), "application/octet-stream")
                return self._file(p, ct) if p and os.path.exists(p) else self._send(404, "{}")
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


class Srv(http.server.ThreadingHTTPServer):
    # na Windows by allow_reuse_address dovolilo DVA servery na stejném portu – dotazy by se náhodně dělily mezi ně
    # (příprava běží v jednom, stav se ptá druhého → přehrávač čeká donekonečna). Port jen pro jeden.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    try:
        srv = Srv(("0.0.0.0", PORT), H)
    except OSError:
        print("▶️  Náhled už běží (jiné okno / na pozadí) – stačí otevřít Výrobu na webu. Tohle okno můžeš zavřít.")
        time.sleep(8)
        return
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

    def statistiky():  # 📊 čas a peníze pro Výrobu – hned a pak co 30 min
        while True:
            try:
                import vyroba_statistiky
                vyroba_statistiky.spocitej()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1800)
    threading.Thread(target=statistiky, daemon=True).start()

    def sama_priprava():  # ⚙️ nový / rozdělaný projekt (Auto Edit právě běží): lehké kopie hned, ať jde náhled pustit bez čekání
        while True:
            try:
                for proj in projekty()[:1]:
                    if time.time() - os.path.getmtime(os.path.join(ANALYZA, proj, "base.drt")) > 12 * 3600:
                        continue
                    d = projekt(proj)
                    if not PRIPRAVA.get(proj, {}).get("bezi") and (not all(f["ready"] for f in d["_files"])
                                                                   or not all(o["ready"] for o in d.get("ovl", []))):
                        priprav(proj)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(30)
    threading.Thread(target=sama_priprava, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
