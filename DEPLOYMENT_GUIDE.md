# Supabase & Vercel Canlıya Alma Rehberi (Adım Adım)

Bu rehber, uygulamanızı **Supabase** veritabanı ve **Vercel** bulut sunucusuna sıfır hata ile taşımanız için hazırlanmıştır.

---

## 1. ADIM: Supabase'de Veritabanını Kurma

1. [supabase.com](https://supabase.com) adresine gidin ve ücretsiz bir hesap/proje oluşturun.
2. Sol menüden **SQL Editor** bölümüne tıklayın.
3. Projenizdeki [`supabase_schema.sql`](./supabase_schema.sql) dosyasının tüm içeriğini kopyalayıp buraya yapıştırın ve **Run** butonuna basın.
   - *Tüm tablolar, ilişkiler, kurlar ve indeksler saniyeler içinde Supabase'de kurulacaktır.*
4. Sol alttaki **Project Settings ➔ Database** bölümüne gidin:
   - **Connection string** alanında **URI** seçeneğini seçin.
   - **Mode:** `Transaction` (Port: `6543`) seçtiğinizden emin olun (Serverless için en hızlı ve güvenli moddur).
   - Bağlantı adresinizi kopyalayın (Örn: `postgresql://postgres.[REF]:[SIFRENIZ]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres?sslmode=require`).

---

## 2. ADIM: (İsteğe Bağlı) Mevcut SQLite Verilerinizi Supabase'e Taşıma

Masaüstünüzdeki mevcut cari hesap ve işlemlerinizi Supabase'e tek tuşla aktarmak için:

1. Proje ana dizinindeki `.env.example` dosyasını `.env` olarak kopyalayın:
   ```bash
   cp .env.example .env
   ```
2. `.env` dosyasını açıp `DATABASE_URL` alanına 1. Adımda aldığınız Supabase URI adresini yapıştırın.
3. Şu komutu çalıştırarak mevcut tüm verileri Supabase'e aktarın:
   ```bash
   python3 scripts/migrate_sqlite_to_supabase.py
   ```
   *(Tüm cari hesaplar, döviz bağlantıları ve işlemler Supabase'e eksiksiz aktarılacaktır.)*

---

## 3. ADIM: Kodları GitHub'a Yükleme

Terminalden proje klasörünüzde şu komutları çalıştırarak GitHub'a yükleyin:

```bash
git init
git add .
git commit -m "feat: Supabase and Vercel cloud deployment ready"
git branch -M main
git remote add origin https://github.com/KULLANICI_ADINIZ/RE सीताराम_ADINIZ.git
git push -u origin main
```
*(Not: `.gitignore` dosyası otomatik olarak `.env` ve kişisel yerel veritabanı dosyalarınızı koruma altına almıştır; şifreleriniz asla GitHub'a gitmez.)*

---

## 4. ADIM: Vercel'e Bağlama ve Yayınlama (Deploy)

1. [vercel.com](https://vercel.com) adresine gidin ve GitHub hesabınızla giriş yapın.
2. **Add New... ➔ Project** butonuna tıklayın ve GitHub'a yüklediğiniz projeyi seçin (**Import**).
3. **Environment Variables** bölümünü açın ve şu değişkeni ekleyin:
   - **Key:** `DATABASE_URL`
   - **Value:** `postgresql://postgres.[REF]:[SIFRENIZ]@...pooler.supabase.com:6543/postgres?sslmode=require`
4. **Deploy** butonuna basın!
   - Vercel yaklaşık 30-45 saniye içinde uygulamanızı derleyip size özel ücretsiz bir HTTPS bağlantısı (`https://projeniz.vercel.app`) verecektir.

---

## 5. ADIM: Kullanım (Bilgisayar & Cep Telefonu)

- Size verilen `https://projeniz.vercel.app` adresini hem **bilgisayarınızdan** hem de **cep telefonunuzdan** açabilirsiniz.
- **Cep Telefonunda:** Safari veya Chrome'da *"Ana Ekrana Ekle"* (PWA) seçeneğini kullanarak uygulamayı tıpkı bir mobil uygulama gibi yükleyebilirsiniz.
- **Varsayılan Giriş:**
  - Kullanıcı Adı: `admin`
  - Şifre: `admin123` (İlk girişten sonra profil menüsünden değiştirebilirsiniz)

