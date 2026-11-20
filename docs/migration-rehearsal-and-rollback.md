# Migration oncesi hedef, yedek ve geri donus plani

Bu belge uygulama talimatidir; canli migration yetkisi vermez. Normal Render Start Command degismez. R2 kontrol duzeltmesi tek basina erisim kaniti degildir.

## Kesin R2 hata nedeni

boto3 S3 istemcisi context manager degildir. `with boto3.client(...)` HeadBucket ag isteginden once TypeError uretir. Onceki MagicMock sahte __enter__ sagladigi icin hatayi gizliyordu. Kod contextlib.closing ile istemciyi kapatir. Gercek SDK nesnesi ve botocore Stubber ile basari/403 yollari test edilir; bu testlerde hicbir R2 ag erisimi yapilmaz. ACCESSIBLE sadece HeadBucket hatasiz donerse uretilir.

## Ayrik PostgreSQL provasi

Docker calisirken depo kokunden:

```text
python backend/scripts/rehearse_migrations.py
```

Komut kendi postgres:17 konteynerini, rastgele loopback portunu ve tmpfs veri dizinini olusturur. DATABASE_URL kabul etmez; .env okumadan Alembic ayar fabrikasini sadece uretilen yerel URL ile degistirir. Canli ayarlara baglanmaz. Baslangicin bos oldugunu, her revision'in uygulanmasini, 16 tabloyu, sutunlari/nullability/primary key alanlarini ve head tekrarinin no-op olmasini kontrol eder. Kendi konteynerini finally blogunda kaldirir. Yerel trust auth yalnizca bu gecici konteyner icindir; uretimde kullanilmaz. Canli PostgreSQL major surumu farkliysa o surumle de prova gerekir.

## Dogru hedefi salt okunur dogrulama

1. Render Dashboard'da beklenen proje/ortam ve PostgreSQL kaynagini secin. Kaynak ID'sini, bolgeyi ve uygulama servisiyle iliskisini kaydedin; adresleri/anahtarlari rapora koymayin.
2. Web servisinin DATABASE_URL degerini Dashboard icinde bu kaynagin Internal Database URL degeriyle gizlice karsilastirin. Degerleri sohbet/log/screenshot'a koymayin. Yanlis projeye ait bos DB de ayni bosluk sonucunu verir.
3. Kaynagin External Database URL bilgisiyle guvenilir yerel istemciden, salt okunur transaction kullanarak baglanin. Kimlik bilgilerini URL komut argumanina koymayin: korumali pg_service.conf ve pgpass kullanin. Render ag izinleri gerekiyorsa yalnizca kendi IP'nizi izinli yapin.
4. `BEGIN READ ONLY; SELECT current_database(), current_user, current_setting('server_version_num'), pg_is_in_recovery(); SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'; ROLLBACK;` sorgularini yerelde inceleyin. Ham veritabani/kullanici adlarini paylasmayin. Envanterdeki tablolar ve alembic_version yokluguyla karsilastirin.
5. Ayni web servisi deploy'u uzerinden /system kontrolunu tekrar calistirin. Baglanti CONNECTED, envanter COMPLETE, NO_USER_RELATIONS ve alembic_version ABSENT beklenir. Replika false olmali. Bu nesne yoklugu tam DB boslugu garantisi degildir; uzantilar/fonksiyonlar/large object'ler kapsamin disindadir.
6. Ancak 1-4 bagimsiz eslestirmesi tamamlaninca gozlenen parmak izini SYSTEM_EXPECTED_DATABASE_FINGERPRINT olarak web servisine tanimlayin ve MATCH gorun. Ilk gorulen parmak izini korlemesine sabitlemeyin. Internal ve external adreslerden uretilen parmak izleri farkli olabilir; bu yuzden dis istemcinin hash'ini kopyalamayin. Token rotasyonu da hash'i degistirir.

## Canli migration oncesi yedek

Once yazan worker/cron/islemleri durdurun. Sunucu major surumu ile uyumlu pg_dump/pg_restore kullanin. PGSERVICEFILE/PGPASSFILE dosyalarini kisitli izinlerle hazirlayin; sifreleri komut satirina veya Git'e koymayin. Asagidakiler planlanan komutlardir, bu gorevde canli ortamda calistirilmadi:

```text
pg_dump --dbname=service=verified_source --format=custom --file=before-migration.dump
pg_restore --list before-migration.dump
```

Cikis kodunu kontrol edin, dosyanin SHA256 ozetini ve UTC zamanini kaydedin. Sifrelenmis ve erisimi kisitli alanda saklayin; Git'e yuklemeyin. pg_dump tek veritabanini yedekler; roller/tablespace gibi cluster nesnelerini kapsamaz. Render yonetilen yetkilerini, uzantilari ve sahiplik gereksinimlerini ayrica kaydedin. Bos DB icin de yedek alin. Salt okunur yedek alma islemi bile bu gorevde canlida yapilmadi.

Yedegi ayri, yeni ve bos bir test veritabanina geri yukleyip dogrulamadan canli migration'a gecmeyin:

```text
pg_restore --dbname=service=isolated_restore --exit-on-error --single-transaction --no-owner --no-privileges before-migration.dump
```

isolated_restore kaynak DB olmamali; farkli host/DB kimligi bagimsiz dogrulanmali. --no-owner/--no-privileges kullaniminda gerekli sahiplik ve grant'ler ayrica plana alinmali. Listeleme tek basina yedegin geri yuklenebilir oldugunu kanitlamaz.

## Geri donus

Migration hatasinda yazarlari kapali tutun. Transaction rollback sonucunu, revision'i ve envanteri salt okunur kontrol edin; otomatik retry/stamp/downgrade yapmayin. Commit edilmis degisikliklerde veya belirsiz durumda mevcut DB'yi koruyun; yedegi yeni ayri DB'ye yukleyin, dogrulayin ve ancak ayri onayla uygulama baglantisini o DB'ye gecirin. Canli DB uzerine --clean/restore, tablo silme veya downgrade uygulanmaz. Eski uygulama commit'ine donus tek basina DB geri donusu degildir. Basarisiz DB inceleme icin korunur. Yazarlar acilmadan once /system ve uygulama kontrolleri yeniden yapilir.

Kaynaklar: https://www.postgresql.org/docs/17/app-pgdump.html ve https://www.postgresql.org/docs/17/app-pgrestore.html

## Bu oturumda gercekten dogrulanan sonuc (2026-11-20)

- Gercek boto3 1.43.125 istemcisinde __enter__ bulunmadigi ve eski with kullaniminin TypeError urettigi yerelde yeniden uretildi.
- Gercek SDK istemcisi + Stubber ile HeadBucket basari ve AccessDenied davranislari dogrulandi; R2'ye ag istegi yapilmadi.
- Gecici PostgreSQL 17.12 (Debian 17.12-1.pgdg13+1) uzerinde 0001 -> 0009 adim adim uygulandi; her adimin revision'i kontrol edildi.
- 16 tablo, sutunlar, nullable/primary key alanlari dogrulandi. Alembic compare_metadata varsayilan karsilastirmasinda model-schema farki cikmadi (tam veri/trigger/check/default dogrulamasi iddiasi degildir).
- upgrade head tekrari basarili no-op. Gecici konteyner kaldirildi.
- Canli Render hedef eslestirmesi, R2 erisimi, canli yedek alma ve yedegin geri yuklenmesi bu oturumda yapilmadi. Bunlar sonraki canli onkosullardir.
