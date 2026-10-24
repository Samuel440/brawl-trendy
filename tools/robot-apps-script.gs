// 🤖 Brawl Trendy – robot pro grafy zhlédnutí (Google Apps Script)
// Běží zdarma na tvém Google účtu každých 30 minut, i když jsou všechna zařízení vypnutá.
// Stejná práce jako GitHub robot (tools/hist.py): vezme videa z Novinek/Týdne/Měsíce všech států ze složky
// „Brawl Trendy – data“, stáhne jejich aktuální zhlédnutí z YouTube a připíše body do s_bt_h_0…f__github-action.json.
// Stejná pravidla jako stránka: videa do 24 h každých 30 min, 1–7 dní co 2 h, 7–30 dní co 12 h.
//
// Nastavení (jednou): vlevo „Služby +“ → YouTube Data API v3 → Přidat, pak nahoře vybrat funkci „nastavit“ → Spustit.

const FOLDER = 'Brawl Trendy – data', ROBOT = 'github-action';
// paměť robota (mezipaměť Apps Scriptu, ne Disk): co se od minula nezměnilo, se znovu nestahuje.
// Google dává na běh robotů 90 min denně – bez paměti by 48 běhů × 2 min bylo moc těsně. Když se paměť ztratí, stáhne se prostě vše.
function memLoad_() {
  try {
    const c = CacheService.getScriptCache(), n = +c.get('mem_n') || 0; if (!n) return {};
    const keys = Array.from({length: n}, (_, i) => 'mem_' + i), parts = c.getAll(keys);
    return JSON.parse(keys.map(k => parts[k] || '').join(''));
  } catch (e) { return {}; }
}
function memSave_(obj) {
  const s = JSON.stringify(obj), CH = 90000, out = {};
  let n = 0; for (let i = 0; i < s.length; i += CH) out['mem_' + n++] = s.slice(i, i + CH);
  out.mem_n = String(n);
  try { CacheService.getScriptCache().putAll(out, 6 * 3600); } catch (e) { console.log('paměť se neuložila:', e.message); }
  return Math.round(s.length / 1024);
}
const HIST_DAYS = 45, HIST_MAX = 40, H = 3600e3;
const TIERS = [[24 * H, 30 * 60e3], [7 * 24 * H, 2 * H], [30 * 24 * H, 12 * H]]; // [stáří do, obnovit po]

// ⏱ Hlídání denního limitu Googlu (90 min běhu robotů denně zdarma): robot si sčítá, kolik dnes spotřeboval,
// a když se blíží limitu, zpomalí starší videa – ať se nezastaví úplně. Videa do 24 h a 📌 kanály jedou vždy naplno.
//   0 = normál · 1 = úspora (1–7 dní a ostatní kanály co 3 h) · 2 = nouze (jen videa do 24 h a 📌 kanály)
const BUDGET_MIN = 90;
function usage_() {
  const p = PropertiesService.getScriptProperties(), k = 'rt_' + Utilities.formatDate(new Date(), 'Europe/Prague', 'yyyy-MM-dd');
  return {p, k, used: +(p.getProperty(k) || 0)};
}
function addUsage_(ms) {
  const u = usage_(); u.p.setProperty(u.k, String(u.used + ms));
  for (const key of u.p.getKeys()) if (/^rt_/.test(key) && key !== u.k) u.p.deleteProperty(key); // včerejšek pryč
}
function saving_() {
  const min = usage_().used / 60e3, d = new Date();
  const hrs = +Utilities.formatDate(d, 'Europe/Prague', 'H') + +Utilities.formatDate(d, 'Europe/Prague', 'm') / 60;
  const projected = min / Math.max(hrs, 2) * 24; // kolik by to dalo za celý den, když to půjde takhle dál
  if (min > BUDGET_MIN - 10) return 2;
  if (min > BUDGET_MIN - 30 || projected > BUDGET_MIN - 10) return 1;
  return 0;
}
const SAVE_NAME = ['normál', '🟡 úspora – starší videa a ostatní kanály co 3 h', '🔴 nouze – jen videa do 24 h a 📌 kanály'];
const MAX_CALLS = 400; // pojistka: nejvýš tolik dotazů na YouTube za jeden běh
const MAX_MS = 4.5 * 60e3; // Google dovolí běh max 6 min – po 4,5 min se zbytek nechá na příště

