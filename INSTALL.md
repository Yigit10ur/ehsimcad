# Installing EhsimCAD v1 on your own server

For the team doing the install. It assumes no knowledge of the application, and
it assumes nobody from the project is in the room -- so everything needed to
finish, and to work out what went wrong, is in this file.

Everything is configuration: the same build runs in every environment, and no
secret is baked into it.

**Nothing has to exist before you start.** `compose.yaml` brings its own
Postgres and its own S3-compatible store, so the four steps below work on a
bare machine with Docker and no accounts anywhere. If the company already runs
a database and a bucket, [use those instead](#using-a-database-and-bucket-you-already-run)
-- it is four lines of configuration and one different command.

**Türkçe özet en altta.**

---

## Start here

Four steps, in this order. Each one is explained further down.

```
cp .env.deploy.example .env.deploy    # then set AUTH_SECRET in it
docker compose run --rm preflight     # must say Ready
docker compose run --rm migrate       # creates the tables
docker compose up -d
```

`AUTH_SECRET` is the only value with no sensible default —
`openssl rand -base64 32` produces one. Everything else in the file already
matches the bundled Postgres and MinIO.

If `run` reports that the service does not exist, the Compose version is old
enough to need the profile named: `docker compose --profile tools run --rm
preflight`. The two tools are in a profile so that `up` does not start them as
services.

**If the server cannot reach the internet**, one step comes before those four:
loading the images, which were built elsewhere. Everything after it is
identical. See [If the server has no internet](#if-the-server-has-no-internet).

Three rules. They are the whole of what installs get wrong:

**1. Do not start anything until `preflight` says Ready.** It is the closest
thing to someone to ask. It connects to the real database and writes a real
object to the real bucket, then names what is wrong in a sentence you can act
on -- which key is rejected, which bucket is missing, which address does not
resolve. A failure here is a five-minute fix; the same failure discovered after
the service is up looks like the application is broken.

**2. The storage address has to work from users' desktops, not just from the
server.** Browsers upload files straight to object storage, never through the
application, so `STORAGE_PUBLIC_ENDPOINT` is the address *they* must be able to
open. It ships as `http://localhost:9000`, which is correct only while you are
at the machine running Docker — from any other desk it has to name the server.
See
[Read this before you configure anything](#read-this-before-you-configure-anything).

**3. The bucket must allow cross-origin requests from the site's address.**
Same reason. The browser sends `OPTIONS` before it sends the file. Allow the
origin, the methods `GET`, `PUT`, `HEAD`, and the `content-type` header. The
bundled MinIO already does; a bucket of your own has to be told.

If something goes wrong later, [When something is wrong](#when-something-is-wrong)
lists what each symptom actually means.

---

## What it is

Two long-running processes, and the two stores they share.

| | What it does | Reached by |
|---|---|---|
| **web** | Serves the site and the API. Node, listens on 3000. | People, through your reverse proxy |
| **worker** | Converts uploaded CAD files. Python + OpenCascade. | Nothing — it connects out |
| **postgres** | The catalogue and the job queue. | web and worker only; no port is published |
| **minio** | The files themselves, over the S3 API. Listens on 9000. | web, worker, **and every user's browser** |

`web` and `worker` never speak to each other directly. An upload becomes a row
in a queue table; the worker picks it up. Stopping the worker does not stop the
site — uploads simply wait.

`postgres` and `minio` are ordinary, replaceable pieces: both speak standard
protocols, and swapping either for one the company already runs is a change to
`.env.deploy`, not to any code. They are bundled because an install that needs
nothing else is an install that can be done in one sitting on a closed
network.

---

## What you need to provide

**Docker, and a host with 2–3 GB of RAM.** That is the whole list. Postgres and
the object store come with it.

**Disk.** A model costs the uploaded file plus about 4–6 MB derived from it,
kept per revision, and nothing is removed unless somebody deletes a model. Both
stores live in named Docker volumes, so `docker compose down` leaves them alone
and only `docker compose down -v` destroys them.

**A reverse proxy** for TLS, in front of port 3000. It does not terminate TLS
itself. It does not need a large body limit — see
[Read this before you configure anything](#read-this-before-you-configure-anything)
for why.

**2 GB of RAM** is comfortable for both application processes together.
Measured: the worker peaked at 435 MB converting a 500-part assembly and
344 MB converting a three-part one. Almost all of that is OpenCascade itself
being loaded — the part that grows with the model is smaller than the fixed
cost, so a bigger assembly does not need a bigger machine nearly as fast as you
would expect. Conversion is single-threaded and takes seconds; the same
500-part assembly took 5.5 s.

---

## Read this before you configure anything

**Browsers upload directly to object storage, not through the application.**
The web application signs a URL and hands it to the browser, which then PUTs
the file straight to the bucket. A 200 MB assembly never passes through Node.

Two consequences, and they are the two things installs get wrong:

1. **`STORAGE_PUBLIC_ENDPOINT` must be an address your users' browsers can
   open.** There are two settings for one store, and this is why:

   | | Used by | Bundled value |
   |---|---|---|
   | `STORAGE_ENDPOINT` | web and worker, from inside the container network | `http://minio:9000` |
   | `STORAGE_PUBLIC_ENDPOINT` | the browser, from somebody's desk | `http://localhost:9000` |

   `minio` is a name that resolves on the container network and nowhere else,
   so it cannot be the one a browser is given. The address is part of what gets
   signed, so this cannot be repaired by rewriting the URL afterwards — the
   signature would stop matching. It has to be signed for the address the
   browser will really use.

   **The shipped `localhost:9000` is right only while you are sitting at the
   machine running Docker.** For anyone else, set it to the server's own name
   or address — `http://cad.internal.example:9000`, or whatever your proxy
   puts in front of port 9000. Leave `STORAGE_PUBLIC_ENDPOINT` empty when one
   address genuinely works everywhere, which is the ordinary case for a hosted
   bucket.

   `preflight` fails outright if browsers would be handed a container-only
   name, and warns if they would be handed `localhost` while the site is
   served from somewhere else. It cannot do better than that: it is not on
   anybody's desktop.

2. **The bucket must allow cross-origin requests from `SITE_URL`.** The browser
   sends a preflight `OPTIONS` before the `PUT`. Allow the origin, the methods
   `GET`, `PUT` and `HEAD`, and the `content-type` header. The bundled MinIO is
   started with `MINIO_API_CORS_ALLOW_ORIGIN=*`, so this is already true; on a
   bucket of your own it is a setting you have to make. An origin is not a
   credential — the presigned URL is what authorises the request — so allowing
   one costs nothing.

The upside of the same design: your proxy never carries a 200 MB body, so
`client_max_body_size` and upload timeouts do not need raising.

---

## Using a database and bucket you already run

The bundled Postgres and MinIO are a convenience, not a commitment. To use the
company's instead, change four values in `.env.deploy`:

```
DATABASE_URL=postgresql://USER:PASSWORD@db.internal:5432/ehsimcad
STORAGE_ENDPOINT=https://storage.internal:9000
STORAGE_PUBLIC_ENDPOINT=            # empty, if that address works everywhere
STORAGE_ACCESS_KEY_ID=...
STORAGE_SECRET_ACCESS_KEY=...
```

and add `--no-deps` to the three commands:

```
docker compose run --rm --no-deps preflight
docker compose run --rm --no-deps migrate
docker compose up -d --no-deps web worker
```

`--no-deps` is the part that matters. Naming the services alone is **not**
enough: Compose starts a named service's dependencies as well, so
`docker compose up -d web worker` would start the very Postgres you are
replacing — and you would have two databases, one of them quietly empty and
holding nothing you meant to keep.

What you need from that side: Postgres 14 or newer, with a database and a user
that owns it — the application creates its own tables — and one bucket with a
key pair that can read, write **and delete** in it. `preflight` checks all of
that against the real thing before anything serves.

---

## If the server has no internet

The application does not need the internet. The *build* does, and that is the
whole of the difficulty.

Building these two images reaches four places: Docker Hub for the two base
images, npm for the web application's dependencies, PyPI for OpenCascade, and a
Debian mirror for four graphics libraries. On a closed network all four fail,
and no amount of configuration changes that.

So the build does not happen on the server. It happens on a machine that does
have the internet, and what travels is the result.

### Check the architecture first

On the server:

```
uname -m
```

`x86_64` means `linux/amd64`. `aarch64` means `linux/arm64`. Get this wrong and
the archive loads without a word of complaint, then every container exits
immediately with `exec format error`.

### Build the archive, on a machine that has the internet

With Docker and a checkout of this repository:

```
./deploy/pack-images.sh linux/amd64
```

It builds the three application images, pulls the three it does not build --
Postgres, MinIO and `mc` -- checks that all six really came out for the
architecture you asked for, and leaves one file:
`ehsimcad_v1-images-linux-amd64.tar.gz`, about 1.1 GB. Nothing in it is secret --
the build reads no configuration, which is why one archive serves every
environment.

That machine can be a laptop. It does not need to resemble the server: the
image carries its own Linux.

### Load it, on the server

Carry the file over however files get carried there, then:

```
docker load -i ehsimcad_v1-images-linux-amd64.tar.gz
```

Six images appear. The four steps under [Start here](#start-here) now run
unchanged, offline: Compose builds a service only when its image is missing,
and none of them is.

### What is switched off without the internet

Nothing that stops the application working, but be deliberate about it:

- **Leave `AUTH_GITHUB_ID` and `AUTH_GITHUB_SECRET` empty.** Signing in through
  GitHub needs the internet from both the server and the browser. Email and
  password work as normal.
- **Leave `MAIL_API_KEY` empty.** Delivery is over HTTPS to a provider. With no
  key the messages go to the web log, where password-reset links can be read
  out by hand.
- **Leave `GITHUB_DISPATCH_TOKEN` and `GITHUB_REPOSITORY` empty.** They belong
  to the hosted deployment, where conversions run as GitHub Actions. Here the
  worker container is what converts, and it needs nothing outside.

The fonts, the viewer and every asset the site serves are inside the image
already. No page fetches anything from a third party.

### Upgrading, offline

The same two steps: repack on the connected machine, `docker load` on the
server, then `docker compose run --rm migrate` and `docker compose up -d`.

The image tags carry the version, so a release whose images were never loaded
does not quietly start on the old ones -- Compose tries to build, and fails
because it cannot reach anything. That failure is the correct one to get.

---

## Install

### Configure

```
cp .env.deploy.example .env.deploy
```

Every value is explained in the file. As shipped it already matches the
bundled Postgres and MinIO, so the only one you must fill in is `AUTH_SECRET`
(`openssl rand -base64 32`).

Two more to look at before anybody else uses this:

- **`SITE_URL`** — the address people will type. Sign-in does not work until
  this is right: the application is inside a container and cannot see what
  anybody typed, so every URL it builds for itself comes from here.
- **`STORAGE_PUBLIC_ENDPOINT`** — the address browsers upload to. `localhost`
  is correct only at the machine running Docker.

### Check the configuration before anything runs

```
docker compose run --rm preflight
```

Without Docker:

```
cd web && npm ci && node --env-file=../.env.deploy scripts/preflight.mjs
```

It connects to the database, checks the schema is up to date, and writes,
reads back and deletes a test object in the bucket. It names what is wrong and
exits non-zero. **Do not skip this**: every check in it stands for a failure
that is otherwise silent or reported as something else.

Warnings are not failures. They list what is switched off — email delivery,
GitHub sign-in — so nothing is a surprise later.

### Create the schema

```
docker compose run --rm migrate
```

Without Docker: `cd web && npx drizzle-kit migrate`

Run it once, and again after every upgrade. It is deliberately not automatic:
a schema change that runs by itself is a schema change nobody read.

### Start

```
docker compose up -d
```

Without Docker, see **Running it without containers** below.

---

## Check that it works

```
curl -fsS http://localhost:3000/api/health
```

`{"status":"ok","database":true}`. It answers 503 when the database is
unreachable, which is the condition worth taking an instance out of rotation
for. It needs no authentication.

Then, through the address people will actually use:

```
curl -fsS https://cad.internal.example/api/auth/providers
```

A JSON object naming the sign-in methods. This is worth its own line because
of what it fails on: sign-in is the one thing that depends on the application
agreeing about which host it is being reached on, and when it does not, the
browser is told only "There is a problem with the server configuration".
Through `localhost` this can pass while the real address fails, so run it
against the address in `SITE_URL`, through your proxy.

Then, in a browser, end to end:

1. Open `SITE_URL`, create an account, sign in.
2. Upload a STEP file. It should show `queued`, then `converting`, then
   `ready` — under a minute for a typical assembly.
3. Open it. The part tree, the properties panel and the section control should
   all work.

If it stays `queued`, the worker is not running or cannot see the queue:

```
docker compose logs worker        # or: journalctl -u ehsimcad-worker -f
```

---

## When something is wrong

| What you see | What it is |
|---|---|
| Upload fails in the browser, server looks fine | The browser cannot reach `STORAGE_ENDPOINT`, or the bucket rejects the cross-origin request. Look at the browser's network tab: a failed `OPTIONS` is CORS, a failed connection is the address. |
| Model stays `queued` for ever | No worker running, or it cannot reach the database. The queue is a table — nothing is lost, it converts as soon as a worker starts. |
| Worker will not start | It refuses to run when OpenCascade is not importable, rather than accepting jobs and failing all of them. The log says so on the first line. |
| Model goes `failed` | The file itself. `error_message` on the version row, and the worker log, say why. Other uploads are unaffected. |
| "There is a problem with the server configuration" on signing in | The application did not recognise the host it was reached on. Check `SITE_URL`, and that the proxy passes `Host` (and `X-Forwarded-Host`) through rather than rewriting it. The web log names the host it saw. |
| Sign-in loops back to the sign-in page | `AUTH_SECRET` is unset or differs between instances behind a load balancer. |
| "Continue with GitHub" errors | The OAuth application's callback must be exactly `SITE_URL` + `/api/auth/callback/github`. Leave `AUTH_GITHUB_*` empty to switch it off entirely. |
| Password reset email never arrives | Expected with no `MAIL_API_KEY`: the message is written to the web log instead. |
| Everyone signed out after a restart | `AUTH_SECRET` is being regenerated instead of kept. |

---

## Running it without containers

**Web.** Node 22.

```
cd web
npm ci
BUILD_STANDALONE=1 npm run build
cp -r public .next/standalone/
cp -r .next/static .next/standalone/.next/
```

`BUILD_STANDALONE=1` asks for a self-contained server at
`.next/standalone/server.js` — the server plus only the dependencies it
actually reaches. Without it you get an ordinary build, which needs the whole
`node_modules` tree beside it and is started with `npm start` instead.

The build does not copy the static files into the standalone directory: they
are served, not imported, so Next.js leaves them where they are. The two `cp`
lines above are what the container image does; miss them and the site loads
with no styling and no icons.

**Worker.** Python 3.12.

```
cd converter
python3.12 -m venv .venv
.venv/bin/pip install ".[cad]"
```

OpenCascade arrives as a wheel and needs a few system libraries beside it:
`libgl1`, `libglu1-mesa`, `libxrender1`, `libxext6`. It is about 220 MB
installed.

**Both as services.** `deploy/systemd/` holds a unit for each. Adjust the
paths and the user, then:

```
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ehsimcad-web ehsimcad-worker
```

---

## Operating it

**Logs.** `docker compose logs -f web worker`, or `journalctl -u ehsimcad-web
-f`. Both processes log to stdout.

**Restarting.** Safe at any time. The worker finishes the job it is on before
exiting, and a job interrupted anyway is picked up again after
`STALE_JOB_SECONDS` — a crashed worker does not lose an upload.

**More conversion capacity.** Run more workers. Two of them take different
jobs rather than the same one; the queue guarantees it. There is nothing to
configure.

**Backups.** The database and the bucket, together. Either one alone restores
to a catalogue whose files are missing, or files nothing points at.

**Upgrading.** Pull, rebuild, run the migration, restart. In that order —
starting new code against an old schema is the one ordering that breaks.

---

## Known limits

**Email needs an HTTPS provider, not SMTP.** The mail path posts to a provider
over HTTPS. An internal SMTP relay is not supported yet. Until it is, leave
`MAIL_API_KEY` empty: everything works except password reset and address
confirmation, and the messages go to the log where they can be read.

**GitHub sign-in needs the internet**, both from the server and from the
browser. On a closed network, leave it off and use email and password.

**The whole model is downloaded before it is drawn.** Fine for the assemblies
tested — 1.7 MB of geometry and 2 MB of metadata for eleven parts — and not a
design for 50 MB models.

**Deleting a model while it is converting** can leave two files in the bucket
that nothing points at. Seconds-wide, and it costs storage, not correctness.

---

## What has not been tested

v1 was built and run on an arm64 Mac under Colima, against the Postgres and the
MinIO that come with it. From an empty slate -- `docker compose down -v` first,
so no volume survived from an earlier attempt -- running exactly the four steps
at the top of this file:

| Step | Result |
|---|---|
| `docker compose build` | all three application images build |
| `docker compose run --rm preflight` | names the one thing wrong: no migrations applied, 7 to run |
| `docker compose run --rm migrate` | applies all 7 |
| `docker compose run --rm preflight` | Ready, with the 2 expected warnings (email, GitHub sign-in) |
| `docker compose up -d` | four containers; web healthy, postgres healthy, worker polling every 5 s |
| the site, from outside every container | `/api/health` answers `{"status":"ok","database":true}`, `/sign-in` and the viewer both 200 |

The storage round trip was tested in the shape the browser actually uses it,
because that is the part these two addresses exist for:

| Step | Result |
|---|---|
| a presigned PUT, signed by the web application's own signing path | came out for `http://localhost:9000` -- the browser's address, not `minio:9000` |
| the CORS preflight a browser sends first | `OPTIONS` → 204 |
| the upload itself, from outside every container | `PUT` → 200 |
| the worker reading that object back over the internal address | the same bytes, via `http://minio:9000` |

The offline path was tested into a genuinely empty image store -- a second
Docker daemon on the same machine that had never seen this project, with no
images of ours and a build cache of 0 B:

| Step | Result |
|---|---|
| `./deploy/pack-images.sh linux/arm64` | six arm64 images, one file of 1.3 GB |
| `docker load` into the empty store | all six restored |
| the four steps, from the loaded images | preflight, migrate, `up -d`; web healthy and answering |
| the build cache afterwards | still 0 B — nothing was built and nothing was pulled |

Image sizes, as the daemon reports them on disk: **457 MB** web, **2.3 GB**
worker (OpenCascade), **2.4 GB** tools, **411 MB** Postgres, **228 MB** MinIO,
**112 MB** `mc`. Compressed for transfer, all six come to 1.3 GB.

**Not tried: an upload through the site's own form.** Everything underneath it
has been exercised in this stack -- signing, CORS, the PUT, and the worker
reading the object back -- but no file has gone through the catalogue in a
browser here, because that is behind sign-in. The conversion itself is covered
elsewhere: a real STEP file put on the queue was converted by this worker in
about four seconds, 7428 triangles.

**Not tried: `systemctl start`.** There is no systemd on the machine this was
developed on, so the unit files are written from the install steps rather than
from a run.

**Not tried: storage other than the bundled MinIO and Supabase.** Ceph and
StorageGRID answer the same protocol and the S3 client is already set to
path-style addressing, but neither has been used.

**Not tried for v1: x86-64.** The archive tested above is arm64. The x86-64
build was exercised for the previous version -- images built, loaded, preflight
against a real database and bucket, the site answering -- but on an arm64 Mac
emulating x86-64, because there was no x86-64 machine to hand. **Build and load
the `linux/amd64` archive once before the install day**, rather than on it.

The preflight check is the thing to trust: it exercises the real database and
the real bucket, whatever they turn out to be.


---

## Türkçe özet

Bu bölüm yukarıdakinin kısaltılmış hâli. Ayrıntı için İngilizce bölümlere bakın;
komutlar aynıdır.

### Ne kuruyorsunuz

Sürekli çalışan iki süreç ve paylaştıkları iki depo: **web** (Node, 3000 portu,
sitenin kendisi), **worker** (Python + OpenCascade, yüklenen CAD dosyalarını
dönüştürür), **postgres** (katalog ve iş kuyruğu; portu dışarı açılmaz) ve
**minio** (dosyaların kendisi, S3 protokolü, 9000 portu). web ile worker
birbirleriyle hiç konuşmaz; yükleme kuyruk tablosunda bir satır olur, worker
onu alır. Worker durursa site çalışmaya devam eder, yüklemeler sırada bekler.

**Önceden hiçbir şeyin var olması gerekmiyor.** Postgres ve depolama
`compose.yaml` ile birlikte geliyor; kurulum, üzerinde yalnızca Docker olan boş
bir makinede ve hiçbir hesap açmadan tamamlanır. Kurumda zaten bir veritabanı
ve bir kova varsa onları kullanmak dört satır ayar ve bir bayrak meselesi —
aşağıdaki "Kendi veritabanınız ve kovanız" başlığına bakın.

### Sizden istenenler

Docker ve 2–3 GB RAM'lik bir makine. Liste bu kadar. Bir de TLS için 3000
portunun önünde bir ters vekil; vekilin büyük gövde limitine ihtiyacı yok,
çünkü dosyalar uygulamanın üzerinden geçmiyor.

### Sıra

```
cp .env.deploy.example .env.deploy    # içindeki AUTH_SECRET'i doldurun
docker compose run --rm preflight     # "Ready" demeli
docker compose run --rm migrate       # tabloları oluşturur
docker compose up -d
```

Varsayılanı olmayan tek değer `AUTH_SECRET`: `openssl rand -base64 32` bir tane
üretir. Dosyadaki geri kalan her şey birlikte gelen Postgres ve MinIO ile zaten
uyumludur; her ayarın ne işe yaradığı da dosyanın kendi içinde yazılıdır.

### Kendi veritabanınız ve kovanız

`.env.deploy` içinde `DATABASE_URL`, `STORAGE_ENDPOINT`,
`STORAGE_PUBLIC_ENDPOINT` ve depolama anahtarlarını kendi altyapınıza çevirin,
sonra üç komuta `--no-deps` ekleyin:

```
docker compose run --rm --no-deps preflight
docker compose run --rm --no-deps migrate
docker compose up -d --no-deps web worker
```

Önemli olan `--no-deps`. Yalnızca servis adını yazmak **yetmez**: Compose
adını verdiğiniz servisin bağımlılıklarını da başlatır, yani
`docker compose up -d web worker` tam da yerine geçmek istediğiniz Postgres'i
ayağa kaldırır.

### İnternetsiz sunucu

Uygulamanın **çalışması** için internet gerekmiyor; **derlenmesi** için
gerekiyor. Derleme dört yere uzanır: iki temel imaj için Docker Hub, web
bağımlılıkları için npm, OpenCascade için PyPI ve dört grafik kütüphanesi için
bir Debian aynası. Kapalı ağda dördü de başarısız olur ve bunu hiçbir ayar
değiştirmez.

Bu yüzden derleme sunucuda yapılmaz. İnterneti olan bir makinede yapılır,
sunucuya sonucu taşınır.

**Önce mimariyi öğrenin.** Sunucuda `uname -m` çalıştırın: `x86_64` ise
`linux/amd64`, `aarch64` ise `linux/arm64`. Yanlışını taşırsanız arşiv tek
kelime itiraz etmeden yüklenir, sonra her konteyner anında `exec format error`
ile kapanır.

**İnterneti olan makinede** — bu depo ve Docker yeterli; bir dizüstü olabilir,
sunucuya benzemesi gerekmez:

```
./deploy/pack-images.sh linux/amd64
```

Üç uygulama imajını derler, derlemediği üçünü -- Postgres, MinIO ve `mc` --
indirir, altısının da gerçekten istenen mimaride olduğunu doğrular ve tek bir
dosya bırakır: `ehsimcad_v1-images-linux-amd64.tar.gz`, yaklaşık 1,1 GB. İçinde
gizli hiçbir şey yoktur — derleme hiçbir ayar okumaz, o yüzden tek arşiv her
ortama gider.

**Sunucuda:**

```
docker load -i ehsimcad_v1-images-linux-amd64.tar.gz
```

Altı imaj görünür. Yukarıdaki dört adım bundan sonra olduğu gibi, internetsiz
çalışır: Compose bir servisi yalnızca imajı yoksa derler, artık hiçbirinin
imajı eksik değildir.

**Kapalı ağda kapalı kalacaklar.** Hiçbiri uygulamayı durdurmaz, ama bilerek
kapatın: `AUTH_GITHUB_ID` ve `AUTH_GITHUB_SECRET` boş kalsın (GitHub ile giriş
hem sunucudan hem tarayıcıdan internet ister; e-posta ve şifreyle giriş
çalışır), `MAIL_API_KEY` boş kalsın (mesajlar gönderilmez, web günlüğüne
yazılır — şifre sıfırlama bağlantısı oradan okunabilir), `GITHUB_DISPATCH_TOKEN`
ve `GITHUB_REPOSITORY` boş kalsın (bunlar barındırılan kurulumun ayarıdır;
burada dönüştürmeyi worker konteyneri yapar ve dışarıya ihtiyacı yoktur). Yazı
tipleri, viewer ve sitenin sunduğu her dosya imajın içindedir; hiçbir sayfa
üçüncü bir yerden bir şey çekmez.

**Yükseltme** aynı iki adım: bağlı makinede yeniden paketleyin, sunucuda
`docker load`, sonra `migrate` ve `up -d`. İmaj etiketleri sürümü taşıdığı
için, imajları yüklenmemiş bir sürüm sessizce eskisiyle başlamaz — Compose
derlemeyi dener ve hiçbir yere ulaşamadığı için durur. Alınması gereken hata
budur.

### Üç kural

**1. `preflight` "Ready" demeden hiçbir şey başlatmayın.** Bu komut gerçek
veritabanına bağlanır, gerçek kovaya gerçek bir nesne yazar, geri okur ve siler.
Sonra neyin yanlış olduğunu tek cümleyle söyler: hangi anahtar reddedildi,
hangi kova yok, hangi adres çözülemedi. Burada beş dakikada çözülen bir sorun,
servis ayağa kalktıktan sonra "uygulama bozuk" gibi görünür.

**2. Depolama adresi kullanıcıların bilgisayarından erişilebilir olmalı**,
yalnızca sunucudan değil. Tarayıcı dosyayı doğrudan depolamaya yükler, hiçbir
zaman uygulamanın üzerinden geçmez. Bu yüzden tek depo için iki ayar var:

| Ayar | Kimin kullandığı | Gelen değer |
|---|---|---|
| `STORAGE_ENDPOINT` | web ve worker, konteyner ağının içinden | `http://minio:9000` |
| `STORAGE_PUBLIC_ENDPOINT` | tarayıcı, birinin masasından | `http://localhost:9000` |

`minio` adı yalnızca konteyner ağında çözülür, tarayıcıda çözülmez. Adres
imzanın parçası olduğu için URL'yi sonradan düzeltmek de işe yaramaz — imza
tutmaz. Baştan doğru adres için imzalanmalı.

**Gelen `localhost:9000` değeri yalnızca Docker'ı çalıştıran makinenin başında
otururken doğrudur.** Başka herkes için sunucunun adını ya da adresini yazın.
`preflight`, tarayıcıya konteyner-içi bir ad verilecekse doğrudan hata verir;
`localhost` verilecekken site başka bir adresten sunuluyorsa uyarır. Daha
fazlasını yapamaz: kimsenin masaüstünde değil.

**3. Kova, sitenin adresinden gelen çapraz kaynak isteklerine izin vermeli.**
Aynı sebep. Tarayıcı dosyayı göndermeden önce `OPTIONS` gönderir. Kaynağa,
`GET` / `PUT` / `HEAD` metotlarına ve `content-type` başlığına izin verin.
Birlikte gelen MinIO buna zaten izin veriyor; kendi kovanıza siz söylemelisiniz.

### Çalıştığını doğrulama

```
curl -fsS http://localhost:3000/api/health
```

`{"status":"ok","database":true}` beklenir. Veritabanına ulaşılamıyorsa 503
döner. Kimlik doğrulama istemez.

Sonra, insanların gerçekten kullanacağı adres üzerinden:

```
curl -fsS https://cad.internal.example/api/auth/providers
```

Giriş yöntemlerini sayan bir JSON dönmeli. Ayrı bir satırı hak etmesinin sebebi
neyde patladığı: giriş, uygulamanın hangi adres üzerinden erişildiği konusunda
hemfikir olmasına bağlı olan tek şey ve hemfikir olmadığında tarayıcıya
yalnızca "There is a problem with the server configuration" yazıyor. `localhost`
üzerinden geçip gerçek adreste patlayabilir, o yüzden `SITE_URL`'deki adrese,
vekilin üzerinden çalıştırın.

Sonra tarayıcıdan: hesap açın, giriş yapın, bir STEP dosyası yükleyin. `queued`
→ `converting` → `ready` sırasını izlemeli, tipik bir montaj için bir dakikadan
kısa sürede. Sonra modeli açın; parça ağacı, özellikler paneli ve kesit
kontrolü çalışmalı.

`queued` durumunda takılı kalıyorsa worker çalışmıyordur ya da veritabanını
göremiyordur — `docker compose logs worker`. Kuyruk bir veritabanı tablosudur:
hiçbir şey kaybolmaz, worker başlar başlamaz dönüşüm yapılır.

### Sorun çıkarsa

Belirtilerin ne anlama geldiği [When something is wrong](#when-something-is-wrong)
tablosunda. En sık çıkanlar:

| Gördüğünüz | Sebebi |
|---|---|
| Tarayıcıda yükleme başarısız, sunucu sağlıklı | Tarayıcı depolama adresine ulaşamıyor ya da kova CORS'a izin vermiyor. Tarayıcının ağ sekmesine bakın: başarısız `OPTIONS` CORS'tur, başarısız bağlantı adrestir. |
| Model sonsuza kadar `queued` | Worker çalışmıyor ya da veritabanını göremiyor. Veri kaybı yok. |
| Worker başlamıyor | OpenCascade yüklenemiyorsa bilerek başlamayı reddeder. Günlüğün ilk satırı sebebi yazar. |
| Girişte "There is a problem with the server configuration" | Uygulama, üzerinden erişildiği adresi tanımadı. `SITE_URL`'e bakın ve vekilin `Host` (ve `X-Forwarded-Host`) başlığını değiştirmeden geçirdiğinden emin olun. Web günlüğü gördüğü adresi yazar. |
| Girişten sonra tekrar giriş sayfası | `AUTH_SECRET` boş, ya da yük dengeleyici arkasındaki örnekler arasında farklı. |
| Yeniden başlatınca herkes çıkmış | `AUTH_SECRET` her seferinde yeniden üretiliyor; sabit olmalı. |
| Şifre sıfırlama e-postası gelmiyor | `MAIL_API_KEY` boşken beklenen davranış: mesaj gönderilmez, web günlüğüne yazılır. |

### Bilinen sınırlar

E-posta HTTPS üzerinden bir sağlayıcı ister; iç SMTP sunucusu henüz
desteklenmiyor. `MAIL_API_KEY` boş bırakılabilir — şifre sıfırlama ve adres
doğrulama dışında her şey çalışır. GitHub ile giriş internet erişimi ister;
kapalı ağda kapatın, e-posta ve şifreyle giriş çalışır.

### Denenmiş ve denenmemiş olanlar

v1, arm64 bir Mac üzerinde Colima ile, birlikte gelen Postgres ve MinIO'ya
karşı derlendi ve çalıştırıldı. Sıfırdan — önce `docker compose down -v`, yani
önceki denemelerden hiçbir disk kalmadan — ve tam olarak yukarıdaki dört adım:
`preflight` eksik olan tek şeyi söyledi (7 migration bekliyor), `migrate`
yedisini de uyguladı, `preflight` "Ready" dedi (beklenen 2 uyarıyla: e-posta ve
GitHub girişi kapalı), `up -d` dört konteyneri kaldırdı — web sağlıklı,
postgres sağlıklı, worker 5 saniyede bir kuyruğa bakıyor. Dışarıdan
`/api/health` `{"status":"ok","database":true}` döndü; `/sign-in` ve viewer 200.

Depolama gidiş-dönüşü, tarayıcının gerçekten kullandığı biçimde sınandı —
iki adresin var olma sebebi tam olarak bu: uygulamanın kendi imzalama yolundan
üretilen PUT URL'si `http://localhost:9000` için çıktı (`minio:9000` için
değil), tarayıcının önce gönderdiği CORS `OPTIONS` isteği 204 aldı, bütün
konteynerlerin dışından yapılan `PUT` 200 aldı, ve worker aynı nesneyi iç
adresten (`http://minio:9000`) aynı içerikle geri okudu.

İnternetsiz yol, gerçekten boş bir imaj deposunda sınandı: aynı makinede bu
projeyi hiç görmemiş ikinci bir Docker sunucusu, bize ait hiçbir imaj ve 0 B
derleme önbelleği. `./deploy/pack-images.sh linux/arm64` altı imajı ve 1,3
GB'lık tek bir dosya üretti; `docker load` altısını da geri yükledi; dört adım
yalnızca yüklenen imajlardan çalıştı ve sonrasında derleme önbelleği hâlâ 0
B'ydi — hiçbir şey derlenmedi, hiçbir şey indirilmedi.

İmaj boyutları (diskte): web 457 MB, worker 2,3 GB (OpenCascade), tools 2,4 GB,
Postgres 411 MB, MinIO 228 MB, `mc` 112 MB. Taşımak için sıkıştırıldığında
altısı 1,3 GB.

**Denenmeyenler.** Sitenin kendi yükleme formundan bir dosya: altındaki her şey
bu yığında sınandı (imzalama, CORS, PUT, worker'ın geri okuması) ama giriş
gerektirdiği için tarayıcıdan uçtan uca bir yükleme burada yapılmadı —
dönüştürmenin kendisi başka yerde kanıtlı: kuyruğa konan gerçek bir STEP
dosyası bu worker tarafından ~4 saniyede dönüştürüldü, 7428 üçgen. Ayrıca
`systemctl start` (bu makinede systemd yok), MinIO ve Supabase dışı bir
depolama, ve **v1 için x86-64**: yukarıdaki arşiv arm64. x86-64 bir önceki
sürümde derlenip çalıştırıldı, ama elde x86-64 makine olmadığı için arm64 bir
Mac üzerinde öykünmeyle. **`linux/amd64` arşivini kurulum gününden önce bir
kez üretip yükleyin**, kurulum gününde değil.

Güvenilecek şey `preflight` çıktısıdır: sizin gerçek veritabanınızı ve gerçek
kovanızı sınar.
