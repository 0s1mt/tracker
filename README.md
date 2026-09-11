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
Telegram'da "darkforums-official.cc" belirmesi gibi). **Otomatik hiçbir şeyi
`FORUM_POOL`/`BROWSER_FORUM_POOL`'a eklemiyor** — `discover_candidates()`
için zaten var olan "önce canlı içerik incelemesi" disiplini burada da
geçerli, bu sadece bir öneri listesi.

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

## DarkWebFeed ile ilişki

Bu modül DarkWebFeed'in genel ingestion pipeline'ından bilerek ayrıldı: kendi
release döngüsü, kendi bağımlılıkları (`requests`, `playwright`,
`beautifulsoup4`) ve kendi dedup durumuyla bağımsız çalışır. Aynı `Incident`
zarf şeklini kullanır, bu yüzden çıktısı istenirse ürün tarafında
DarkWebFeed'in diğer kaynaklarıyla aynı stream'de birleştirilebilir.
