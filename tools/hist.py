# 🤖 Brawl Trendy – sběr bodů do grafů zhlédnutí, i když jsou všechna zařízení vypnutá.
# Běží na GitHubu (Actions) každých 30 min. Čte sdílenou složku „Brawl Trendy – data“ na Google Disku
# (servisní účet, kterému je složka nasdílená jako Editor), vezme videa z Novinek/Týdne/Měsíce všech států,
# stáhne jejich aktuální zhlédnutí z YouTube a připíše body do souborů s_bt_h_0…f__github-action.json
# (ty předpřipraví stránka – servisní účet sám soubory zakládat nesmí).
# Stejná pravidla jako stránka: videa do 24 h každých 30 min, 1–7 dní co 2 h, 7–30 dní co 12 h.
import json, os, sys, time
from datetime import datetime
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

FOLDER = 'Brawl Trendy – data'
ROBOT = 'github-action'
DAPI = 'https://www.googleapis.com/drive/v3'
HIST_DAYS, HIST_MAX = 45, 40
H = 3600 * 1000
TIERS = [(24 * H, 30 * 60 * 1000), (7 * 24 * H, 2 * H), (30 * 24 * H, 12 * H)]  # (stáří do, obnovit po)
MAX_CALLS = 400  # pojistka: nejvýš tolik dotazů na YouTube za jeden běh

def log(*a): print(*a, flush=True)

def drive_session():
    info = json.loads(os.environ['GDRIVE_SA_JSON'])
    cred = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
    cred.refresh(Request())
    s = requests.Session(); s.headers['Authorization'] = 'Bearer ' + cred.token
    return s, info.get('client_email', '')

def dlist(s, q):
    out, page = [], None
    while True:
        p = {'q': q, 'pageSize': 1000, 'fields': 'nextPageToken,files(id,name,modifiedTime,owners(emailAddress))'}
        if page: p['pageToken'] = page
        r = s.get(DAPI + '/files', params=p); r.raise_for_status(); j = r.json()
        out += j.get('files', []); page = j.get('nextPageToken')
        if not page: return out

def thin(p):
    p = sorted({x[0]: x for x in p}.values())
    if len(p) <= HIST_MAX: return p
    step = (len(p) - 1) / (HIST_MAX - 1)
    return [p[0]] + [p[round(i * step)] for i in range(1, HIST_MAX - 1)] + [p[-1]]

def bucket(vid): return 'bt_h_' + format(ord(vid[0]) % 16, 'x')

def main():
    key = os.environ.get('YT_API_KEY', '').strip().strip('"\'')  # mezera / uvozovky navíc při vkládání
    if not key or not os.environ.get('GDRIVE_SA_JSON'):
        log('Chybí tajné hodnoty YT_API_KEY / GDRIVE_SA_JSON (Settings → Secrets → Actions) – zatím nic nedělám.'); return
    s, sa_email = drive_session()
    folders = dlist(s, f"name='{FOLDER}' and mimeType='application/vnd.google-apps.folder' and trashed=false")
    if not folders: sys.exit(f'Složka „{FOLDER}“ není nasdílená servisnímu účtu {sa_email} – nasdílej ji jako Editor.')
    # robot soubory říkají, která složka je ta pravá (připravil je majitel)
    folder = None
    for f in folders:
        if dlist(s, f"'{f['id']}' in parents and name contains '__{ROBOT}' and trashed=false"): folder = f['id']; break
    if not folder: sys.exit('Ve složce chybí soubory s_bt_h_*__github-action.json – otevři stránku a připoj Disk majitelským účtem (založí je sama).')
    files = [f for f in dlist(s, f"'{folder}' in parents and name contains 's_' and trashed=false") if f['name'].endswith('.json')]
    sets, hist, robot_ids = {}, {}, {}
    for f in files:
        name = f['name']
        if not (name.startswith('s_bt_c_') or name.startswith('s_bt_h_')): continue
        try: doc = s.get(f"{DAPI}/files/{f['id']}", params={'alt': 'media'}).json()
        except Exception as e: log('nejde přečíst', name, e); continue
        k, v, t = doc.get('k'), doc.get('v'), doc.get('t', 0)
        if name.endswith(f'__{ROBOT}.json'): robot_ids[k] = f['id']
        if not k or v is None: continue
        if k.startswith('bt_h_'):
            h = hist.setdefault(k, {})
            for vid, pts in v.items(): h[vid] = thin(h.get(vid, []) + pts)
        elif k.rsplit('_', 1)[-1] in ('day', 'week', 'month') and (k not in sets or t > sets[k][0]):
            sets[k] = (t, v)
    now = int(time.time() * 1000)
    pub = {}
    for _, v in sets.values():
        for it in v.get('items', []):
            try: tp = int(datetime.fromisoformat(it['pub'].replace('Z', '+00:00')).timestamp() * 1000)
            except Exception: continue
            if now - tp < 30 * 24 * H: pub[it['id']] = tp
    due = []
    for vid, tp in pub.items():
        pts = hist.get(bucket(vid), {}).get(vid, [])
        last = pts[-1][0] if pts else 0
        every = next(e for lim, e in TIERS if now - tp < lim)
        if now - last >= every - 60 * 1000: due.append(vid)
    due.sort(key=lambda v: -pub[v])  # nejnovější první (když narazíme na pojistku, ať se obnoví hlavně ty)
    log(f'videí do 30 dní: {len(pub)}, k obnovení: {len(due)}')
    changed, calls = set(), 0
    for i in range(0, len(due), 50):
        if calls >= MAX_CALLS: log('pojistka – zbytek příště'); break
        part = due[i:i + 50]
        r = requests.get('https://www.googleapis.com/youtube/v3/videos', params={'part': 'statistics', 'id': ','.join(part), 'key': key})
        calls += 1
        if r.status_code != 200:
            log('YouTube chyba', r.status_code, r.text[:300])
            if r.status_code in (403, 429) or 'API key' in r.text:
                if 'API key' in r.text: log(f'Špatný YT_API_KEY (délka {len(key)}, začíná {key[:4]!r}) – vlož znovu klíč „Brawl Trendy robot“ do Secrets.')
                break
            continue
        for x in r.json().get('items', []):
            views = int(x.get('statistics', {}).get('viewCount', 0))
            b = bucket(x['id']); h = hist.setdefault(b, {}); pts = h.get(x['id'], [])
            if pts and pts[-1][1] == views and now - pts[-1][0] < 12 * H: continue
            h[x['id']] = thin(pts + [[now, views]]); changed.add(b)
    log(f'dotazů na YouTube: {calls}, změněných souborů: {len(changed)}')
    for b in sorted(changed):
        fid = robot_ids.get(b)
        if not fid: log('chybí robot soubor', b); continue
        h = {vid: pts for vid, pts in hist[b].items() if pts and now - pts[-1][0] < HIST_DAYS * 24 * H}
        body = json.dumps({'k': b, 'v': h, 't': now}, separators=(',', ':'))
        r = s.patch(f'https://www.googleapis.com/upload/drive/v3/files/{fid}', params={'uploadType': 'media'}, data=body.encode(), headers={'Content-Type': 'application/json'})
        if r.status_code >= 300: log('zápis selhal', b, r.status_code, r.text[:300])
    log('hotovo')

if __name__ == '__main__':
    main()