// ▶ Spusť jednou ručně: nastaví spouštění každých 30 minut a hned jednou proběhne.
function nastavit() {
  for (const t of ScriptApp.getProjectTriggers()) ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger('sber').timeBased().everyMinutes(30).create();
  ScriptApp.newTrigger('zvonecek').timeBased().everyMinutes(10).create();
  console.log('✅ Robot nastavený – grafy každých 30 minut, 🔔 zvoneček 📌 kanálů každých 10 minut. Teď proběhne poprvé:');
  zvonecek_();
  sber();
}

// ✂️ Robot rozdělený na 2 Google účty (každý má od Googlu 90 min běhu denně):
//   nejedlysam440 → „nastavitGrafy“ (sběr grafů co 30 min) · ultrasamispoluprace → „nastavitHledani“ (zvoneček + hledání co 10 min)
function nastavitGrafy() {
  for (const t of ScriptApp.getProjectTriggers()) ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger('sber').timeBased().everyMinutes(30).create();
  console.log('✅ Tenhle robot teď sbírá jen GRAFY (každých 30 min). Hledání a zvoneček dělá robot na druhém účtu.');
  sber();
}
function nastavitHledani() {
  for (const t of ScriptApp.getProjectTriggers()) ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger('zvonecek').timeBased().everyMinutes(10).create();
  console.log('✅ Tenhle robot teď dělá jen 🔔 ZVONEČEK a 🕐 HLEDÁNÍ nových videí (každých 10 min). Grafy sbírá robot na druhém účtu.');
  zvonecek_();
}

// složka s daty (id si pamatujeme, hledání podle jména stojí čas)
function folder_() {
  const c = CacheService.getScriptCache(), id = c.get('folder');
  if (id) try { return DriveApp.getFolderById(id); } catch (e) {}
  const it = DriveApp.getFoldersByName(FOLDER);
  while (it.hasNext()) { const f = it.next(); if (f.searchFiles(`title contains '__${ROBOT}' and trashed = false`).hasNext()) { c.put('folder', f.getId(), 6 * 3600); return f; } }
  throw new Error(`Nenašel jsem složku „${FOLDER}“ se soubory robota. Jednou otevři web s připojeným Diskem (majitelským účtem).`);
}
function robotFile_(folder, k) {
  const it = folder.getFilesByName(`s_${k}__${ROBOT}.json`);
  return it.hasNext() ? it.next() : null;
}

