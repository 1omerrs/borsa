# BorsaNabız

Hisse, altın ve döviz fiyatlarını toplayan, günlük fark ile yüzdeyi hesaplayan ve herkese açık bir panoda gösteren uygulama. Kayıt SQL'dedir. Koşul oluşunca e-posta gider, günlük ve haftalık rapor üretilir.

n8n akışını bu proje kurmaz. Google Sheets ve kendi otomasyonun için Bağlantı sayfasındaki uçları kullan.

## Çalıştırma

```bat
baslat.bat
```

Ya da:

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Site: http://localhost:8000

Aynı ağdaki cihazlar bilgisayarın IP adresiyle açabilir. Uzaktan kural ve e-posta ayarı için `.env` içine `ADMIN_TOKEN` yaz, tarayıcıda Bağlantı sayfasına anahtarı gir. İzleme herkese açıktır.

## Senin kuracağın n8n

1. Zamanlayıcı + HTTP Request `GET /api/n8n/snapshot` + Google Sheets satır ekle.
2. İsteğe bağlı IF düğümü ile `change_percent` eşiği ve e-posta.
3. Webhook düğümünün üretim adresini sitedeki n8n alanına kaydet. Koşul oluşunca uygulama bu adrese JSON yollar.

Ayrıntı ve örnek gövde sitede, Bağlantı bölümünde. API şeması: http://localhost:8000/docs

## E-posta

Bağlantı sayfasına SMTP sunucusu, port, kullanıcı, parola ve alıcı adresi yaz. Parola yerel SQLite dosyasında durur. 465 portu SSL, diğer portlar STARTTLS kullanır.

Günlük rapor e-postası 18:00'dan sonra, günde bir kez gider. Bunu aynı sayfadan açabilirsin.

## Not

Veri Yahoo Finance, gerekirse Stooq ve kur için Frankfurter üzerinden gelir. Gram altın, ons fiyatının 31,1035'e bölünüp dolar/TL ile çarpılmasıdır; kuyumcu satış fiyatı değildir. Yatırım tavsiyesi değildir.
