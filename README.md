# Darkweb Forum Tracker

Underground hacking/cracking/carding forumlarından **gerçek thread + ilk mesaj
içeriğini** RSS + tarayıcı ile, ve bunları relay eden gated Telegram
kanallarından bir köprü servisi üzerinden çeken, [DarkWebFeed](../README.md)
ana ingestion modülünden ayrılmış bağımsız bir modül. Kullanıcı talebi
aggregator özetleri değil, gerçek forum "sohbet" içeriğiydi — bu modül
sadece onu yapar, artı elde ettiği veriyi keyword/risk-skoru tabanlı bir
alarm kartına (OsintCTI/"Data Leak Monitor" tarzı) dönüştürür.

## Kurulum ve çalıştırma

```bash
pip install -r requirements.txt
playwright install chromium   # tarayıcı-tabanlı kaynak için gerekli, bir kere çalıştırılır
python -m forumtracker.cli --once                # tek seferlik çalıştır, yeni kayıtları stdout'a bas
python -m forumtracker.cli --loop --interval 900  # sürekli poll (15 dk)
```

`playwright install chromium` çalıştırılmamışsa `BrowserForumSource` sessizce
uyarı loglayıp o turu boş döner (pipeline'ın geri kalanını etkilemez) —
RSS kaynağı (`UndergroundForumSource`) bundan bağımsız her zaman çalışır.

Çıktı: `data/incidents.jsonl` (append-only, her yeni kayıt) ve
`data/alerts.jsonl` (append-only, sadece keyword/risk-score eşiğini geçen
kayıtlar — bkz. "Alarm katmanı" altında). Dedup durumu `data/seen.sqlite3`
içinde tutulur — DarkWebFeed'in kendi `data/` klasöründen bağımsız, sıfırdan
başlar.

Testler (ağ gerekmez, kayıtlı örnek verilerle):

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## CI/CD — `.github/workflows/`

Kullanıcı talebi: "sürdürülebilir kıl, CI/CD uygun olacak şekilde" — yerel
bir oturuma/makineye bağımlı kalmadan tracker'ın kendi kendine çalışması.
İki ayrı workflow, iki ayrı amaç:

**`ci.yml`** — her push/PR'da testler (`pytest`, Python 3.11 + 3.12
matrix'i). Ağ gerektirmiyor, tüm testler kayıtlı fixture/mock veriyle
çalışıyor (bu README'nin her bölümünde tekrar eden disiplin: "canlı
doğrulandı" ayrı, "testte mock'landı" ayrı).

**`pipeline.yml`** — asıl "sürdürülebilirlik" kısmı. 30 dakikada bir
(`workflow_dispatch` ile elle de tetiklenebilir) `python -m
forumtracker.cli --once` çalıştırıyor — bu artık `pipeline.run_full_cycle()`
çağırıyor (aşağıdaki "run_full_cycle refactor" bölümüne bakın), yani tek
bir workflow adımı ingest + alarm + kimlik-eşleştirme + sağlık-kontrolü +
istatistik-snapshot'ının **hepsini** yapıyor.

**Durum kalıcılığı gerçek bir problem — GitHub Actions runner'ları
ephemeral.** `data/` (dedup DB, health/stats/identity SQLite'ları,
büyüyen `incidents.jsonl`/`alerts.jsonl`) `.gitignore`'da — SQLite
dosyalarını her çalıştırmada commit'lemek git diff'lerini anlamsız
büyütür. Çözüm: `actions/cache` her çalıştırmadan sonra `data/`'yı yeni
bir cache key'e (`tracker-state-${{ github.run_id }}`) kaydediyor, bir
sonraki çalıştırma `restore-keys: tracker-state-` prefix eşleşmesiyle en
son cache'i geri yüklüyor — cache entry'leri immutable olduğu için "her
zaman yeni key ile kaydet, prefix ile geri yükle" GitHub Actions'ın kendi
önerdiği büyüyen-state deseni.

**Görünür/kalıcı olan tek şey `reports/latest_cycle.json`** —
`run_full_cycle()`'ın döndürdüğü özet (yeni incident sayısı, yeni kimlik
eşleşmeleri, sağlık raporu, stats snapshot'ları) her çalıştırmadan sonra
bota commit'leniyor (`[skip ci]` ile, kendi kendini tetiklemesin diye).
Bu, repo'ya girip hiçbir şey çalıştırmadan tracker'ın son durumunu görmeyi
sağlıyor.

**`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`** repo secret'ı olarak
tanımlanırsa `notify_alerts`/`notify_health_alert`/`notify_identity_match`
otomatik çalışır; tanımlanmazsa `notify_telegram.py`'nin zaten var olan
sessiz no-op sözleşmesi gereği hiçbir şey kırılmaz.

**`run_full_cycle()` refactor'ı:** Bu iş sırasında gerçek bir mimari boşluk
bulundu — `identity_correlation`/`health`/`forum_stats` sadece
`pipeline.loop()`'a bağlıydı, `cli.py`'nin tek seferlik (`--once`/varsayılan)
yoluna değil. Yani CI'nin çalıştıracağı tek seferlik bir invocation bu
üçünü **sessizce atlardı**. Düzeltme: hepsini tek bir
`pipeline.run_full_cycle()`'a topladım; `loop()` artık bunu döngüde
çağıran ince bir sarmalayıcı, `cli.py`'nin varsayılan yolu da aynı
fonksiyonu bir kez çağırıyor — iki giriş noktası artık birbirinden
sapamaz. `tests/test_pipeline.py`'a 3 yeni test (kimlik eşleştirmenin
gerçekten tetiklendiğini, ikinci çağrının dedup sayesinde tekrar
etmediğini, bir kaynağın çökmesinin tüm döngüyü düşürmediğini doğruluyor).

**`forum_stats.collect_snapshots()` de bu turda paralelleştirildi** —
önceden 23 forumu sırayla (`for` döngüsü) çekiyordu, worst-case ~23×12s.
`health.check_all()`'la aynı desene geçti: `ThreadPoolExecutor` ile
paralel fetch, ama `store.record()` çağrıları ana thread'de kalıyor
(SQLite bağlantısı `check_same_thread=True` ile açıldığı için worker
thread'den yazmak hataya düşerdi) — bu, CI job'ının 20 dakikalık zaman
sınırını (`timeout-minutes: 20`) güvenle karşılaması için gerekliydi, sadece
yerel kullanım kolaylığı değil.

**Bilinçli olarak yapılmayan:** `incidents.jsonl`/`alerts.jsonl`'i git
history'sine commit'lemek — sürekli büyüyen dosyaları her 30 dakikada bir
commit'lemek repo'yu şişirir. Cache'te kalıyorlar, git'e giren sadece
küçük, insan-okunur `reports/latest_cycle.json` rollup'ı.

## Master dashboard — `dashboard.py`

Kullanıcı talebi: "full darkweb forum tracker son halini full + full
görmek istiyorum... görsel olarak bütün herşeyi bir arayüze aktar... bir
arama butonu ekle". Bu ana kadarki her özelliğin (health, forum_stats,
identity_correlation, member_directory) kendi ayrı raporu vardı — bu
modül hepsini **tek bir statik HTML sayfasında** birleştirip üstüne
gerçek bir arama kutusu koyuyor.

**Nasıl çalışıyor:** `python -m forumtracker.cli --build-dashboard`,
`data/`'daki mevcut duruma (tüm SQLite store'lar + `incidents.jsonl` +
`alerts.jsonl`) bakıp `reports/dashboard.html`'i üretiyor — sunucu yok,
görüntülerken hiçbir ağ isteği yok, o anki state JSON olarak sayfaya
gömülüyor (`forumtracker/templates/dashboard.html` şablonu +
`__DATA_JSON__` yer tutucusu). Ağ gerektirmediği için testleri de
(`tests/test_dashboard.py`) sahte `data/` durumuyla çalışıyor.

**Sayfada ne var:** KPI şeridi (izlenen forum/kayıt/alarm/kimlik
eşleşmesi sayıları), 32 forumun sağlık durumu + gerçek
`forum_stats.py` geçmişinden çizilen mini aktivite grafiği (sparkline),
en yüksek riskli 25 alarm, çapraz-forum kimlik eşleşmeleri tablosu
(`identity_correlation`) ve çapraz-forum kayıtlı hesaplar tablosu
(`member_directory`) — üstte tek bir arama kutusu, yazdıkça bunların
**hepsinde birden** (kayıt başlığı, kullanıcı adı, forum adı, etiket)
aynı anda arıyor, sonuçları kategoriye göre gruplayıp gösteriyor.

**Bilinçli olarak `pipeline.yml`'e bağlanmadı:** bu sayfa tüm
incidents/alerts geçmişini gömüyor (büyüyor, şu an ~1.5 MB) —
`reports/latest_cycle.json`'ın aksine, bunu her 30 dakikada bir otomatik
commit'lemek repo'yu sürekli MB'larca diff ile şişirir. Talep üzerine
elle (`--build-dashboard`) veya istenirse ayrı, daha seyrek bir
workflow'la üretilmesi düşünülebilir — şimdilik bilinçli olarak elle.

**Yayınlanan canlı örnek** (12 Eylül 2026 verisiyle, statik snapshot):
https://claude.ai/code/artifact/36d6fc46-a557-4c38-84a3-72c008d34da1

## Mimari

```
forumtracker/
  schema.py             <- Incident/Source/ThreatActor/Victim/Evidence dataclass'ları
  dedup.py               <- SQLite: incident_id daha önce görüldü mü?
  alerting.py             <- keyword eşleştirme + risk skoru + alert kartı + Telegram render (LLM yok)
  discover.py             <- Telegram'dan referans alınan forum URL'lerini FORUM_POOL adayı olarak sıralar
  notify_telegram.py      <- alert kartlarını gerçek bir Telegram kanalına POST eder (Bot API)
  ransomware_intel.py     <- bilinen RaaS grup adı + fidye-notu kalıp eşleştirme
  entity_extraction.py    <- metinden gerçek mağdur alan adı (victim.website) çıkarımı
  sources/
    base.py               <- Source arayüzü (fetch() -> Iterable[Incident], enrich()/enrich_batch())
    underground_forums.py <- FORUM_POOL (22 site) + RSS keşfi/parse/normalize + thread-sayfası enrich() + ekran görüntüsü
    browser_forum.py      <- BROWSER_FORUM_POOL + Playwright ile navigasyon/parse/ekran görüntüsü
    telegram_bridge.py    <- TELEGRAM_BRIDGE_POOL + tg.i-c-a.su üzerinden gated kanal okuma
  pipeline.py             <- üç source'u da çalıştırır, yeni kayıtları + alarmları emit + Telegram'a gönderir
  cli.py                  <- --once / --loop komut satırı girişi
schemas/
  incident.schema.json    <- resmi JSON Schema kontratı (draft 2020-12)
  examples/
    underground_forums.example.json  <- RSS kaynağından 13 gerçek gönderi (dizi)
    browser_forums.example.json      <- Playwright kaynağından 1 gerçek gönderi
tests/
  fixtures/                    <- canlı sitelerden kaydedilmiş gerçek HTML/RSS (offline test için)
  test_underground_forums.py   <- normalize() birim testleri (kayıtlı RSS fragmanlarıyla)
  test_underground_forums_enrich.py <- thread-sayfası enrich() birim testleri (mock'lu HTTP)
  test_browser_forum.py        <- parse/classify birim testleri (kayıtlı HTML fixture'larıyla)
  test_telegram_bridge.py      <- köprü parse birim testleri (kayıtlı gerçek RSS fixture'ıyla)
  test_discover.py             <- forum adayı sıralama birim testleri
  test_alerting.py             <- keyword eşleştirme/risk skoru/kart/render birim testleri
  test_notify_telegram.py      <- Telegram gönderim birim testleri (requests.post mock'lu)
  test_pipeline.py             <- build_alerts() sıralama/filtre + ransomware_intel wiring birim testleri
  test_ransomware_intel.py     <- RaaS grup adı + fidye-notu kalıp birim testleri
  test_entity_extraction.py    <- mağdur alan adı çıkarımı birim testleri
  test_schema_examples.py      <- örnek dosyaları şemaya karşı doğrular
```

