# Frontend Workflow (Tailwind tanpa CDN)

Dokumen ini jadi acuan tim untuk workflow frontend di project Koperasi.

## Tujuan
- Styling tidak menggunakan CDN.
- CSS utama berasal dari Tailwind build file.
- Alur dev dan production harus konsisten dengan setup server.

## Struktur file utama
- Input Tailwind: `static/src/input.css`
- Output dev: `static/css/app.css`
- Output production: `staticfiles/css/app.css`
- Template load CSS: `templates/base.html` -> `{% static 'css/app.css' %}`

## Command yang dipakai tim
Install dependency frontend:

```powershell
npm install
```

Mode development (watch):

```powershell
npm run dev
```

Mode production build:

```powershell
npm run build
```

## Aturan penggunaan
1. Saat coding UI harian, jalankan `npm run dev`.
2. Jangan edit langsung file output `app.css`, edit di source (`templates` atau `static/src/input.css`).
3. Untuk release/deploy, jalankan `npm run build` agar CSS final masuk ke `staticfiles/css/app.css` sesuai alur Nginx production.
4. Pastikan class Tailwind dipakai di folder yang tercakup `tailwind.config.js` content scan.

## Troubleshooting cepat
- Perubahan class tidak muncul:
1. Pastikan `npm run dev` sedang aktif.
2. Cek path template memang masuk scan `tailwind.config.js`.
3. Hard refresh browser (`Ctrl + F5`).

- CSS production tidak update:
1. Jalankan ulang `npm run build`.
2. Reload/redeploy service yang melayani static files bila perlu.

## Catatan penting
- Workflow ini memang sengaja membedakan output dev dan output production.
- Jangan ubah path output build tanpa koordinasi karena sudah terkait konfigurasi Nginx.
