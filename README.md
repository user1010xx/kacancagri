# Kaçan Çağrı Botu

Invekto PBX üzerinden günlük kaçan çağrıları (missed calls) tespit edip yetkili Telegram grubuna anlık bildirim gönderen basit ve etkili bir bot.

## Özellikler

- Invekto PBX API ile entegrasyon (reportType 2/4)
- Belirli departman/kuyruk filtreleme
- Tekrar gönderimi önleyen kalıcı deduplication (45 güne kadar eski kayıtlar otomatik temizlenir)
- `/kacancagri` ile tarih aralığı zengin Excel raporu
- Personel yönetimi ve özel mesaj (DM) bildirimi (`telegram_chat_id` ile)
- `/stats`, `/kuyruklar`, `/ayar`, `/temizle` gibi yönetim komutları
- Railway (veya benzer) için kolay deploy

## Gereksinimler

- Python 3.10+
- Telegram Bot Token + Grup Chat ID
- 8 haneli Invekto Firma Kodu
- (Opsiyonel) Invekto tarafında istek IP'si whitelist

## ⚠️ Güvenlik (Çok Önemli)

- **Asla** gerçek `TELEGRAM_BOT_TOKEN` değerini `.env.example` veya herhangi bir commit'e koyma.
- `.env` dosyası `.gitignore` tarafından otomatik olarak yoksayılır.
- Bot token'ını **sadece**:
  - Yerel geliştirme için `.env` dosyasında, veya
  - Railway (veya benzer platform) Environment Variables bölümünde sakla.
- Gerçek token'ı içeren `.env` dosyası **paylaşılmamalı**, yedeklenmemeli veya public repo'ya atılmamalıdır.
- Token sızarsa hemen @BotFather üzerinden **revoke** edip yeni token üret.

**Önerilen:**
- `.env` dosyasını her zaman temiz tut.
- Gerçek değerleri asla kod içine hardcode etme.

## Kurulum (Yerel)

1. Repoyu klonla
2. `.env` oluştur (`.env.example` örneğini kullan)
3. Bağımlılıkları kur:

```bash
pip install -r requirements.txt
```

4. Botu çalıştır:

```bash
python bot.py
```

## Ortam Değişkenleri (.env)

```env
TELEGRAM_BOT_TOKEN=123456:ABC-xyz
TELEGRAM_GROUP_CHAT_ID=-1001234567890
INVEKTO_DEPARTMENT_NAME=Gelen Arama
POLLING_INTERVAL_SECONDS=60
NOTIFY_UNCOMPLETED_ONLY=true
INVEKTO_DEPARTMENT_LOOSE_MATCH=false
# Railway volume için:
# DATA_DIR=/app/data
```

`TELEGRAM_GROUP_CHAT_ID` değerini öğrenmek için gruba botu ekledikten sonra `/chatid` yazın.

## Komutlar (Sadece yetkili grupta çalışır)

| Komut | Açıklama |
|-------|----------|
| `/start` `/help` | Yardım mesajı |
| `/ping` | Bağlantı ve yetki testi |
| `/chatid` | Grup ID göster |
| `/ayar` | Mevcut bot ayarlarını göster |
| `/firmakodu 12345678` | Firma kodunu ayarla (8 hane) |
| `/stats` | Dedup kaydı sayısı, son poll bilgisi |
| `/kuyruklar` | Invekto'daki kuyruk/departman adlarını listele |
| `/kacancagri 15.06.2026, 25.06.2026` | Tarih aralığı için Excel raporu (tüm kaçan çağrılar) |
| `/iletilenkacancagri 28.06.2026` | İletilen çağrı raporu (sadece bugün veya dün) |
| `/personelekle 105 Ahmet @ahmet` | Personel ekle/güncelle |
| `/personelsil 105` | Personel sil |
| `/personeller` | Kayıtlı personelleri listele |
| `/temizle` | Eski dedup kayıtlarını temizle |
| Excel (.xlsx) yükle | 3 sütun: dahili, ad, @username |

**Özel mesaj (DM) için:** Personel, bota özel sohbetten `/start` yazmalıdır. Böylece `telegram_chat_id` kaydedilir ve kaçan çağrı bildirimi DM ile iletilir.