**Yeni bir RSS forumu eklemek için:** `sources/underground_forums.py`
içindeki `FORUM_POOL` sözlüğüne `{"homepage": "...", "rss": "..."}` (veya
sadece `"rss": None` bırakıp otomatik keşfe güvenerek) bir satır eklemek
yeterli. **Yeni bir tarayıcı-tabanlı (RSS'siz) forum eklemek için:**
`sources/browser_forum.py`'deki `BROWSER_FORUM_POOL`'a bir site + parser adı
eklemek, ve o forum yazılımı için henüz yoksa `_parse_*`/`_extract_*` çifti
yazmak gerekir — her iki durumda da pipeline'ın geri kalanı değişmez.

## Araştırma süreci ve bulgular

Kullanıcı talebi: aggregator özetleri değil, gerçek forum thread/mesaj
içeriği. İzlenen yol:

1. **Tor (.onion) erişimi bu ortamdan mümkün değil** — Tor'un ihtiyaç duyduğu
   ham TCP bağlantıları (relay'lere 9001 gibi portlardan) bu sandbox'ta
   engelleniyor; Tor client bootstrap edemiyor. Gerçek .onion forumları
   kapsam dışı kaldı (bkz. "Bilinen kısıt" altında çözüm önerisi).
2. **Clearnet'teki büyük forumların çoğu ölü/korumalı**: BreachForums (.cx)
   FBI tarafından ele geçirilmiş, Exploit.in bot/CAPTCHA (Altcha PoW)
   duvarının arkasında, XSS.is/Nulled.to/Cracked.io/BreachForums.st bu
   sandbox'tan hiç erişilemiyor.
3. **Kaynak havuzu**: [github.com/fastfire/deepdarkCTI](https://github.com/fastfire/deepdarkCTI)
   (`forum.md`) — topluluk tarafından tutulan, ONLINE/OFFLINE durumu
   işaretli ~350 forum listesi. Buradan "ONLINE + clearnet (.onion değil)"
   189 aday çıktı, canlı test edilince **76'sı gerçekten erişilebilir**
   çıktı (>2KB gerçek içerik dönenler).
4. **Yazılım tespiti**: bu 76 sitenin HTML imzalarına bakılarak
   XenForo (28), MyBB (12), Invision (5), vBulletin (5), Discourse (1),
   geri kalanı (33) bilinmeyen/özel yazılım olarak sınıflandırıldı.
5. **RSS otomatik keşfi**: forum ana sayfasındaki
   `<link rel="alternate" type="application/rss+xml">` etiketi okunarak
   (URL tahmin etmeden) XenForo/MyBB/Invision hedeflerinde RSS feed'i
   bulundu — **40 adaydan 29'unda çalışan bir RSS feed** tespit edildi.
   Hem XenForo'nun `content:encoded` hem MyBB/IPB'nin `description` alanı
   **thread'in gerçek ilk mesaj metnini** taşıyor — yani başlık değil,
   gerçek "sohbet" içeriği.
6. **Canlı test**: 12 siteden oluşan ilk havuzla 234 gerçek gönderi çekildi
   (`tests/test_underground_forums.py` ile offline doğrulandı). Örnek
   gerçek sinyal: TurkHackTeam'de "Visual Studio Code #DOWN" başlıklı,
   hedef/WAF/yöntem/süre bilgisi içeren bir DDoS iddiası; carding
   forumlarından SSN/DOB/DL "fullz" veri satışı ilanları.

İlk havuz 11 siteydi; RSS keşfi sırasında ayrıca 40 aday denenmiş, 34'ünde
çalışan bir RSS bulunmuştu ama sadece 11'i koda yazılmıştı — kalan ~22'si
oturum verilerinde (`forum_rss_discovery.json`) kullanılmadan bekliyordu.
Bu ikinci turda o listeyi tekrar canlı doğruladık: **5'i artık ölü**
(cracked.st, crdpro.at, rehubcom.ag, rootsploit.org, wwh-club.net — 404/403/
timeout), 17'si hâlâ canlıydı. O 17'yi eklemeden önce Altenens'teki gibi bir
içerik/başlık taraması yaptık:

**Havuzdan çıkarılan (6):** `hard-tm.com` Altenens ile aynı profil —
canlı içeriği kişisel/yetişkin içerik paylaşımına kaymış ("Слив
<isim>" tarzı başlıklar). `dublikat.club` ve `seopirat.club`, örneklenen
gönderilerinde aynı tekrarlayan "интимные архивы (18+)" reklamını
taşıyordu (muhtemelen forumlar arası yayılan bir reklam, ama örnekte
çıktığı için ihtiyatlı davranıp çıkardık). `bits.media` (ana akım kripto
forumu), `mipped` (kupon/promosyon kodu forumu) ve `openssource`
(kurs/warez korsanlığı forumu) ise CTI açısından konu dışı — hacking/
cracking/carding odaklı değiller.

**Eklenen (11):** `breachforums` (bf.st — BreachForums'un güncel aynası),
`darkforums` (darkforums.as), `blackbones`, `blackhatpakistan`,
`forumteam.top`, `niflheim`, `nodo313`, `piratebuhta`, `spear`, `toolbase`,
`warriors`. Hepsi net şekilde hacking/carding/breach-satışı odaklı ve canlı
doğrulandı (bkz. aşağıdaki tam liste).

## Şu anki havuz (22 canlı forum)

TurkHackTeam, Spyhackerz, Sinister.ly, BDF Club, Carders.biz, Leetforums,
MMGP, Probiv, Xreactor, LeakForum.io, BHCForums (ilk 11) + BreachForums
(bf.st), DarkForums (darkforums.as), BlackBones, BlackHatPakistan,
ForumTeam.top, Niflheim, Nodo313, PirateBuhta, Spear, ToolBase, Warriors
(ikinci tur) — RSS üzerinden **gerçek thread + ilk mesaj metni**. Test:
443 kayıt (tek çekimde, 22 site toplamı).

Gerçek sinyal örnekleri: BreachForums'ta **ShinyHunters** imzalı "750M -
China JD.com 2026" satış ilanı; DarkForums'ta **LunarisSec** imzalı,
Fransız devlet portalı snu.gouv.fr'den IDOR ile 250k kullanıcı sızıntısı
iddiası (bkz. `schemas/examples/underground_forums.example.json`).

## Thread sayfasından tam içerik — `UndergroundForumSource.enrich()`

**Sorun:** RSS'in kendi `content:encoded`/`description` alanı çoğu zaman
ilk mesajın *tamamı* değil, forumun kendi RSS üretecinin kestiği bir teaser
— önceki halimiz bunu 800 karaktere sabit kesip olduğu gibi kullanıyordu.
Kullanıcı talebi net: "forumlardaki sohbeti çekmek" gerçek ilk mesajın tam
metni demek, RSS özeti değil.

**Çözüm:** `pipeline.run_once()` her yeni (dedup'tan geçmiş) kayıt için
kaynağın `enrich(incident)` kancasını çağırıyor — `UndergroundForumSource`
bunu override edip thread URL'sine gerçek bir `requests.get` atıyor,
sayfayı `BeautifulSoup` ile parse edip ilk mesaj gövdesini çıkarıyor:

```python
POST_BODY_SELECTORS = ["article.message .bbWrapper", ".message-body", "div.post_body"]
```

İlk ikisi XenForo (havuzdaki 16 site), üçüncüsü MyBB (6 site) — 22 forumun
20'sinde canlı doğrulandı (2026-09-08). Sadece **dedup'tan geçen yeni
kayıtlar** enrich ediliyor (her döngüde 660 RSS item'ının tamamı değil) —
yani ek HTTP yükü, zaten yeni olan kayıt sayısıyla orantılı.

**"Uzunsa değiştir" kuralı, özel durum kodu yazmadan iki gerçek sorunu
kendiliğinden çözüyor:**
- **Reply-duvarlı ("[HIDE]") threadler** (xreactor, leakforum.io, warriors'ta
  canlı görüldü): misafir görünümünde sayfa sadece "Sign In or Register"
  gibi kısa bir metin döndürüyor — bu, RSS teaser'ından neredeyse her zaman
  daha kısa olduğu için asla üzerine yazmıyor, mevcut açıklama korunuyor.
- **Metin içermeyen ilk mesajlar** (probiv'de canlı görüldü, sadece resim
  içeren bir thread): seçici eşleşse bile metin boş dönüyor, yine üzerine
  yazılmıyor.

Gerçekten daha uzun/zengin bir gövde bulunduğunda (`len(yeni) > len(mevcut)`)
`description` değişiyor ve `extra.content_source = "thread_page"` işaretleniyor
— canlı doğrulama: sinister.ly'de 5 kayıttan 3'ü 794-800 karakterlik RSS
teaser'dan 1164-1999 karakterlik gerçek ilk mesaja yükseldi, 2'si zaten RSS
ile aynı/daha uzun olduğu için değişmedi.

**Doğrulama:** `tests/test_underground_forums_enrich.py` (10 test) — XenForo/
MyBB seçici eşleşmesi, çoklu-eşleşmede ilk post'un seçilmesi (cevap değil),
gated-placeholder'ın teaser'ın üzerine yazmaması, `post_url` yoksa/ağ hatası
olursa kaydın değişmeden dönmesi, ld+json reply-count okuma.

## Popülerlik skoru — `compute_popularity_score()`

Aynı thread-sayfası fetch'inin içinde, XenForo'nun `schema.org/
DiscussionForumPosting` JSON-LD bloğu varsa (`interactionStatistic.
userInteractionCount`) gerçek bir cevap sayısı da çıkarılıyor —
`extra.reply_count`. **Kapsam dürüstçe kısıtlı:** 21 canlı forumun 8'inde
(hepsi XenForo, hepsi paralı bir SEO eklentisiyle geliyor gibi görünüyor)
bu veri var; MyBB'nin (5 forum) hiçbirinde eşdeğer bir işaretleme yok,
`toolbase` da yok. Canlı sayılar: carders.biz=6853, piratebuhta=444,
blackbones=46, mmgp=135, bdfclub=13, xreactor=1, spyhackerz=0,
forumteam.top=0.

Kullanıcı talebi: "görüntülenme/cevap sayısı ve potansiyel risk skoru eş
orantılı olacak". `compute_popularity_score()` bunu yapıyor:

```python
def compute_popularity_score(incident, risk_score=None) -> int:
    # reply_count -> 1-10 log-skala (0'dan 6800+'e kadar aşırı çarpık bir
    # dağılım, doğrusal skala neredeyse her şeyi 1'e yığardı)
    # reply_count yoksa: risk_score tek başına döner (uydurma 0 ile
    # ortalama alıp veri sağlamayan forumları cezalandırmamak için)
    # reply_count varsa: round((risk_score + engagement_score) / 2)
```

Sonuç `to_alert_card()`'ın `rich.popularity_score` + `rich.reply_count`
alanlarına yazılıyor, `render_telegram_message()`'a "🔥 Popularity: X/10
(N replies)" satırı olarak ekleniyor (sadece `reply_count` varsa
gösteriliyor), ve **`pipeline.build_alerts()` alarm listesini
`popularity_score`'a göre azalan sırada sıralıyor** — "en popüler
sayfaları öne çıkarmak" talebinin somut karşılığı bu sıralama.

Canlı doğrulama (carders.biz + piratebuhta, 45 kayıt → 5 alarm): en üstte
pop=8 (risk=6, 6853 cevap), en altta pop=4 (risk=4, cevap verisi yok) —
gerçek veriyle beklenen sıralama.

**Doğrulama:** `test_alerting.py`'a 3 yeni test (reply_count yoksa risk'e
düşme, reply_count'un skoru yukarı çekmesi, 1-10 sınırlaması) +
`tests/test_pipeline.py` (yeni dosya, 2 test: sıralama + alarm-değeri
filtresi).

## Forum-seviyesi aktivite skoru — `forum_stats.py`

Yukarıdaki `compute_popularity_score()` bir **thread**'in ne kadar önemli
olduğunu puanlıyor, forumun kendisinin ne kadar aktif/büyük olduğunu değil
— bu, kullanıcının "forumların popülaritesini neye göre skalıyorsun"
sorusuyla ortaya çıkan gerçek bir boşluktu. Yeni dış kaynağa gerek yok:
`underground_forums._discover_rss_url` zaten her cycle'da forum ana
sayfasını RSS keşfi için çekiyor — o HTML'in içinde XenForo'nun standart
"Forum İstatistikleri" widget'ı (`data-widget-definition="forum_statistics"`
veya temaya göre `forumStatistics`/`live_forum_statistics`) gerçek,
kümülatif Thread/Mesaj/Üye sayılarını `<dt>etiket</dt><dd>sayı</dd>`
çiftleri olarak zaten taşıyor.

Canlı doğrulandı — 23 forumun 10'unda widget var (kalan 13'ü ya MyBB/farklı
tema ya da widget'ı ana sayfada göstermiyor, `no_stats_widget_found` bunu
şeffafça raporluyor):

```
turkhackteam      threads=1,463,872  messages=7,931,265  members=927,937
mmgp              threads=737,650    messages=17,493,825 members=406,071
probiv            threads=162,658    messages=1,203,467  members=148,616
niflheim          threads=170,173    messages=396,996    members=60,699
piratebuhta       threads=36,518     messages=306,923    members=179,007
forumteam.top     threads=71,003     messages=272,570    members=162,087
bdfclub           threads=153,298    messages=326,731    members=72,916
carders.biz       threads=14,077     messages=68,487      members=64,642
leetforums        threads=17,062     messages=34,471      members=106,637
blackhatpakistan  threads=869        messages=1,761       members=3,470
```

Etiketler dile/temaya göre değişiyor (Konular/Threads/Темы,
Mesajlar/Messages/Сообщения, Kullanıcılar/Members/Users/пользователи) —
`extract_stats()` tek bir sabit string yerine küçük çok-dilli bir
anahtar-kelime tablosuna göre eşleştiriyor. Sayı formatı da değişiyor:
turkhackteam `1,463,871` (virgülle binlik), bdfclub `153.298` (noktayla) —
`_parse_count()` metindeki tüm rakamları birleştirip int'e çeviriyor,
ayraç ne olursa olsun çalışıyor.

**Canlı bulunan gerçek bug:** İlk implementasyon `soup.find()` ile "ilk
`data-widget-definition` içinde 'statistic' geçen eleman"ı alıyordu.
bdfclub'da bu, sayıları hiç taşımayan bir "en çok mesaj atan üyeler"
karuseliydi (`live_forum_statistics` anahtarı "statistics" kelimesini
taşıyor ama `dt`/`dd` çifti sıfır) — gerçek sayılar ondan SONRA gelen ikinci
`forumStatistics` widget'ındaydı, yani bdfclub hep `None` dönüyordu.
Düzeltme: tüm adaylar (`find_all`) sırayla denenip ilk gerçek sonuç veren
kullanılıyor — bkz. `tests/test_forum_stats.py::
test_extract_stats_skips_an_earlier_empty_statistics_widget`.

**Tek anlık görüntü sadece büyüklük verir, aktivite vermez** — "şu an ne
kadar hareketli" için en az iki snapshot gerekiyor. `StatsStore.
activity_per_day()` bunu, forumun kendi 24-saatlik "bugün X mesaj"
alanına güvenmek yerine (bunu sadece bdfclub'ın bir varyantı gösteriyor,
tutarlı bir alan değil), iki snapshot arasındaki gerçek mesaj-sayısı
farkından türetiyor — 1 saatten kısa aralıkta veya sayaç geriye
gittiğinde (site sıfırlama/yanlış-scrape) `None` döner, uydurma bir oran
üretmez.

**Kullanım:** `python -m forumtracker.cli --stats` (tek seferlik snapshot
+ o ana kadar biriken geçmişten aktivite oranı), `--loop` modunda her
cycle otomatik snapshot alınıyor (`pipeline.py`, `STATS_DB`). Bu oranın
`compute_popularity_score()`'a nasıl karışacağı (ya da karışıp
karışmayacağı) henüz karar verilmedi — bu bilinçli olarak ayrı bırakıldı,
mevcut thread-seviyesi skoru sessizce değiştirmemek için.

## Çapraz-forum kullanıcı adı eşleştirme (v2) — `identity_correlation.py`

Kullanıcı talebi: bir platformdaki tüm kullanıcı isimlerini çekip diğer
forumlarda aratıp eşleştirmek — aynı nickname birden fazla forumda
görülüyorsa bunu tek bir gerçek aktörün çapraz-platform varlığı olarak
işaretlemek.

**Aktif arama canlı test edildi ve çalışmıyor — bu bir varsayım değil,
ölçülmüş sonuç.** 21 forumun (15 XenForo + 6 MyBB) tamamında misafir
member-search denendi:

- XenForo `/search/member?q=` — 14/15 forumda 404 (route kapalı), 1
  forumda (turkhackteam) gerçek login duvarına düşüyor.
- XenForo'nun otomatik-tamamlama endpoint'i `/find-member?q=` — denenen
  4 forumun tamamında 404 (login + CSRF token gerektiriyor).
- MyBB `member.php?action=search` — 6 forumun tamamı 200 dönüyor ama
  gövde tamamen boş (gerçek CSRF token'lı bir POST + oturum gerektiriyor).

Yani forumlar üye aramasını bilerek login arkasına kilitlemiş — bu tam da
bu tür otomatik enumerasyona karşı bir önlem, bypass edilecek bir bug
değil.

**Gerçekten çalışan ve yeni ağ isteği gerektirmeyen kısım:** zaten her
incident `threat_actor.name` (yazan kişinin forum kullanıcı adı) ve
`source.name` (`forum:<site_name>`) taşıyor. `IdentityStore` bunları
(kullanıcı_adı, forum) çiftleri olarak biriktiriyor; bir kullanıcı adı
2+ farklı forumda görülünce **çapraz-platform eşleşmesi** olarak
işaretleniyor. Bu, aktif bir aramadan daha güçlü bir yaklaşım — her yeni
`--loop` cycle'ı veriyi büyütüyor, tek seferlik bir taramanın
göremeyeceği eşleşmeleri zamanla ortaya çıkarıyor.

`backfill_from_feed()` ile mevcut `incidents.jsonl`'den (2362 kayıt)
geriye dönük dolduruldu — **canlı sonuç: 59 gerçek çapraz-forum eşleşmesi**,
örnekler:

```
Aisus        -> bdfclub, forumteam.top, spyhackerz, xreactor
Angedaniya   -> blackbones, forumteam.top, leetforums
stevee       -> darkforums, leakforum.io, niflheim
```

Genel rol isimleri (`admin`, `unknown`, `moderator`, `system`, ...)
kasıtlı olarak dışlanıyor — aksi halde her forumda trivial olarak
"eşleşirlerdi". Eşleştirme büyük/küçük harf duyarsız (case-insensitive).

**Kullanım:** `python -m forumtracker.cli --identity` (mevcut eşleşmeleri
raporlar), `--identity --identity-backfill` (önce `incidents.jsonl`'den
doldurur, sonra raporlar). `--loop` modunda her cycle'ın yeni incident'leri
otomatik olarak kaydediliyor; bir kullanıcı adı o cycle'da **ilk kez** 2.
foruma geçtiğinde (her görülüşte değil, sadece keşif anında)
`notify_telegram.notify_identity_match()` ile Telegram'a bildiriliyor.

**Canlı bulunan iki gerçek bug (düzeltildi):** (1) `IdentityStore.
all_matches()` bir kullanıcının forum listesini ilk eşleşme anındaki
donmuş bir JSON snapshot'tan okuyordu — 3./4. forum sonradan eklendiğinde
rapor güncellenmiyordu; artık her zaman `username_forums` tablosundan canlı
hesaplanıyor. (2) `record_incidents()` `None`/dict-olmayan bir kayıtla
karşılaşınca hata loglamaya çalışırken kendisi patlıyordu; artık tip
kontrolüyle en baştan atlanıyor. İkisi de kendi testlerimizle yakalandı
(`tests/test_identity_correlation.py`).

## Küresel kullanıcı havuzu — `member_directory.py`

Kullanıcı talebi (threatactorusernames.com referans alınarak): "önce bir db
oluşturucaz ve ordan global bütün forumlar üzerinden kullanıcı db çekicez,
elimizde bir tehdit aktör havuzu oluşturucaz". `identity_correlation.py`
zaten aktif member-*search*'ün çalışmadığını kanıtlamıştı — ama bu, farklı
bir şey: her forumun herkese açık üye **listesi/dizini** (login gerektiren
arama değil, sade "tüm üyeleri gör" sayfası). Canlı test edildi
(2026-09-11), gerçekten farklı bir sonuç çıktı:

```
/members/ (XenForo)         8/15 forum 200 döndü, gerçek alfabetik üye listesi
                             (carders.biz, leetforums, xreactor, blackhatpakistan,
                              forumteam.top, niflheim, piratebuhta, warriors)
                             kalan 7'si (turkhackteam, spyhackerz, bdfclub, mmgp,
                             probiv, blackbones, nodo313) 403
memberlist.php (MyBB)       leakforum.io: varsayılan sayfa boş bir arama formu,
                             gerçek liste için harf başına sorgu göndermek gerekiyor
                             (4119 sayfa tek harf için) - kapsam dışı bırakıldı,
                             yanlış/eksik implementasyon yapmaktansa dürüstçe not düşüldü
```

**Neden yeniden başlatılabilir (resumable) olmak zorunda:** çalışan 8
forumun üye sayısı birkaç yüzden (xreactor) 179.007'ye (piratebuhta) kadar
değişiyor — en büyüklerin tam taraması binlerce sayfa. `MemberDirectoryStore`
her forum için (alfa harfi, sayfa) checkpoint'i tutuyor; `crawl_batch()`
her `--loop` cycle'ında/CI çalıştırmasında sınırlı sayıda sayfa (varsayılan
3/forum) ilerletip kaldığı yerden devam ediyor — `forum_stats.py`'nin
snapshot yaklaşımıyla aynı "her cycle küçük, sınırlı bir artış" deseni,
CI'nin zaman bütçesine sığmak için.

**Canlı doğrulandı:** ilk çalıştırmada (2 sayfa/forum) 8 forumdan **245
gerçek kullanıcı adı** çekildi tek seferde.

**İki farklı güven seviyesi, bilinçli olarak ayrı tutuluyor:**
- `identity_correlation.IdentityStore` — **güçlü**: kullanıcı gerçekten bir
  şey PAYLAŞMIŞ (bizim zaten topladığımız incident'lerden).
- `member_directory.MemberDirectoryStore.cross_forum_registered()` —
  **zayıf**: kullanıcının hesabı var ama hiç post attığını görmemiş
  olabiliriz (sadece kayıtlı).

İkisi birleştirilmiyor — bir çağıran hangi tür kanıta baktığını her zaman
biliyor.

**Kullanım:** `python -m forumtracker.cli --crawl-members` (varsayılan 3
sayfa/forum, `--crawl-pages N` ile ayarlanabilir). `--loop`/`--once` her
cycle'da otomatik bir artış yapıyor (`pipeline.run_full_cycle()`'a
eklendi).

**Bunun somut sonucu — `mrpink` profili:** `identity_correlation` + mevcut
`incidents.jsonl` üzerinden gerçek bir örnek: `mrpink` kullanıcısı
bdfclub.com + probiv.work'te 19 farklı ilan paylaşmış (kimlik hırsızlığı,
devlet kurumlarından veri sorgulama, sahte belge, banka/devlet
çalışanlarına içeriden veri sızdırma teklifi), hepsi ekran görüntüsüyle
kayıtlı, ilanlardan birinde kendi Telegram/e-posta bilgileri bile
görünüyor. threatactorusernames.com'un verdiği tek bilgi `{"username":
"mrpink", "forum": "..."}` iken, bizimki tarih + tam içerik + kanıt
sağlıyor.

### Global genişleme batch'i (2026-09-12)

Kullanıcı talebi: "bu forumlar çok az, bize global bütün darkforumlar
lazım". Bir araştırma turu ~28 aday domain'i (deface/carding/leak
forumları, threat-intel yazılarından ve forum dizinlerinden toplandı)
gerçek HTTP isteğiyle canlı test etti — hem araştırma turunda hem burada
ayrıca bağımsız olarak. Sonuç:

- **6 yeni forum `FORUM_POOL`'a eklendi** (RSS 200 + gerçek `<item>` içeriği
  doğrulandı): `darknetarmy`, `carding.pw`, `validmarket`, `cardinggame`,
  `darkwebmafias`, `antichat` (bu sonuncusu standart
  `/forums/-/index.rss` yerine kendi `/news.rss` yolunu kullanıyor —
  `FORUM_POOL`'un `rss` değeri düz bir URL olduğu için kod değişikliği
  gerekmedi). RSS havuzu **22 → 28** forum oldu.
- **1 yeni forum `MEMBER_DIRECTORY_POOL`'a eklendi**: `cardinggame.site`
  (`/members/` 200 + gerçek alfabetik liste). Diğer 5 yeni forum ya 403
  (`carding.pw`, `validmarket.io`, `darkwebmafias.net`) ya 404
  (`forum.antichat.io`) ya da alfa-indeksli listeleme yerine farklı bir
  "notable members" şablonu (`darknetarmy.st`) döndürdüğü için eklenmedi —
  hepsi canlı test edilip elendi, tahmin edilmedi.
- **Bilinçli olarak eklenmeyenler** (gerçek/aktif ama uygun değil):
  Cloudflare/bot-duvarı arkasında olup bu ortamdan erişilemeyen
  (`hackforums.net`, `ezcarder.is`, `procrd.cc`, `dublikat.cc`,
  `exploit.in`), RSS'i olmayan gerçek forumlar (`cracked.st`,
  `wwh-club.net`, `omerta.cc`, `greysec.net`, `carder.uk`,
  `cardingsecrets.is`), el konulmuş/ölü/squat edilmiş domainler
  (`leakbase.la`, `nulled.cc`, `omerta.vc`, `xss.is`/`.ac`), ve
  `crdpro.at` — RSS gövdesi gerçek ve geçerli ama endpoint sürekli 404
  döndürüyor (200 değil), bu yüzden havuzun kendi canlılık barını
  geçemedi; 200 dönmeye başlarsa eklenebilir.

### deepdarkCTI birleştirme batch'i (2026-09-12)

Kullanıcı talebi: bu modülün orijinal seed listesi zaten deepdarkCTI'den
türetilmişti (bkz. dosya başındaki docstring) — güncel
`github.com/fastfire/deepdarkCTI/forum.md`'yi tekrar çekip mevcut
havuzla mutabakat sağlamak istendi. Listedeki 300+ satırdan (çoğu .onion
veya OFFLINE/seized mirror) clearnet + "ONLINE" etiketli, zaten
havuzda/elenmiş olmayan **134 aday** kaldı; hepsi canlı test edildi
(RSS 200 + gerçek `<item>` + konu-uygunluğu örneklemesi):

- **3 gerçekten yeni forum eklendi**: `0x00sec` (Ghost/RSS, güvenlik
  araştırması topluluğu), `nulledbb` (MyBB, credential/mail-access
  dump'ları), `voided` (MyBB, aynı tür). RSS havuzu **28 → 31**.
- **2 aday, zaten havuzdaki bir forumun ayna (mirror) domaini çıktı** —
  yeniden eklenmedi: `leakforum.st`, zaten havuzdaki `leakforum.io` ile
  bayt-bayt aynı feed'i veriyor (aynı linkler, aynı guid); `toolba.se`,
  zaten havuzdaki `www.toolbase.bz`'e 301 yönlendiriyor.
- **43 aday** erişilebilir ama çalışan bir RSS'i yok (ör. `lolz.guru` bot
  duvarı, `dumped.to` boş JS yönlendirmesi, `cracked.sh` kendi ilan
  ettiği RSS linki 404 veriyor).
- **80 aday** erişilemez/ölü/parklanmış (~40'ı artık kayıtlı bile değil,
  geri kalanı Cloudflare/DDoS-Guard duvarı, squat edilmiş domain, veya
  DNS-sinkhole).
- **3 aday, RSS çalışıyor ama konu dışı olduğu için eklenmedi**:
  `thejavasea.com` (yetişkin içerik ağırlıklı), `skynetzone.pw` (Vietnam
  bahis sitesine squat edilmiş), `4cheat.org`/`4cheat.ru` (oyun hile
  topluluğu, underground/CTI'yle ilgisiz).

## Ransomware takibi — `ransomware_intel.py` + `entity_extraction.py`

Roadmap'in 3. maddesi: "ransomware forumlarını özel olarak takip etmek".
Ayrı bir forum alt-havuzu kurmak yerine (RaaS grupları belirli bir foruma
bağlı değil, hepsine dağılmış durumda) her kaynaktan gelen her yeni kayda
iki deterministik sinyal uygulanıyor — `pipeline._apply_ransomware_intel()`,
`enrich()`'ten hemen sonra çalışıyor (yani zenginleştirilmiş tam metin
üzerinde, RSS özeti değil):

**1. Bilinen RaaS grup adı eşleştirme** (`ransomware_intel.
match_ransomware_group`) — LockBit, Akira, Qilin, RansomHub, BlackCat/ALPHV,
Clop, Medusa, BianLian, Play, Hive, Conti gibi ~45 aktif/geçmiş grup +
takma ad. **Bilinçli tasarım:** grup isimleri iki kademede tutuluyor —
"lockbit", "akira", "rhysida" gibi ayırt edici isimler tek başına eşleşir;
"play", "meow", "hive", "royal", "fog", "medusa" gibi sıradan İngilizce
kelime/marka isimleriyle çakışan gruplar SADECE "play ransomware", "meow
leaks" gibi nitelikli ifadelerle eşleşir — yoksa "oyun oynadık" gibi
alakasız bir cümle yanlışlıkla LockBit-tarzı bir tespit üretir. Canlı bir
hata da bu şekilde yakalandı ve düzeltildi: "conti" kısa bir kelime olduğu
için ilk halde substring eşleşmesi kullanıyordu, İspanyolca bir veritabanı
sızıntısı gönderisindeki "... contiene informacion ..." ifadesi içinde
yanlışlıkla eşleşti — düzeltme: tüm grup isimleri artık sadece kelime-sınırı
(`\bterm\b`) ile eşleşiyor, asla düz substring değil.

**2. Fidye notu kalıp tanıma** (`has_ransom_note_pattern`) — grup listesinde
olmayan/yeni rebrand olmuş bir grubu da yakalamak için: "we have encrypted
your network", "pay us within N hours", "data will be published", vb. 6
kalıp.

İkisinden biri eşleşirse: `category` (sadece `forum_post` ise, daha
spesifik bir kategori asla düşürülmez) `ransomware_leak`'e yükseliyor,
`extra.ransomware_group` / `extra.ransom_note_pattern` set ediliyor,
`compute_risk_score()`'a isimlendirilmiş-grup bonusu (+1, genel
"ransomware" keyword'ünden daha güçlü bir sinyal olduğu için) ekleniyor.

**Ek olarak (aynı adımda):** `entity_extraction.extract_victim_domain()`
metindeki ilk makul alan adını `victim.website`'e yazıyor — şema zaten bu
alanı içeriyordu, hiç doldurulmamıştı. `Victim.name` bilerek değiştirilmedi
(ham thread başlığı kalıyor, `incident_id` hash'i buna bağlı) — `website`
sadece EK bir temiz kimlik alanı. Forumun kendi domaini (`exclude_hostname`)
ve mega.nz/pastebin/t.me/ibb.co gibi barındırma-sitesi domainleri hariç
tutuluyor (canlı taramada bunların "mağdur" gibi göründüğü gerçek yanlış
pozitifler görüldü ve listeye eklendi: `mega.io`, `anonfile.com` de bu
şekilde bulunup eklendi).

Canlı doğrulama, tüm havuz üzerinde tek çekim (425 kayıt, sadece RSS, thread
enrich olmadan): **64 kayıtta gerçek bir alan adı çıkarıldı**, en dikkat
çekici örnek — "Birleşmiş Milletler Kalkınma Programı HACKED! ANKA TEAM"
başlıklı bir gönderiden `victim.website: undp.org` doğru şekilde çıkarıldı.
**Bilinen kısıt:** çıkarım "metindeki ilk domain-benzeri string" mantığıyla
çalışıyor, gerçek mağdur olup olmadığını anlamıyor — `github.com`,
`store.steampowered.com` gibi bazı genel platform referansları da zaman
zaman yanlışlıkla `victim.website` olarak çıkabiliyor; bunlar açık bariz
barındırma siteleri (mega.nz, pastebin) kadar net olmadığı için listede
değiller, kabul edilen bir gürültü payı.

**Canlı doğrulama — RaaS grup eşleştirme (aynı 425 kayıtlık tur):** düzeltme
sonrası **0 yanlış pozitif** (önceki turda `conti` iki kez farklı şekilde
yanlış eşleşmişti — biri "contiene" substring bug'ı, biri "Conti VPN"
marka adı; ikisi de yukarıdaki değişikliklerle giderildi). Bu spesifik
taramada gerçek bir RaaS grup mention'ı da çıkmadı — havuzdaki forumlar
ağırlıklı olarak carding/combolist/database-satış içeriği, ki bu da
zaten `ransomware_leak` kategorisine ek olarak alarmların çoğunu
`data_leak`/`forum_post` tarafında tutuyor; grup eşleşmesi (LockBit,
Akira vb.) daha çok DarkForums/BreachForums gibi sızıntı-odaklı
forumlarda ve gelecekteki Telegram/döngü çalıştırmalarında beklenir.

**Doğrulama:** `tests/test_ransomware_intel.py` (8 test, ayırt edici/
belirsiz grup ayrımı dahil) + `tests/test_entity_extraction.py` (6 test) +
`tests/test_pipeline.py`'a 3 yeni test (kategori yükseltme, mevcut daha
spesifik kategoriyi asla düşürmeme, alakasız gönderide no-op).

## Tarayıcı-tabanlı yedek kaynak — `browser_forum.py`

**Neden var:** [brunosergi/darkweb-forums-tracker](https://github.com/brunosergi/darkweb-forums-tracker)
adlı bir referans projeyi inceledik — DarkForums.st'i n8n + Playwright MCP +
bir LLM ajanı (Gemini) ile izleyen, CAPTCHA/login için VNC üzerinden insan
müdahalesi alan bir sistem. Bizim mimarimize (n8n/Docker/LLM ajanı yok, sade
Python) uygun değildi, ama fikir aynen geçerliydi: **RSS'i olmayan/anti-bot
duvarlı forumlar için gerçek bir tarayıcı gerekebilir.**

**Araştırma bulguları:**
1. RSS keşfinde 6 aday hiç RSS döndürmedi: `comfybox.floofey.dog`,
   `cryptogid.org`, `enclave.cc`, `nulledbb.com`, `patched.to`, `voided.to`.
2. Bunların hepsi düz `requests` ile **tam HTML** döndürdü — CAPTCHA/
   Cloudflare/DDoS-Guard duvarı yoktu. Yani gerçek bir tarayıcı (Playwright)
   *teknik olarak* bu 6 site için şart değil; yine de kullanıcı tercihiyle
   kuruldu — ileride JS-render veya anti-bot duvarı olan bir hedef
   çıkarsa hazır altyapı olsun diye.
3. İçerik/erişilebilirlik taraması:
   - `comfybox.floofey.dog`: gerçek, ama genel bir furry/hobi topluluk
     forumu — CTI açısından konu dışı.
   - `cryptogid.org`: ana akım bir Rus kripto/Bitcoin forumu — konu dışı
     (bits.media ile aynı profil).
   - `enclave.cc`: gerçek carding içeriği var (bir "CC/FULLZ & Non-VBV
     Dumps" konusu ana sayfada sponsor banner olarak görünüyordu) ama ana
     sayfa gerçek bir "son konular" listesi değil — dönen bir reklam
     banner'ı, güvenilir şekilde yeni içerik taranamaz. Konunun kendisi de
     misafir oturuma "Sorry, you do not have permission for that!" döndü —
     login duvarı. Havuza eklenmedi; bu sayfa sadece `_classify_blocked()`
     testinde login-duvarı fixture'ı olarak kullanılıyor.
   - `nulledbb.com` / `voided.to`: gerçek MyBB-ailesi forumlar ama ana
     sayfaları sadece board/kategori adlarını gösteriyor ("The Lounge",
     "Giveaways", "Upgraded Tools") — hangi board'un CTI-ilgili olduğu için
     ayrı bir inceleme gerekiyor, henüz yapılmadı.
   - `patched.to`: login duvarı yok, ve `/Forum-refunding-services` board'u
     gerçek, sayfalanmış, CTI-ilgili konu listesi + tam ilk-mesaj metni
     içeriyor (refund-fraud-as-a-service reklamları). **Somut olarak
     bağlanan tek site bu.**
4. Sonuç: `BROWSER_FORUM_POOL` şu an sadece `patched.to`'yu içeriyor.
   Diğer 5 aday ya konu dışı (comfybox, cryptogid) ya da daha fazla
   inceleme bekliyor (enclave, nulledbb, voided) — bilerek küçük ve
   doğrulanmış tutuldu, "erişilebilir" olması tek başına yeterli
   sayılmadı (aynı disiplin Altenens kararında da uygulanmıştı).

**Nasıl çalışıyor:** `BrowserForumSource.fetch()` tek bir headless Chromium
başlatır (`playwright.sync_api`), her site için board sayfasına gider,
`page.content()` ile render edilmiş HTML'i alır, `_classify_blocked()` ile
CAPTCHA/login-duvarı imzalarına karşı kontrol eder (engellenmişse o siteyi
o tur için sessizce atlar), sonra BeautifulSoup ile konu satırlarını
ayrıştırır. İlk birkaç konu için ayrıca thread sayfasına gidip gerçek
ilk-mesaj metnini çeker (`MAX_THREAD_BODIES_PER_SITE` ile sınırlı). Ayrıştırma
mantığı (`_parse_*`/`_extract_*`/`_classify_blocked`) saf fonksiyonlar olarak
yazıldı, bu yüzden testler gerçek tarayıcı/ağ olmadan `tests/fixtures/`
altındaki kaydedilmiş gerçek HTML'e karşı çalışır — RSS kaynağının
`_normalize()` test deseniyle aynı felsefe.

## Alarm katmanı — `alerting.py`

Kullanıcı örnek olarak gerçek OSINT-CTI Telegram botlarından (bir "OsintCTI
Threat Alert" kartı, bir de `t.me/breachdetect` "Data Leak Monitor" formatı)
ekran görüntüsü paylaştı ve "forumları böyle canlı izleyip bu yapıda bir şey
üretelim" dedi. İki farklı kart görülüyordu: biri LLM ile üretilmiş zengin bir
analiz (risk skoru, ülke tahmini, PII alan listesi...), diğeri ham kaynak
verisinden doğrudan üretilebilecek sade bir yapı (Source/Content/author/
Detection Date/Type). Burada LLM'siz, tamamen deterministik bir orta yol
seçildi — hem "cahil" bir kullanıcının hızlı okuyabileceği hem de bir CTI
uzmanının rakamların nereden geldiğini denetleyebileceği bir çift kart:

- **`match_entities(text)`** — brunosergi/darkweb-forums-tracker'ın n8n
  "Entity Matching Code" node'undaki algoritmanın birebir Python portu:
  aksan temizleme, kısa terimler için kelime-sınırı (`\bssn\b`), çok
  kelimeli terimler için substring, tekli uzun kelimeler için ikisi birden.
  `KEYWORD_GROUPS` küçük bir başlangıç seti (ransomware, database, combolist,
  fullz/cvv, ssn, breach/leak, government, bank) — **kendi izlemek
  istediğin marka/kurum/CVE adlarını buraya eklemen bekleniyor**, tıpkı
  `FORUM_POOL`'a yeni bir forum eklemek gibi tek satırlık bir iş.
- **`compute_risk_score(incident)`** — 1-10, kategori tabanlı taban puan +
  eşleşen keyword sayısı + `confidence: verified` bonusu. Şeffaf: bir
  analist puanın nereden geldiğini kod okuyarak görebilir, kara kutu değil.
- **`to_alert_card(incident)`** — aynı incident'ten iki şekil üretir:
  `simple` (kullanıcının 3. görselindeki Source/Content/author/Detection
  Date/Type yapısının birebir aynısı) ve `rich` (2. görseldeki Target/
  Threat Actor/Country/Intel Source/Detection Date/Risk Score/Brief
  Summary/Source Link yapısına yakın, artı `matched_entities`).
- **`is_alert_worthy(incident)`** — herhangi bir keyword eşleşirse ya da
  risk skoru eşiği (varsayılan 6) geçerse `True`. `pipeline.build_alerts()`
  yeni incident'leri bundan geçirip sadece eşleşenleri `data/alerts.jsonl`'a
  yazar; `python -m forumtracker.cli --once` da hem incident'leri hem
  alarmları basar.

**Ekran görüntüsü (LeakRadar tarzı, "ekstra" olarak istendi):**
`browser_forum.py` zaten her thread için Playwright ile o sayfaya gidiyordu
— `is_alert_worthy()` ile eşleşen thread'lerde artık `page.screenshot()` da
çağrılıp `data/screenshots/` altına kaydediliyor ve `evidence.screenshot_url`
alanına o yerel dosya yolu yazılıyor (henüz bir görsel host bağlı değil, bu
yüzden web URL'i değil — bkz. şemadaki alan açıklaması).

`underground_forums.py` (asıl 21 forumluk RSS havuzu) artık **her yeni
kayıt için**, alarm eşiğine bakmaksızın aynı şekilde ekran görüntüsü
alıyor — `Source.enrich_batch()` (yeni, `base.py`'da `enrich()`'in toplu
hali) tek bir paylaşılan Playwright tarayıcısı açıp o döngüdeki tüm yeni
kayıtları gezip kapatıyor, incident başına ayrı Chromium açmak yerine (asıl
maliyet tarayıcı açılışı, sayfa gezme değil).

Canlı geliştirmede yakalanan gerçek bir hata: carders.biz (ve muhtemelen
aynı temayı kullanan başka XenForo forumları) `domcontentloaded` anında
gönderi metnini DOM'a zaten koymuş oluyor — yani `wait_for_selector`
anında geçiyor — ama üstünde birkaç saniyelik zamanlayıcıyla kapanan tam
ekran bir "Bookmark our domains" splash overlay'i duruyor. Bu yüzden
`wait_for_selector` başarıyla dönse bile ekran görüntüsü hâlâ splash'ı
yakalıyordu (içerik var/görünür değil ayrımı). Düzeltme: selector
bekledikten sonra sabit bir `SCREENSHOT_OVERLAY_SETTLE_MS` (3sn) bekliyor,
sonra gerçek gönderiyi görünür alana kaydırıyor
(`scroll_into_view_if_needed`) — aksi halde üst kısımdaki reklam
şeritleri çekilirdi. `_save_screenshot()`'ın docstring'i bu sırayı
açıklıyor.

## .onion forumlarına erişim — güncelleme

**Düzeltme (2026-09):** Önceki turda "bu ortamdan Tor bootstrap edilemiyor"
diye yazmıştık. Bu artık yanlış — bu makinede portable Tor Expert Bundle
(admin hakkı gerektirmeden `dist.torproject.org`'dan indirilip
çıkarılabiliyor) ile gerçek bir Tor süreci canlı bootstrap edildi ve gerçek
`.onion` adreslerine (arama motorları, dizinler, en az bir ransomware-grup
sitesi) SOCKS5 (`127.0.0.1:9050`) üzerinden başarıyla bağlanıldığı
doğrulandı — bkz. `health.py`'nin Tor-aware `check_one()`'ı, bu keşfin
doğrudan sonucu. Aşağıdaki eski liste hâlâ geçerli, sadece 1. madde artık
gerekli değil (bootstrap otomatik yapılabiliyor):

1. `pip install pysocks` sonrası `requests`'e
   `proxies={"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}`
   verilerek aynı `Source` arayüzüyle bir `darkweb_onion.py` yazılabilir —
   deepdarkCTI listesindeki ONLINE .onion adresleri hedef havuzu olur. Henüz
   yazılmadı — bu bir sonraki adım, `health.py`'nin adres-değişikliği
   altyapısı bunun üstüne kurulacak şekilde tasarlandı (bkz. aşağıdaki
   bölüm).
2. Önemli sınır: ciddi carding/cybercrime forumlarının çoğu misafir
   görünümünü çok kısıtlı tutar — gerçek "sohbet" kapsamı için çoğunlukla
   kayıtlı (bazen vouch edilmiş) bir hesap gerekir; bu otomatik
   kurulamayacak bir adım.

## Forum canlılık takibi ve adres değişikliği tespiti — `health.py`

Dark/underground forumlar sık adres değiştirir (takedown, DDoS, gönüllü
taşınma) — bir mirror rotasyonu, "bakımda" hatasından farklı olarak, eski
adresin tamamen çözümlenmez/bağlanılamaz hale gelmesiyle kendini belli eder
(hata sayfası değil, DNS/bağlantı hatası). `health.py` bu ikisini ayırt
ediyor:

- **`up`** — HTTP yanıtı geldi, status < 500 (adres yaşıyor)
- **`degraded`** — HTTP yanıtı geldi ama 5xx (sunucu hatası, adres yine de
  yaşıyor — mirror avına gerek yok)
- **`unreachable`** — DNS/bağlantı/timeout hatası (adres muhtemelen artık
  yok)

`HealthStore` (SQLite, `dedup.py`'nin `SeenStore`'uyla aynı "tek dosya, ekstra
altyapı yok" felsefesi) her forum için ardışık `unreachable` sayısını
tutuyor; `DEAD_THRESHOLD` (3) art arda geçildiğinde forum "muhtemelen adres
değiştirdi" olarak işaretleniyor — tek seferlik bir kesinti (yeniden başlatma,
geçici DDoS-Guard) eşiği geçmez, sadece sürdürülen bir kesinti geçer. `.onion`
homepage'ler otomatik olarak Tor SOCKS proxy'sinden geçiyor (yukarıdaki
bölümde doğrulanan kurulumla aynı).

**Yeni adres keşfi `discover.py`'yi yeniden kullanıyor:** Bir forum
`DEAD_THRESHOLD`'u geçtiğinde, `discover.find_mirror_candidates(dead_name)`
zaten var olan "Telegram'da bahsedilen ama havuzda olmayan hostname"
sinyalini (`discover_candidates()`) alıp, ölü forumun ismiyle metin
benzerliğine göre sıralıyor (`difflib.SequenceMatcher` + isim
içerme/içerilme durumunda benzerlik 0.8'e sabitleniyor — bir mirror hemen
her zaman markayı yeni hostname'de de taşır, ör. "darkforums" ölünce
Telegram'da "darkforums-official.cc" belirmesi gibi).

**Otomatik adres geçişi — `auto_mirror.py`:** Önceden bu sadece bir öneri
listesiydi — forum ölünce birinin Telegram uyarısını görüp `FORUM_POOL`'u
elle düzenlemesi gerekiyordu, bu da günlerce süren bir veri boşluğu
anlamına geliyordu ("forum kapandığında çöp olma" problemi). Artık
`pipeline.run_full_cycle`, bir forum eşiği geçtiği anda otomatik olarak bir
mirror benimsemeyi DENİYOR — ama iki sert koşul birden sağlanmadan hiçbir
şeye güvenmiyor: (1) benzerlik ≥0.8 (marka adı yeni hostname'de birebir
geçiyor) VE (2) **canlı doğrulama** — `UndergroundForumSource` adayın
RSS/HTML'ini gerçekten çekip en az 1 gerçek incident üretmek zorunda (boş/
park edilmiş/phishing klon bir domain bunu geçemez). İkisi birden
sağlanırsa adres, `data/auto_mirrors.json`'a kaydediliyor ve **bir sonraki
pipeline döngüsü** (zaten her 30 dakikada taze bir process — bkz. CI/CD
bölümü) aynı forum ismi altında otomatik olarak takibe devam ediyor —
kod değişikliği gerekmeden. Telegram uyarısı artık "otomatik olarak yeni
adrese geçildi" diyor ama yine de manuel inceleme öneriyor — canlı ve
RSS-şekilli olmak, incelenmiş ve güvenli olmakla aynı garanti değil.
`FORUM_POOL`'un kendi kaynak kodu hâlâ hiç otomatik değişmiyor; bu ayrı,
üzerine binen bir katman.

**Kullanım:**

```bash
python -m forumtracker.cli --health
```

Tek seferlik tam rapor (her forumun durumu + varsa mirror önerileri) JSON
olarak basılır. `--loop` modunda her döngüde otomatik çalışır
(`pipeline.check_forum_health()`); bir forum o döngüde eşiği **yeni**
geçtiyse (önceki döngüde zaten geçmemişse) `notify_telegram.
notify_health_alert()` ile Telegram'a düz metin uyarı + bulunan mirror
adayları gönderilir — aynı forum için her döngüde tekrar tekrar spam
atmıyor, sadece kesinti başladığı anda bir kez.

Bu oturumda canlı doğrulandı: 23 havuz forumunun tamamı `up` döndü, sıfır
yanlış pozitif.

## Telegram kaynağı — `telegram_bridge.py` + `discover.py`

**Düzeltme:** Önceki turda "breachdetect gated, Telethon + telefon numarası
şart" diye yazmıştık. Bu yanlıştı — kimliksiz `t.me/s/breachdetect` isteği
gerçekten login sayfasına yönlendiriyor (bunu tekrar canlı doğruladık), ama
**`tg.i-c-a.su` adlı ücretsiz, kimlik doğrulamasız bir Telegram→RSS köprüsü**
(`https://tg.i-c-a.su/rss/breachdetect`) aynı içeriği hiçbir oturum açmadan
veriyor — bu bizim değil, üçüncü taraf küçük bir servis (kaynağı bilinmiyor,
paylaşılan/agresif bir rate limit'i var: art arda farklı kanal istekleri
birkaç saniyeliğine 429/403 döndürüyor), ama canlı çalıştığı doğrulandı.
`ctifeeds` ve `DarkWebInformer` bu köprüde denendi, ikisi de "not a public
channel / unlock in NNs" kilidine takıldı — muhtemelen bu servisin daha önce
hiç istenmemiş kanallar için bir "ısınma" süresi var; `TELEGRAM_BRIDGE_POOL`'a
eklenip tekrar denenebilir, `fetch()` başarısız kanalı o tur için atlayıp
diğerlerini etkilemez.

**`telegram_bridge.py`** — her breachdetect gönderisi zaten kullanıcının
ekran görüntüsündeki `Source`/`Content`/`author`/`Detection Date`/`Type`
JSON'ının kendisi (RSS `<description>` içinde çift HTML-escape'li) —
`_parse_item()` bunu decode edip regex'le ayrıştırıyor. Zorlu kısım:
`Content` alanı bazen kendi içinde bir `<a href="...">` taşıyor (ör.
"[Checkermax.pro - Ultimate Solana Wallet Checker]"), bu da naif bir
`"Content":\s*"([^"]*)"` regex'ini o iç tırnakta kırıyordu — çözüm: önce
`Source`'u orijinal (tag'li) metinden `href` ile çıkar, sonra geri kalan
tüm alanları HTML tag'leri temizlenmiş metinden çıkar.

**`discover.py`** — kullanıcının asıl istediği şey buydu: "oradan verilen
source kaynaklarını çekip forum kategorisini zenginleştirmek". Her
telegram_bridge kaydının `extra.referenced_source_url` alanı breachdetect'in
kendi "Source" alanıdır (hangi forumdan geldiği) — `discover_candidates()`
bunu `FORUM_POOL`/`BROWSER_FORUM_POOL`'daki mevcut hostname'lerle
karşılaştırıp yeni adayları sıklığa göre sıralıyor. **Kasıtlı olarak
otomatik eklemiyor** — her önceki ekleme (altenens/hard-tm/dublikat.club/
seopirat/bits.media/mipped/openssource) canlı içerik incelemesinden geçti,
bir Telegram gönderisinde bir kez geçen hostname bu incelemeyi atlamak için
yeterli değil. Çalıştır:

```bash
python -m forumtracker.discover
```

Bu oturumda canlı çalıştırıldığında gerçek çıktı:

```
4 candidate forum(s) not yet in FORUM_POOL/BROWSER_FORUM_POOL:

  ascarding.net    mentions=3   e.g. https://ascarding.net/
  4pda.to          mentions=2   e.g. https://4pda.to/
  zx-pk.ru         mentions=1   e.g. https://zx-pk.ru/
  mipped.com       mentions=1   e.g. https://mipped.com/f/
```

(`mipped.com` daha önce bilerek konu-dışı diye çıkarılmıştı — araç bunu
bilmiyor, sadece "havuzda değil" diyor; bu yüzden ekleme kararı hâlâ manuel
inceleme gerektiriyor, araç sadece adayı buluyor.)

**Alarm kartına etkisi:** `alerting.to_alert_card()`'ın `simple.Source`
alanı artık, varsa `extra.referenced_source_url`'u kullanıyor (yoksa kendi
`evidence.post_url`'üne düşüyor) — yani breachdetect'ten gelen bir kayıt
için `Source` alanı bizim Telegram mesaj linkimiz değil, breachdetect'in
işaret ettiği **orijinal forum URL'i** oluyor; kullanıcının 3. görselindeki
anlamla birebir eşleşiyor.

**Kategori düzeltmesi — canlı dashboard'da yakalanan gerçek hata:**
`t.me/breachdetect` kanalının kendi `Type` alanı **hemen her gönderide**
literal olarak `"Data leak"` — gerçek combolist/erişim satışı olsun ya da
düz tüketici teknoloji haberi olsun ("Lenovo yeni monitör çıkardı",
"MediaTek Helio G99 güncellendi", "GPT-6 Astra bir testi geçti") fark
etmiyor, kanal ikisini de aynı etiketle gönderiyor. Eskiden `_CATEGORY_MAP`
bu etikete körü körüne güveniyordu, yani bu tür haberler dashboard'da
"VERİ SIZINTISI" rozetiyle görünüyordu — bir CTI ürünü için ciddi bir
güvenilirlik sorunu. Düzeltme: `_looks_underground_relevant(content)`,
EN+RU yeraltı-pazarı kelime dağarcığına (leak, combo, fullz, webmail,
erişim, аккаунт, скам, база, vb. — `_RELEVANCE_TERMS`) karşı kontrol
ediyor; kanalın "data leak"/"data breach" etiketi veya bilinmeyen bir
`Type` bu sözlüklerden hiçbirine denk gelmiyorsa kategori nötr
`forum_post`'a düşüyor. Kanalın açıkça belirttiği daha spesifik tipler
(`ransomware`/`phishing`/`defacement`) dokunulmadan kalıyor — bunlar daha
nadir ve daha spesifik, ayrıca ransomware iddiaları zaten
`pipeline._apply_ransomware_intel()`'de ikinci, bağımsız bir kontrolden
geçiyor. Canlı veritabanında bulunan 5 yanlış etiketli kayıt (yukarıdaki
örnekler + bir Sony haberi) düzeltildi; hiçbiri zaten alarm eşiğini
geçmemişti (risk skoru 5 < 6 eşik), yani sadece ham akıştaki rozet
yanlıştı, alarm listesi etkilenmemişti.

## Telegram'a gönderme — `notify_telegram.py`

`alerting.render_telegram_message()` görsel 2'deki kart düzenini üreten
metni zaten hazırlıyordu; `notify_telegram.py` bunu gerçekten bir Telegram
kanalına POST ediyor — `requests` ile `sendMessage` çağrısı, ekstra
bağımlılık yok (python-telegram-bot vs. gerekmiyor).

```python
def send_alert(card: dict, token=None, chat_id=None) -> bool: ...   # tek kart
def notify_alerts(alerts: list[dict]) -> int: ...                    # bir döngünün tüm kartları
```

Kimlik bilgileri koddan değil ortam değişkenlerinden okunuyor:

| Değişken | Nereden alınır |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram'da @BotFather'a `/newbot` — birkaç saniye sürüyor, telefon numarası/OTP gerekmiyor (breachdetect'i okumak için kullandığımız `tg.i-c-a.su` köprüsünden tamamen ayrı bir mekanizma) |
| `TELEGRAM_CHAT_ID` | Botu hedef kanala/gruba admin ekleyip `https://api.telegram.org/bot<TOKEN>/getUpdates` çağrısıyla (bir test mesajı sonrası) `chat.id` alanından okunur — `@kanaladi` değil, sayısal id (kanallarda genelde `-100...`) |

İkisi de ortam değişkeni olarak set edilmemişse `notify_alerts()` sessizce
hiçbir şey yapmıyor (`return 0`) — Telegram yapılandırılmadan pipeline'ı
çalıştırmak davranışı değiştirmiyor, sadece `emit_alerts()` ile
`data/alerts.jsonl`'a yazmaya devam ediyor. `pipeline.loop()` ve
`cli.py --once` her döngüde `emit_alerts()`'ten hemen sonra
`notify_alerts()`'i çağırıyor.

**Doğrulama:** `tests/test_notify_telegram.py` (6 test) — `requests.post`
mock'lanarak gerçek token olmadan: doğru URL/payload'ın kurulduğu, 200
dışı yanıtların/network hatalarının sessizce `False` döndüğü (pipeline'ı
düşürmediği), kimlik bilgisi yoksa no-op olduğu doğrulanıyor.

**Hâlâ yapılmayan:** Gerçek bir bot token ile canlı gönderim testi —
kullanıcının kendi @BotFather token'ını ve hedef kanal id'sini vermesi
gerekiyor, biz onun adına bot/kanal oluşturamayız.

## Alarm kartının görsel render'ı — `render_telegram_message()`

`to_alert_card()`'ın `rich` yarısını, kullanıcının 2. ekran görüntüsündeki
kart düzenine (severity banner + emoji etiketli alan listesi) birebir
uyacak şekilde düz metne çeviriyor:

```
⚠️ MEDIUM SEVERITY LEAK DETECTED ⚠️

• Target/Title: 250k snu.gouv.fr database leak
👤 Threat Actor: LunarisSec
📍 Country: FR
🌐 Intel Source: forum:darkforums
📅 Detection Date: 2026-09-08T10:00:00+00:00
📊 Risk Score: 7/10

📝 Brief Summary: LunarisSec imzalı bir forum gönderisi: 250k snu.gouv.fr

🔗 Source Intelligence Link: https://darkforums.as/Thread-x
```

`_severity_label()` risk skorunu LOW(<4)/MEDIUM(4-6)/HIGH(7-8)/CRITICAL(9-10)
bantlarına eşliyor. Bu, henüz bir yere POST edilmiyor — `notify_telegram.py`
eklendiğinde bu metni doğrudan gönderecek fonksiyon budur.

## JSON şeması

`forumtracker/schema.py` çalışma zamanı (runtime) tanımıdır. Ürün tarafının
Python koduna bakmadan entegre olabilmesi için `schemas/incident.schema.json`
resmi kontrattır; `schemas/examples/underground_forums.example.json` (RSS
kaynağından) ve `schemas/examples/browser_forums.example.json` (Playwright
kaynağından) gerçek veriyle doldurulmuş örneklerdir — `tests/test_schema_examples.py`
her build'de ikisini de şemaya karşı doğrular. İki kaynak da aynı zarfı
üretir; ürün tarafı hangi mekanizmayla çekildiğini bilmesine gerek kalmadan
tek bir şemaya karşı entegre olabilir.

Özet şekil (DarkWebFeed'in ortak zarfıyla aynı, `source.type`/`category` bu
modülde sabit `"forum"` / `"forum_post"`):

```json
{
  "incident_id": "sha256[:16] (source+actor+victim+published_at)",
  "detected_at": "ISO8601 - tracker'ın kaydı gördüğü an",
  "published_at": "ISO8601 - RSS pubDate",
  "source": {"type": "forum", "name": "forum:<site>", "url": "..."},
  "threat_actor": {"name": "...", "aliases": []},
  "victim": {"name": "<thread başlığı>", "website": null, "country_code": null, "sector": "<forum alt kategorisi>"},
  "category": "forum_post",
  "title": "...",
  "description": "<thread'in gerçek ilk mesaj metni>",
  "evidence": {"post_url": "...", "screenshot_url": null},
  "confidence": "claimed",
  "tags": ["forum", "<site>", "..."],
  "extra": {}
}
```

## Forum/aktör önem sıralaması, sektör sınıflandırma, ülke metin fallback'i (2026-11-01 batch)

Rakip bir ürünün (vecert) Türkiye filtresiyle yan yana karşılaştırmadan
çıkan 3 somut boşluğa karşılık:

**1. "Hangi forum ŞU AN aktif" — `ranking.py` (yeni modül).** Önceden tek
sinyal, forum başına ömür boyu `incident_count`'tu — yıllar önce çok post
atmış ama artık sessiz bir forum, bu hafta gerçekten aktif olandan önde
görünüyordu. `forum_activity()` artık sabit, dokümante edilmiş bir
ağırlıklandırma kullanıyor: `activity_score = son_7g×3 + son_30g` — forum
sağlığı kartları artık bu skora göre sıralı (lifetime toplam değil), yeni
"Forum önem / aktiflik sıralaması" tablosu bunu forum başına en aktif
aktörle (`per_forum_top_actors()`) birlikte gösteriyor. Aktör tarafında
`actor_reputation()` basit, sabit bir formül: `skor = toplam_post + 2 ×
(çapraz_forum_sayısı - 1)` — tek forumda çok post atan biri yerine birden
fazla forumda görülen aktörü ödüllendiriyor (identity_correlation.py'nin
çapraz-forum sezgisiyle aynı mantık), 1-5 yıldıza bucket'lanıyor
(`actorRank` listesinde `★` olarak görünüyor). Hiçbiri ML/LLM değil —
ransomware_intel.py'nin keyword/eşik skorlama tarzıyla aynı felsefe.

**2. "Forum kapandığında çöp olmuyoruz" — `auto_mirror.py` (yeni modül).**
Bkz. yukarıdaki "Forum canlılık takibi" bölümüne eklenen ayrıntı — mirror
önerisi artık sadece Telegram'a düşmüyor, benzerlik ≥0.8 VE canlı RSS
doğrulaması (gerçek incident üretmesi) ikisi birden sağlanırsa otomatik
olarak `data/auto_mirrors.json`'a kaydedilip bir sonraki döngüden itibaren
aynı forum ismi altında takip ediliyor.

**3. `victim.sector` — gerçek bir sınıflandırma değildi.** Şemada alan
baştan beri vardı ama tek dolduran yer (`underground_forums.py`'nin RSS
normalize'i) forumun kendi ALT-FORUM/BOARD kategorisini ("Dos & Ddos", "tht
yardım merkezi") yazıyordu — bu bir navigasyon etiketi, mağdurun endüstrisi
değil (zaten `tags`'te duruyor, dokunulmadı). Diğer kaynaklarda (Telegram,
browser) alan hep boştu. Yeni `sector_classification.py`, tıpkı
`country_code`'daki gibi domain-önce/metin-fallback deseniyle çalışıyor:
`sector_from_domain()` domain etiketlerinden (`gov`/`mil`→government,
`edu`/`ac`→education — ccTLD'den bağımsız, yapısal bir isimlendirme
kuralı, ör. `spk.gov.tr`→government), `sector_from_text()` ise
İngilizce+Türkçe anahtar kelime/ifade eşleştirmesiyle ("bank", "GSM",
"hastane", "üniversite", "bakanlığı"...) çalışıyor. **Dürüst sınır:**
vecert'in kendi kartlarında da sık görülen "UNCLASSIFIED" durumu burada da
gerçek — bir leak başlığı genelde kurumun ADINI verir, sektörünü değil
("Ziraat Bank" geçer, "a financial institution" geçmez), o yüzden bu katman
geniş kapsama değil, sadece açık/jenerik ipuçlarını yakalıyor.

Ülke tarafında da aynı boşluk vardı ve aynı desenle kapatıldı:
`country_from_text()` (`entity_extraction.py`) artık domain bulunamayan
gönderilerde ("Turkey GSM Database" gibi domainsiz başlıklarda) ülke
adı/demonim eşleşmesine düşüyor — mevcut veri setinde `country_code`
kapsamı 133 → 427 kayda çıktı. Dashboard arama kutusu da düzeltildi: "turkey"
yazmak artık doğrudan `cc=="TR"` eşleşen her şeyi getiriyor, sadece
başlıkta kelimenin geçtiği kayıtları değil — aynı desen artık sektör için
de geçerli (yeni "En çok hedeflenen sektörler" sıralaması + arama).

**Doğrulama:** `tests/test_ranking.py` (9 test), `tests/test_auto_mirror.py`
(7 test), `tests/test_sector_classification.py` (10 test), artı
`tests/test_entity_extraction.py`/`test_pipeline.py`/`test_dashboard.py`/
`test_notify_telegram.py`/`test_underground_forums.py`'a eklenen yeni
testler — toplam süit 171 testte yeşil.

## Local-only geliştirme ve sürekli toplama (2026-11-01)

`origin/main` 13 Ekim'de başka bir katkıcı tarafından tamamen farklı bir
mimariye ("forumwatch": Postgres + matcher + API + Docker servisi)
pivotlandı ve bu pivot sırasında `.github/workflows/pipeline.yml`'in
30-dakikalık zamanlayıcısı kaldırıldı — yani origin'deki hiçbir şey artık bu
dashboard/JSONL mimarisini çalıştırmıyor. Bilinçli karar: bu dal `main`'i
origin'e **push etmiyoruz / forumwatch'u merge etmiyoruz** — iki ayrı yön,
local'de bağımsız gelişmeye devam ediyor. Bu da şu sonucu doğurdu: veri
toplama artık **hiçbir yerde** otomatik çalışmıyordu — `data/incidents.jsonl`
6 hafta (8 Eylül → 20 Eylül'den sonra hiç) donmuş durumda bulundu, bu da
"Türkiye sonuçları neden bu kadar az" sorusunun gerçek sebeplerinden
biriydi (veri eski olduğu için değil — ama tazeleme de kapsamı artırmadı,
aşağıya bkz).

### Local sürekli çalıştırma — `scripts/run_cycle.ps1` + Windows Task Scheduler

GitHub Actions'ın yerini alan local eşdeğeri: `scripts/run_cycle.ps1` bir
`--once` döngüsü + `--build-dashboard`'u loglayarak çalıştırıyor
(`logs/cycle.log`, ~2MB'ı geçince son 5000 satıra kırpılıyor). Kayıtlı
görev:

```powershell
schtasks /create /tn "DarkWebForumTracker" /tr "powershell.exe -ExecutionPolicy Bypass -File \"C:\Users\Monster\Desktop\DarkWebFeed\Darkweb-forum-tracker\scripts\run_cycle.ps1\"" /sc minute /mo 30 /rl limited /f
```

Her 30 dakikada bir (GitHub Actions'ın zaten doğrulanmış kadansıyla aynı —
bkz. pipeline.yml'nin kendi gerekçesi), kullanıcı oturumu açıkken çalışır
(parola/SYSTEM yetkisi gerekmez). Kontrol: `schtasks /query /tn
"DarkWebForumTracker" /v`, elle tetikleme: `schtasks /run /tn
"DarkWebForumTracker"`, kaldırma: `schtasks /delete /tn
"DarkWebForumTracker" /f`.

**Neden `--loop` değil, tekrarlayan `--once`:** Tek bir uzun ömürlü süreç
yerine her 30 dakikada bağımsız, kendi kendini iyileştiren bir çalıştırma —
bir döngünün çökmesi (beklenmeyen exception, uyku moduna geçen makine) bir
sonraki zamanlanmış çalıştırmayı etkilemiyor; `--loop`'ta aynı süreç
sonsuza dek canlı kalmak zorunda.

**Mirror-adoption gecikme formülü (3. sorudaki "TLD değişince feed
gecikiyor" sorusunun yanıtı):** `health.DEAD_THRESHOLD = 3` ardışık
ulaşılamaz kontrol + adoption da aynı `run_full_cycle` içinde çalıştığı
için, bir forum öldükten sonra otomatik mirror geçişinin tetiklenmesi en
fazla `3 × 30dk = 90 dakika` sürer. Bu zaten bilinçli bir tasarım — `--health`
bayrağı ayrı ve daha sık çalıştırılsa bile adoption'ı HIZLANDIRMAZ, çünkü
adoption'ın ihtiyaç duyduğu SeenStore bağlamı sadece `run_full_cycle`'da var
(`cli.py`'nin `run_health_check()` docstring'i bunu zaten belirtiyor) — yani
daha sık health-check eklemek yanlış çözüm olurdu, gerçek kaldıraç kolun
30 dakikalık ana döngünün düzenli çalışmasını garantilemekti.

### Bulunan ve düzeltilen: sınırsız ekran görüntüsü fazı, zamanlanmış görevi tehdit ediyordu

`scripts/run_cycle.ps1`'i elle test ederken (gerçek, 6 haftalık boşluktan
sonraki ilk döngü) bir çalıştırma **25+ dakika** sürdü ve log'da uzun süre
hiç yeni satır görünmedi — dışarıdan "takıldı" ile "yavaş ama çalışıyor"
ayırt edilemiyordu (çünkü `_save_screenshot()` sadece HATA durumunda log
yazıyor, her başarılı adımda değil). Kök neden: `enrich_batch()`'teki
ekran görüntüsü fazı tek tek adımlarda sınırlı (nav 15sn, selector 8sn,
settle 3sn) ama **toplamda sınırsız** — yüzlerce yeni kayıt + birkaç yavaş
forum varsa, tüm döngü teorik olarak süresiz uzayabiliyordu. Bu artık her
30 dakikada bir zamanlanmış bir görev olarak çalışacağı için ciddi bir risk:
ekran görüntüsü fazı uzarsa, döngünün geri kalanı (emit, alarm, health-check,
mirror-adoption, dashboard yenileme — hepsi hızlı) hiç çalışamayabilirdi.

**Düzeltme:** `SCREENSHOT_BATCH_BUDGET_SECONDS = 600` (10 dakika) — tüm
ekran görüntüsü fazı için TOPLAM bir zaman bütçesi. Aşılırsa kalan kayıtlar
RSS-teaser `description`'larıyla ve ekran görüntüsüz bırakılıyor (Playwright
hiç kurulu değilmiş gibi, zaten var olan bir davranış), döngünün geri kalanı
her zaman çalışır. Doğrulama: `tests/test_underground_forums_enrich.py`'a
`time.monotonic()` mock'lanarak eklenen yeni test — toplam süit artık 172
testte yeşil.

### Asıl kırılma: `ransomware.live` entegrasyonu — `forumtracker/sources/ransomware_live.py`

"ziraat" aratınca hiç sonuç çıkmaması şikayeti üzerine deepdarkCTI'nin daha
önce hiç bakmadığımız `ransomware_gang.md` dosyasına bakıldı (forum.md
dışındaki kategoriler: telegram_threat_actors.md, telegram_infostealer.md,
markets.md, ransomware_gang.md, defacement.md, others.md — hepsi
`fastfire/deepdarkCTI` reposunda, `api.github.com/repos/fastfire/deepdarkCTI/contents/`
ile listelendi). Orada **ransomware.live** (api.ransomware.live/v2) bulundu:
bizim forum/Telegram taramamız gibi metinden kurban/ülke/sektör TAHMİN
etmek yerine, kendi CTI ekibi tarafından zaten çözülmüş gerçek kurban
adı/domaini/ülkesini/sektörünü veren, ücretsiz, kimliksiz bir API.
**Canlı doğrulama:** `GET /v2/countryvictims/TR` → **187 gerçek Türk
ransomware kurbanı** (vecert'in başta gösterdiği 161 rakamına çarpıcı
derecede yakın — muhtemelen onlar da kısmen böyle bir agregatörden
besleniyor, salt forum taramıyorlar).

**`RansomwareLiveSource`** (`GET /v2/recentvictims`, döngü başına bir kez):
`victim`/`post_title` → `Victim.name`, `country` → `Victim.country_code`
(DOĞRUDAN, bizim domain/metin tahminimize hiç gerek yok), `domain`/`website`
→ `Victim.website`, `activity` → `Victim.sector` (küçük, bilinçli bir
eşleme tablosuyla — `sector_classification.py`'nin kendi disiplini: sadece
belirsizliksiz eşleşenler, "Other" gibi değerler None kalıyor), `group`/
`group_name` → `ThreatActor.name`, `claim_url`/`post_url` → `evidence.post_url`.
`category` her zaman `"ransomware_leak"`. **Önemli düzeltme:**
`pipeline._apply_ransomware_intel()` artık `victim.country_code`/`sector`
**zaten doluysa onu EZMIYOR** — önceden her incident için koşulsuz
yeniden hesaplayıp üzerine yazıyordu, bu da ransomware.live'ın zaten doğru
verdiği değeri bizim daha zayıf domain/metin tahminimizle bozabilirdi.

**ToS notu:** Ücretsiz v2 API "Personal use only... not intended for
corporate or business use" diyor, ~1 istek/dakika/endpoint sınırı var.
Bu kaynak döngü başına sadece BİR endpoint'i BİR kez çağırıyor (30 dk'lık
zamanlanmış döngümüzle kıyasla sınıra yaklaşmıyor bile) — ama "personal
use" şartı teknik değil, iş modeli sorusu; bu tracker'ın çıktısı ticari
kullanılırsa `api-pro.ransomware.live` (ücretli, sınırsız) seçeneğine
geçilmeli.

**Doğrulama:** `tests/test_ransomware_live.py` (9 test, hem
`/recentvictims` hem `/countryvictims/<cc>`'nin farklı alan adlarını
kapsıyor), canlı uçtan uca test (`RansomwareLiveSource().fetch()` gerçek
API'ye karşı çalıştırıldı — 100 kayıt, ilk kurban gerçekten TR/Evinoks).

**Araştırmada bulunan ama şimdilik eklenmeyen ikinci bir aday:**
`api.ransomfeed.it` — aynı tarzda, canlı doğrulanmış (~90 gerçek kayıt,
`country`/`work_sector` alanları var) ama veri kalitesi tutarsız
(bazı `description` alanları "[AI generated] N/A" placeholder) — ayrı bir
turda değerlendirilebilir.

### İkinci kırılma: Telegram köprüsü sadece BİR botun formatını anlıyordu

`telegram_bridge.py`'nin asıl kısıtı forum sayısı değil, **ayrıştırıcının
kendisiydi**: `_parse_item`, sadece breachdetect botunun "Source"/"Content"/
"Type" yapılandırılmış kart formatını tanıyordu — gerçek bir insanın normal
attığı herhangi bir Telegram gönderisi (düz metin/fotoğraf/video) bu formata
uymadığı için **sessizce atılıyordu**. Canlı doğrulandı: gerçek, aktif, CTI
açısından alakalı bir kanal (`SliceForLifeee` — CVE'ler, Europol operasyonları,
ransomware/leak gönderileri) bu yüzden **0 incident** üretiyordu, kanal
ölü/erişilemez olduğu için değil, ayrıştırıcı onu hiç tanımadığı için.

**Düzeltme:** `_parse_generic_item()` — yapılandırılmış format eşleşmezse
devreye giren bir fallback. Başlık/açıklamadan `content` çıkarıyor (medya-only
başlıkları — "[Photo]", "[Video]" vb. — atıyor), `author`/`type`/`source_url`
None bırakıyor (`_to_incident` zaten bunları opsiyonel olarak ele alıyordu).
Ayrıca double-HTML-escape sırası düzeltildi (önce unescape, sonra tag strip —
tersi, kaçırılmış `<a href=...>` metnini olduğu gibi içerikte bırakıyordu) ve
standart RSS `pubDate` formatı için `parsedate_to_datetime` eklendi.

`SliceForLifeee` artık `TELEGRAM_BRIDGE_POOL`'da (içerik incelemesi yapıldı,
canlı doğrulandı). `ctifeeds`/`DarkWebInformer` denendi, köprünün
kanal-başına soğuma süresine takıldı — ayrı bir turda tekrar denenmeli.

**Doğrulama:** `tests/test_telegram_bridge.py`'a 8 yeni test (toplam 18),
tüm süit 189 testte yeşil.

### Üçüncü kırılma: "Leads Market" — zaten sahip olduğumuz forumların kullanmadığımız alt-forumu

Ziraat Bank örneği klasik bir "hacklenmiş veritabanı" ilanı değildi —
"LEAD INFORMATION / Category: Bank Promotion Leads / Market: Banking-Finance"
formatında, yani bir **pazarlama-lead satıcısı** ilanı, farklı bir tür. Bunun
üzerine (deepdarkCTI'ye hiç dönmeden) doğrudan web'de "lead marketplace
forum" araması yaptım — ve `bf.st` (BreachForums) ile `darkforums.as`
üzerinde **"Leads Market" diye özel bir alt-forum olduğunu** buldum. Her
ikisi de **zaten FORUM_POOL'umuzda** — sorun yeni bir forum bulmak değildi,
sorun şu anki genel feed'in (`syndication.php`, sitedeki TÜM alt-forumların
en son ~30 gönderisi, `MAX_ITEMS_PER_SITE`) bu düşük-hacimli alt-forumu
boğuyor olmasıydı.

**Canlı doğrulandı:** Her iki forum da MyBB altyapısı kullanıyor ve
`syndication.php?fid=N` ile alt-foruma özel feed veriyor (`bf.st` için
fid=42, `darkforums.as` için fid=83) — ikisinde de **aynı "Turkey Betting
Leads" gönderisi** çıktı (çapraz doğrulama). Gerçek içerik: "21Bitcoin
Crypto Leads", "700k Coinbase Crypto Leads", "Turkey Betting Leads",
"China Communist Party Members Leads" — tam olarak aradığımız tür.

Pool'daki diğer MyBB-tabanlı forumlarda (`bhcforums`, `leakforum.io`,
`sinister.ly`) aynı `/Forum-Leads-Market` yolu denendi — yok, bu ikisine
özgü.

**Eklenen:** `breachforums-leads`, `darkforums-leads` (`underground_forums.py`
FORUM_POOL'a yeni girdi olarak — yeni bir forum değil, var olan bir
forumun ayrı bir feed'i). Canlı test: `UndergroundForumSource` ile
30 gerçek "lead" ilanı çekti.

### Araştırılan ama bilinçli olarak YAPILMAYAN: Cloudflare/CAPTCHA'lı forumlar

Kullanıcı `pwnforums.st`'yi örnek verdi (vecert/osint tarzı "Country/
Category/Market" alanlı yapılandırılmış ilan formatı). Doğrulandı: site
gerçek, ama hem düz `requests` hem gerçek bir Playwright/Chromium
tarayıcısı (6sn bekleme dahil) "Just a moment..." (Cloudflare
interaktif doğrulama) ekranından öteye geçemiyor. Araştırma (Cybersixgill,
Flare gibi vendörlerin kendi metodoloji sayfaları) gösteriyor ki ciddi
oyuncular bile bunu scraping hilesiyle değil **HUMINT** ile çözüyor (gerçek
bir hesapla foruma üye olup itibar kazanan bir insan analist) — yani
otomatik bir "çözüm" aramak zaten yanlış hedef. Kapsam dışı bırakıldı;
tek gerçekçi yol kullanıcının kendi hesap/cookie'siyle meşru bir oturum
vermesi olurdu (ayrı, insan-bakımı gerektiren bir karar, şimdilik
uygulanmadı).

## DarkWebFeed ile ilişki

Bu modül DarkWebFeed'in genel ingestion pipeline'ından bilerek ayrıldı: kendi
release döngüsü, kendi bağımlılıkları (`requests`, `playwright`,
`beautifulsoup4`) ve kendi dedup durumuyla bağımsız çalışır. Aynı `Incident`
zarf şeklini kullanır, bu yüzden çıktısı istenirse ürün tarafında
DarkWebFeed'in diğer kaynaklarıyla aynı stream'de birleştirilebilir.