// 🔔 Zvoneček: každých 10 minut RSS ručně přidaných (📌) kanálů – zdarma, nebere kvótu YouTube.
// Nové video hned zapíše do s_bt_new__github-action.json; stránka si ho doplní do Novinek a sběr grafů mu hned počítá body.
function zvonecek() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return; // zrovna běží sběr grafů – zvoneček počká na příště (za 10 min)
  const t0 = Date.now();
  try { zvonecek_(); } finally { addUsage_(Date.now() - t0); lock.releaseLock(); }
}
function zvonecek_() {
  const t0 = Date.now(), folder = folder_(), cache = CacheService.getScriptCache();
  // kanály ze sledovaných (bt_ch_<stát> ze všech účtů) – seznam se načítá jen jednou za hodinu.
  // 📌 ručně přidané se kontrolují každých 10 min, ostatní sledované (stejně jako na stránce: nejvíc Brawl videí, max 150 na stát) co 2 h.
  let lists = JSON.parse(cache.get('zv_chans2') || 'null');
  if (!lists) {
    const manual = {}, auto = {}, per = {};
    const files = folder.searchFiles("title contains 's_bt_ch_' and trashed = false");
    while (files.hasNext()) {
      const doc = readJson_(files.next()); if (!doc || !doc.k || !doc.v) continue;
      const cid = doc.k.replace('bt_ch_', '');
      for (const id in doc.v) {
        const e = doc.v[id]; if (!e) continue;
        if (e.manual) manual[id] = cid;
        else if ((e.ids || []).length) { const p = per[cid] = per[cid] || {}; p[id] = Math.max(p[id] || 0, e.ids.length); }
      }
    }
    for (const cid in per) Object.keys(per[cid]).sort((a, b) => per[cid][b] - per[cid][a]).slice(0, 150).forEach(id => { if (!manual[id]) auto[id] = cid; });
    lists = {manual, auto};
    cache.put('zv_chans2', JSON.stringify(lists), 3600);
  }
  const chans = Object.assign({}, lists.manual), lvl = saving_();
  const allDue = !cache.get('zv_all') && lvl < 2; // ostatní sledované jednou za 2 h (v úspoře za 3 h, v nouzi vůbec)
  if (allDue) Object.assign(chans, lists.auto);
  const ids = Object.keys(chans); if (!ids.length) { console.log('🔔 žádné sledované kanály'); return; }
  const f = robotFile_(folder, 'bt_new');
  if (!f) { console.log('🔔 chybí soubor s_bt_new__github-action.json – jednou otevři web s připojeným Diskem (majitelským účtem).'); return; }
  const doc = readJson_(f) || {k: 'bt_new', v: {}}, nw = doc.v || {}, now = Date.now();
  let added = 0;
  // RSS po 100 kanálech najednou (zdarma, nebere kvótu YouTube); když by běh trval moc dlouho, zbytek příště
  const res = [];
  for (let i = 0; i < ids.length && Date.now() - t0 < MAX_MS - 60e3; i += 100)
    res.push(...UrlFetchApp.fetchAll(ids.slice(i, i + 100).map(id => ({url: 'https://www.youtube.com/feeds/videos.xml?channel_id=' + id, muteHttpExceptions: true}))));
  const atom = XmlService.getNamespace('http://www.w3.org/2005/Atom'), yt = XmlService.getNamespace('yt', 'http://www.youtube.com/xml/schemas/2015');
  res.forEach((r, i) => {
    if (r.getResponseCode() !== 200) return;
    let entries;
    try { entries = XmlService.parse(r.getContentText()).getRootElement().getChildren('entry', atom); } catch (e) { return; }
    for (const e of entries) {
      const vid = e.getChildText('videoId', yt), pub = e.getChildText('published', atom);
      if (!vid || nw[vid] || !(now - Date.parse(pub) < 24 * H)) continue; // jen nová za posledních 24 h
      nw[vid] = {cid: chans[ids[i]], pub, ch: ids[i], at: now}; added++;
    }
  });
  // 🕐 + hledání nejnovějších videí (i od youtuberů, které ještě nesledujeme) – dřív jen při otevřené stránce
  // hledání je rychlé (~2 s) a nejcennější → jede i v nouzi, jen ne úplně na hraně limitu
  const ns = usage_().used / 60e3 < BUDGET_MIN - 2 ? novinky_(nw, now, t0) : 0; added += ns;
  for (const vid in nw) if (vid[0] !== '_' && now - Date.parse(nw[vid].pub) > 30 * 24 * H) delete nw[vid]; // starší než měsíc pryč
  if (added || (nw._robot && nw._robot.at === now)) f.setContent(JSON.stringify({k: 'bt_new', v: nw, t: now}));
  if (allDue) cache.put('zv_all', '1', (lvl ? 3 : 2) * 3600);
  console.log(`🔔 ${Object.keys(lists.manual).length} 📌 kanálů${allDue ? ` + ${Object.keys(lists.auto).length} sledovaných` : ''}, nových videí: ${added} (z hledání ${ns}), hledání dnes ${(nw._robot || {}).s || 0}/${NS_CAP}, trvalo ${Math.round((Date.now() - t0) / 1000)} s · dnes ${Math.round(usage_().used / 60e3)}/${BUDGET_MIN} min · ${SAVE_NAME[lvl]}`);
}

