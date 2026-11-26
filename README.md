# Дуу Салгагч

Монгол хэлтэй веб болон PWA. Demucs дууны хоолой, хөгжмийг салгана. Хоёр хувилбарын эхний 30 секундийг үнэгүй сонсоно. Wire-ээр 5,000₮ төлсний дараа хоёр бүтэн MP3 (320 kbps) татна. YouTube холбоосыг шууд оруулах эсвэл аудио файл сонгоно. Сервер YouTube аудиог yt-dlp-ээр авч, Demucs-д WAV болгон дамжуулна. Ингэснээр эцсийн MP3 үүсгэхээс өмнө чанар алдагдуулах дахин шахалт хийхгүй. Нийтэд нээлттэй, боловсруулах эрхтэй, 10 минут хүртэлх бичлэг ашиглана.

## Локал ажиллуулах

```sh
docker build -t duu-salgagch .
docker run --rm -p 8080:8080 -v duu-jobs:/data duu-salgagch
```

http://localhost:8080 хаягаар нээнэ. YouTube импортод серверийн интернэт холболт хэрэгтэй. YouTube тухайн серверээс аудио авах хүсэлтийг хязгаарлавал аудио файл оруулах сонголтыг ашиглаж болно. Файл салгах, сонсох, PWA-ийн бүртгэлийг туршиж болно. Wire төлбөр зориулалтын HTTPS хаяг, Wire API түлхүүртэй үед идэвхжинэ.

## Нийтийн хост

Docker болон Compose ажилладаг, нүүр хаягаа HTTPS-ээр гаргах боломжтой VM ашиглана. Эхний хэмжилтэд 2 CPU, 4 ГБ RAM төлөвлөнө. Oracle Always Free VM-ийн боломж тухайн бүсийн багтаамж, дансны квотоос хамаарна. Серверт 80, 443 порт нээгээд PUBLIC_HOST хаягийг VM-ийн нийтийн IP рүү заана.

```sh
git clone https://github.com/bumerdene072-cyber/panamn.git
cd panamn
cp .env.example .env
chmod 600 .env
```

Сервер дээрх `.env` файлд `PUBLIC_HOST`-ыг жинхэнэ домэйнээр солино. `WIRE_API_KEY`-д Wire Dashboard-ийн тухайн төслийн түлхүүрийг оруулна. Эхлээд Wire test түлхүүртэй туршиж, дараа нь идэвхтэй live оператортой төслийн түлхүүрээр солино.

```sh
docker compose up -d --build
```

Дуу бүрийн захиалгад зориулж Wire hosted checkout үүсгэнэ. Төлбөрийн хуудас QR болон банкны аппын холбоосыг харуулна. Буцаж ирэхэд болон «Төлбөр шалгах» товч дарахад сервер Wire API-аас захиалгын төлбөрийг шалгана. Сервер PaymentIntent, захиалгын ID, дүн, валют, төлөвийг тулгаж бүтэн файлыг нээнэ. Wire Dashboard-д `https://<public-host>/api/wire/webhook` хаягаар `payment_intent.succeeded` үйл явдлыг бүртгэнэ. Webhook-ийн нууц түлхүүрийг `WIRE_WEBHOOK_SECRET`-д хадгална. Сервер гарын үсгийг шалгаад Wire API-аас тухайн захиалгын төлбөрийг баталгаажуулна.

## Хадгалалт ба PWA

- 50 МБ, 10 минут хүртэлх файлыг хүлээн авна. Нэг instance, нэг Gunicorn worker, нэг зэрэг боловсруулах ажилтай.
- Төлбөр эхлээгүй файлыг 24 цаг, төлбөр эхэлсэн файлыг долоо хоног, баталгаажсан файлыг баталгаажуулснаас долоо хоног хадгална. Хугацаа дууссан бичлэгийг шинэ файл хүлээн авахдаа цэвэрлэнэ.
- `jobs` volume-д MP3 болон захиалгын төлөв хадгалагдана. Шинэчлэл хийхдээ volume-ийг хэвээр ашиглана. Сэргээх шаардлагатай үед volume-ээ нөөцөлнө.
- Үр дүнгийн хуудасны холбоос нь файлыг дахин нээх түлхүүр тул хувийн холбоосоор хадгална.
- Chrome, Edge дээр апп суулгах боломжтой үед «Апп суулгах» товч гарна. iPhone дээр Safari-ийн «Add to Home Screen» сонголтоор нэмнэ. Аудио боловсруулах, сонсох, татахад серверийн холболт шаардлагатай.

## YouTube ID-д эх файл холбох

`PANAMN_SOURCE_DIR`-д PANAMN процесс унших эрхтэй тусдаа хавтас заана. Боловсруулж, хэрэглэгчдэд MP3-аар түгээх зөвшөөрөлтэй эх файлыг тухайн бичлэгийн 11 тэмдэгт ID-аар нэрлэнэ. Жишээ нь `BaW_jenozKc.mp3`. Нэг ID-д нэг эх файл байршуулна. Хавтас дотор түр нэрээр хуулж дуусаад эцсийн нэр рүү шилжүүлнэ.

Бүртгэлтэй эх файл байвал PANAMN YouTube-ээс дахин татахгүй. YouTube линк оруулсан хэрэглэгч уг файлыг үнэгүй MP3 болгон авах эсвэл хоолой, аяыг салгах боломжтой. Эх файлыг 50 МБ, 10 минутын хязгаарт багтаана.

### Optional Apify fallback

Set `APIFY_API_TOKEN` in the app service's environment to enable the
API Ninja YouTube Video Downloader fallback on YouTube bot checks or HTTP 403.
Keep the token server-side. Docker Compose passes it through from the deployment
environment. The Actor receives one canonical YouTube URL per run, with a
60-second run timeout and a requested cap of $0.15 per run. Monitor the account's
usage separately. Successful imports use the existing audio cache.