**İletilen rapor kuralı:** `/iletilenkacancagri` komutu sadece bugün ve bir önceki gün için çalışır. Bugün tarihi seçilirse rapor, komut anına kadar iletilen kayıtları verir.

**Not:** `NOTIFY_UNCOMPLETED_ONLY=true` iken anlık bildirimler sadece tamamlanmamış çağrıları kapsar; `/kacancagri` Excel raporu her zaman tüm kaçan çağrıları listeler.

## Excel Raporu

`/kacancagri` komutu şu sütunları içerir:
- ID, Telefon, Tarih, Saat, Departman/Kuyruk
- Durum, Tamamlandı, Çağrı/Ring/Bekleme süreleri, Trunk, Extension

## Deploy: Railway

### 1. Temel Deploy

- GitHub'a bağla veya CLI ile deploy et.
- Environment Variables'ı ayarla (yukarıdaki gibi).
- `railway.toml` zaten mevcuttur.

### 2. Railway Volume Kurulumu (Kalıcı Veri) — ÖNERİLEN

`sent_calls.json` ve `config.json` **kaybolmasın** diye (Railway container'ları varsayılan olarak ephemeral'dır):

1. Railway Dashboard'da ilgili **service**'i seç.
2. Sağ tarafta veya Command Palette (`Cmd+K` / `Ctrl+K`) ile **"Add Volume"**'a tıkla.
3. **Mount Path** olarak **tam olarak** şunu yaz:
   ```
   /app/data
   ```
   > Not: Railway, uygulamayı `/app` klasörü içine koyar. Kodumuz `./data` kullandığı için mount path `/app/data` olmalıdır.

4. (Opsiyonel ama önerilen) Environment Variables'a şunu ekle:
   ```
   DATA_DIR=/app/data
   ```

5. Değişiklikleri commit'le ve **Redeploy** yap.

**Sonuç:**
- `data/sent_calls.json`
- `data/config.json`
- `data/logs/`

gibi dosyalar artık kalıcı olarak saklanır.

> **Not:** `railway.toml` dosyası içinde sadece kısa bir hatırlatma bırakıldı. Detaylı talimatlar burada.

### 3. VS Code'ta railway.toml "23 problem" uyarısı

VS Code'ta (özellikle **Even Better TOML** eklentisiyle) 20+ sarı/kırmızı problem görebilirsin.

**Sebep:**
- VS Code, `railway.toml` dosyasını Railway'in resmi JSON şemasına (`https://railway.com/railway.schema.json`) göre doğrulamaya çalışıyor.
- Uzun açıklama yorumları (özellikle Türkçe karakterler içeren) şema doğrulayıcıyı "hata" olarak işaretliyor.
- Bu **gerçek bir syntax hatası değil**. Railway bu yorumları tamamen görmezden gelir.

**Çözümler (en kolayı):**
1. `railway.toml` dosyasını **mümkün olduğunca temiz** tuttuk (şu anki hali).
2. Hâlâ çok problem görüyorsan:
   - VS Code ayarlarından `toml.schema.enableValidation` değerini `false` yap, veya
   - "Even Better TOML" eklentisinin validation'ını kapat.
3. railway.toml dosyasının en üstüne `$schema` yorumu ekledik.

Bu uyarıların hiçbiri **çalışmayı etkilemez**. Güvenle kullanabilirsin.

## Mimari Notlar

- Polling ile çalışır (JobQueue).
- Her poll sadece **bugünün** verisini çeker.
- Dedup anahtarı: `ID|Phone|dd.mm.yyyy|saat|Departman`
- Başlangıçta bugünün çağrıları seed edilir (restart'ta tekrar bildirim önlenir).
- Görüşme geçmişi (15 gün) poll başına tek API çağrısıyla cache'lenir.
- DM başarısız olursa kayıt tamamlanmaz; sonraki poll'da özel mesaj yeniden denenir.

## Geliştirme

Testleri çalıştır:

```bash
pytest -q
```

## Sorun Giderme

- Bildirim gelmiyor → `/ping` ve `/ayar` ile kontrol et. Firma kodu ve departman doğru mu?
- API hatası → Invekto'da IP whitelist kontrol et.
- Veri siliniyor → Volume'un `/app/data` olarak mount edildiğinden emin ol.

## Lisans

İç kullanım için.