// 🕐 Nejnovější videa každého státu (hledání order=date) podle místního času – v hlavní čas vydávání častěji, v noci vůbec.
// Hledání je drahé (100 denně pro celý projekt – sdílí ho i stránka), proto max NS_CAP denně a nejvýš 2 státy za běh.
// Stránka už sama nejnovější nehledá (pozná podle _robot) → nic se nehledá dvakrát, ani když je otevřený mobil i PC.
const COUNTRIES = [
  {id:'it', lang:'it', region:'IT', tz:'Europe/Rome'}, {id:'de', lang:'de', region:'DE', tz:'Europe/Berlin'},
  {id:'ru', lang:'ru', region:'RU', tz:'Europe/Moscow', q:'brawl stars|бравл старс'}, {id:'ua', lang:'uk', region:'UA', tz:'Europe/Kyiv', q:'brawl stars|бравл старс'},
  {id:'pl', lang:'pl', region:'PL', tz:'Europe/Warsaw'}, {id:'fr', lang:'fr', region:'FR', tz:'Europe/Paris'},
  {id:'es', lang:'es', region:'ES', tz:'Europe/Madrid'}, {id:'br', lang:'pt', region:'BR', tz:'America/Sao_Paulo'},
  {id:'tr', lang:'tr', region:'TR', tz:'Europe/Istanbul'}, {id:'jp', lang:'ja', region:'JP', tz:'Asia/Tokyo', q:'brawl stars|ブロスタ'},
  {id:'kr', lang:'ko', region:'KR', tz:'Asia/Seoul', q:'brawl stars|브롤스타즈'}, {id:'en', lang:'en', region:'US', tz:'America/New_York'},
  {id:'cz', lang:'cs', region:'CZ', tz:'Europe/Prague'}];
const NS_CAP = 55; // hledání denně pro robota – zbytek ze 100 zůstane stránce na plná hledání
function nsEvery_(c, d) { // minut mezi hledáními, 0 = teď ne (noc)
  const h = +Utilities.formatDate(d, c.tz, 'H'), wd = Utilities.formatDate(d, c.tz, 'u'), weekend = wd === '6' || wd === '7';
  if (h < 7 || h >= 23) return 0;
  return (weekend ? h >= 8 && h < 17 : h >= 12 && h < 17) ? 120 : 240;
}
function novinky_(nw, now, t0) {
  const p = PropertiesService.getScriptProperties(), day = Utilities.formatDate(new Date(), 'America/Los_Angeles', 'yyyy-MM-dd'); // kvóta YouTube se nuluje o půlnoci v Kalifornii
  let used = p.getProperty('ns_d') === day ? +(p.getProperty('ns_n') || 0) : 0;
  const last = JSON.parse(p.getProperty('ns_last') || '{}'), d = new Date(now);
  const due = COUNTRIES.map(c => ({c, every: nsEvery_(c, d)})).filter(x => x.every && now - (last[x.c.id] || 0) >= x.every * 60e3 - 5 * 60e3)
    .sort((a, b) => (last[a.c.id] || 0) - (last[b.c.id] || 0)).slice(0, 2);
  let added = 0;
  for (const {c} of due) {
    if (used >= NS_CAP || Date.now() - t0 > MAX_MS - 30e3) break;
    let r;
    try { r = YouTube.Search.list('snippet', {type: 'video', order: 'date', maxResults: 50, q: c.q || 'brawl stars', relevanceLanguage: c.lang, regionCode: c.region,
      publishedAfter: new Date(now - 24 * H).toISOString().replace(/\.\d+Z$/, 'Z')}); }
    catch (e) { console.log('🕐 hledání', c.id, e.message); if (/quota/i.test(e.message)) used = NS_CAP; break; }
    used++; last[c.id] = now;
    for (const it of r.items || []) {
      const vid = it.id && it.id.videoId; if (!vid || nw[vid]) continue;
      nw[vid] = {cid: c.id, pub: it.snippet.publishedAt, ch: it.snippet.channelId, at: now, s: 1}; added++;
    }
    console.log(`🕐 nejnovější ${c.id}: +${added}`);
  }
  p.setProperty('ns_d', day); p.setProperty('ns_n', String(used)); p.setProperty('ns_last', JSON.stringify(last));
  // pro stránku: robot hledá sám (a kolik hledání dnes vzal – počítá se do stejné stovky)
  nw._robot = {at: now, d: day, s: used, last};
  return added;
}

// ⏹ Kdybys robota chtěl vypnout
function vypnout() {
  for (const t of ScriptApp.getProjectTriggers()) ScriptApp.deleteTrigger(t);
  console.log('⏹ Robot vypnutý.');
}

function sber() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) { console.log('Předchozí běh ještě neskončil – přeskakuji.'); return; }
  const t0 = Date.now();
  try { run_(); } finally { addUsage_(Date.now() - t0); lock.releaseLock(); }
}

function thin_(p) {
  const m = {};
  for (const x of p) m[x[0]] = x;
  p = Object.values(m).sort((a, b) => a[0] - b[0]);
  if (p.length <= HIST_MAX) return p;
  const step = (p.length - 1) / (HIST_MAX - 1), out = [p[0]];
  for (let i = 1; i < HIST_MAX - 1; i++) out.push(p[Math.round(i * step)]);
  out.push(p[p.length - 1]);
  return out;
}
function bucket_(vid) { return 'bt_h_' + (vid.charCodeAt(0) % 16).toString(16); }
function readJson_(f) {
  try { return JSON.parse(f.getBlob().getDataAsString()); } catch (e) { console.log('nejde přečíst', f.getName()); return null; }
}

function run_() {
  const t0 = Date.now();
  const folder = folder_(); // pravá složka = ta, ve které jsou soubory robota (připravila je stránka majitele)

  const cache = memLoad_();
  const seenU = cache.u || {}, newU = {}, oldSets = cache.sets || {}, newSets = {};
  const chk = cache.chk || {}, newChk = {}; // kdy se video naposled kontrolovalo, i když se zhlédnutí nezměnila (minuty)

  // projít soubory: seznamy videí (s_bt_c_<stát>_<období>__<kdo>.json) a body grafů (s_bt_h_<x>__<kdo>.json)
  const sets = {}, hist = {}, robot = {}, robotHot = {}, hotBuf = {}, changed = {};
  let read = 0;
  const files = folder.searchFiles("title contains 's_bt_' and trashed = false");
  while (files.hasNext()) {
    const f = files.next(), name = f.getName();
    if (!name.endsWith('.json')) continue;
    const m = name.match(/^s_(bt_c_.+_(?:day|week|month))__/);
    if (m) { // od každého seznamu stačí nejnovější kopie – stáhne se až níž (šetří čas)
      const when = f.getLastUpdated().getTime();
      if (!sets[m[1]] || when > sets[m[1]].when) sets[m[1]] = {when, f};
      continue;
    }
    if (!name.startsWith('s_bt_h_')) continue;
    // body ze stránek robot nečte – stránka si je slučuje sama. Robot má 2 druhy svých souborů:
    //   __github-action.json = všechna videa (velké, čte a přepisuje se jen při „spláchnutí“ co 6 h)
    //   __github-action-hot.json = jen nové body od posledního spláchnutí (malé, zapisuje se každý běh)
    const k = (name.match(/^s_(bt_h_[0-9a-f])__/) || [])[1]; if (!k) continue;
    if (name.endsWith(`__${ROBOT}.json`)) robot[k] = f;
    else if (name.endsWith(`__${ROBOT}-hot.json`)) robotHot[k] = f;
  }
  // spláchnout (číst + zapsat velké soubory): co 6 h, nebo když chybí paměť / horké soubory (ty založí stránka majitele)
  const hotOk = Object.keys(robotHot).length === 16;
  const flush = !hotOk || !cache.last || !cache.flushAt || Date.now() - cache.flushAt > 6 * H;
  for (const k in robot) {
    const docs = flush ? [robot[k], robotHot[k]] : [robotHot[k]];
    for (const f of docs.filter(Boolean)) {
      const doc = readJson_(f); read++; if (!doc || !doc.v) continue;
      const tgt = flush ? (hist[k] = hist[k] || {}) : (hotBuf[k] = hotBuf[k] || {});
      for (const vid in doc.v) tgt[vid] = thin_((tgt[vid] || []).concat(doc.v[vid]));
    }
  }
  // poslední měření každého videa (minuta, zhlédnutí) – z paměti, po spláchnutí z úplných dat
  const last = flush ? {} : Object.assign({}, cache.last);
  if (flush) for (const k in hist) for (const vid in hist[k]) { const p = hist[k][vid], l = p[p.length - 1]; if (l) last[vid] = [Math.round(l[0] / 60e3), l[1]]; }

  // která videa jsou na řadě (podle stáří videa a posledního bodu); nezměněné seznamy se berou z paměti
  const now = Date.now(), pub = {}, cidOf = {};
  for (const k in sets) {
    // nezměněný seznam z paměti; velký Měsíc (videa 7–30 dní se měří jen co 12 h) stačí přečíst jednou za 6 h
    const old = oldSets[k], monthOk = /_month$/.test(k) && old && now - (old.at || 0) < 6 * H;
    let ids = old && (old.when === sets[k].when || monthOk) ? old.ids : null; // {id: zveřejnění v minutách}
    const at = ids ? old.at || now : now;
    if (!ids) {
      ids = {}; read++;
      const doc = readJson_(sets[k].f);
      for (const v of (doc && doc.v && doc.v.items) || []) { const tp = Date.parse(v.pub); if (tp && now - tp < 30 * 24 * H) ids[v.id] = Math.round(tp / 60e3); }
    }
    newSets[k] = {when: ids === (old && old.ids) ? old.when : sets[k].when, ids, at};
    for (const id in ids) if (now - ids[id] * 60e3 < 30 * 24 * H) { pub[id] = ids[id] * 60e3; cidOf[id] = k.split('_')[2]; }
  }
  // + videa od 🔔 zvonečku (ještě nemusí být v seznamech – stránka je doplní, až se otevře)
  const nf = robotFile_(folder, 'bt_new'), nd = nf && readJson_(nf);
  for (const id in (nd && nd.v) || {}) { const tp = Date.parse(nd.v[id].pub); if (tp && now - tp < 30 * 24 * H && !pub[id]) { pub[id] = tp; cidOf[id] = nd.v[id].cid; } }
  for (const id in pub) if (chk[id]) newChk[id] = chk[id];
  // blíží se denní limit → starší videa řidčeji (úspora: 1–7 dní co 3 h; nouze: jen videa do 24 h)
  const lvl = saving_();
  // 🌙 noc v zemi videa (22–7 h místního času) – lidi spí, graf by se stejně skoro nehnul
  const night = {}; for (const c of COUNTRIES) { const h = +Utilities.formatDate(new Date(now), c.tz, 'H'); night[c.id] = h >= 22 || h < 7; }
  const tiers = lvl === 2 ? [[24 * H, 30 * 60e3], [7 * 24 * H, Infinity], [30 * 24 * H, Infinity]]
    : lvl === 1 ? [[24 * H, 30 * 60e3], [7 * 24 * H, 3 * H], [30 * 24 * H, 12 * H]] : TIERS;
  const due = Object.keys(pub).filter(vid => {
    const lastPt = (last[vid] || [0])[0] * 60e3, lastM = Math.max(lastPt, (chk[vid] || 0) * 60e3);
    let every = tiers.find(x => now - pub[vid] < x[0])[1];
    if (night[cidOf[vid]]) every = Math.max(every, 4 * H); // 🌙 v noci té země se skoro nesleduje → jen co 4 h
    return now - lastM >= every - 60e3;
  }).sort((a, b) => pub[b] - pub[a]); // nejnovější první (když dojde čas, ať se obnoví hlavně ty)
  console.log(`videí do 30 dní: ${Object.keys(pub).length}, k obnovení: ${due.length} · dnes ${Math.round(usage_().used / 60e3)}/${BUDGET_MIN} min · ${SAVE_NAME[lvl]}`);

  // zhlédnutí z YouTube po 50 videích (1 dotaz = 1 jednotka z 10 000 denních)
  let calls = 0;
  for (let i = 0; i < due.length; i += 50) {
    if (calls >= MAX_CALLS || Date.now() - t0 > MAX_MS) { console.log('pojistka – zbytek příště'); break; }
    calls++;
    let r;
    try { r = YouTube.Videos.list('statistics', {id: due.slice(i, i + 50).join(','), maxResults: 50}); }
    catch (e) { console.log('YouTube chyba:', e.message); if (/quota|forbidden|403/i.test(e.message)) break; continue; }
    for (const x of r.items || []) {
      const views = +((x.statistics && x.statistics.viewCount) || 0), b = bucket_(x.id), l = last[x.id];
      if (l && l[1] === views && now - l[0] * 60e3 < 12 * H) { newChk[x.id] = Math.round(now / 60e3); continue; } // nic nového – jen si zapamatovat kontrolu
      const pt = [[now, views]];
      if (flush) { const h = hist[b] = hist[b] || {}; h[x.id] = thin_((h[x.id] || []).concat(pt)); }
      else { const h = hotBuf[b] = hotBuf[b] || {}; h[x.id] = (h[x.id] || []).concat(pt); }
      last[x.id] = [Math.round(now / 60e3), views]; changed[b] = true;
    }
  }

  // zapsat (stránka si soubory sloučí sama): při spláchnutí velké soubory se vším + vyprázdnit horké, jinak jen horké
  let written = 0;
  if (flush) {
    for (const b of Object.keys(robot).sort()) {
      const out = {};
      for (const vid in hist[b] || {}) { const pts = hist[b][vid]; if (pts.length && now - pts[pts.length - 1][0] < HIST_DAYS * 24 * H) out[vid] = pts; }
      robot[b].setContent(JSON.stringify({k: b, v: out, t: now})); written++;
      if (robotHot[b]) { robotHot[b].setContent(JSON.stringify({k: b, v: {}, t: now})); written++; }
    }
    if (hotOk) cache.flushAt = now;
  } else {
    for (const b of Object.keys(changed).sort()) { robotHot[b].setContent(JSON.stringify({k: b, v: hotBuf[b] || {}, t: now})); written++; }
  }
  // paměť: jen videa do 30 dní
  for (const vid in last) if (!pub[vid]) delete last[vid];
  const kb = memSave_({u: newU, sets: newSets, chk: newChk, last, flushAt: flush ? (hotOk ? now : 0) : cache.flushAt});
  console.log(`${flush ? '💧 spláchnutí (velké soubory) · ' : ''}staženo souborů: ${read}, dotazů na YouTube: ${calls}, zapsaných souborů: ${written}, paměť ${kb} kB, trvalo ${Math.round((Date.now() - t0) / 1000)} s – hotovo`);
}
