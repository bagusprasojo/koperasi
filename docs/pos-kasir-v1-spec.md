# Spesifikasi Teknis Kasir v1 (POS)

## 1) Tujuan
- Menjadikan modul kasir sebagai modul utama dengan pengalaman kerja secepat aplikasi desktop.
- Menekan ketergantungan mouse lewat keyboard-first flow.
- Menyiapkan jalur print thermal yang robust.
- Menjaga backend tetap reusable agar mudah dibungkus menjadi aplikasi desktop di masa depan.

## 2) Prinsip Desain
- Desktop-like behavior: fokus input selalu aktif, respons instan, state transaksi jelas.
- Keyboard-first: seluruh aksi utama punya shortcut.
- Deterministik: satu aksi menghasilkan satu transaksi final (idempotent).
- Observability: semua aksi kritikal tercatat untuk audit dan debugging.
- API-first: POS UI hanya client; domain logic tetap di service/backend.

## 3) Arsitektur Target
- Backend: Django (domain, validasi, transaksi database, audit, otorisasi).
- POS UI: halaman web khusus kasir (single workflow), minim navigasi.
- Print bridge: service lokal kecil (HTTP localhost) untuk kirim raw ESC/POS ke printer thermal.
- Queue lokal: browser-side queue untuk job print/retry.

Catatan desktop-ready:
- Endpoint dan payload POS distandarkan sejak awal.
- UI kasir dipisah dari template umum agar mudah dipindah ke Electron/Tauri nanti.

## 4) Scope v1
- Buat transaksi penjualan kasir end-to-end.
- Cari/tambah item cepat (barcode/SKU/nama).
- Hitung harga sesuai tier yang sudah ada.
- Dukungan pembayaran cash, member deposit, dan gabungan (split payment).
- Simpan sale + payment + mutasi stok atomik.
- Cetak struk thermal per transaksi.
- Void transaksi terbatas (role admin/supervisor).

## 5) Non-Functional Requirement
- Latensi tambah item target < 150ms (lokal jaringan toko).
- Checkout tidak boleh double-submit.
- Print tidak blocking UI kasir.
- Error harus actionable (bukan traceback mentah).
- Semua nominal pakai pemisah ribuan di tampilan.

## 6) UX Flow Kasir (Keyboard-First)
1. Kasir buka layar POS, fokus default di input scan/cari item.
2. Member default otomatis terpasang (non-member walk-in).
3. Kasir bisa cari dan pilih member real dengan cepat (by kartu/no hp/nama).
4. Jika member real terpilih, info kartu/member tampil ringkas.
5. Scan barcode atau ketik SKU/nama lalu Enter.
6. Item masuk cart, qty default 1.
7. Jika item yang sama discan ulang, qty baris item tersebut bertambah (tidak membuat baris baru).
8. Shortcut qty: pilih baris lalu ubah qty via keyboard.
9. Shortcut hapus baris, hold transaksi, dan clear cart.
10. Tekan checkout, pilih metode pembayaran di modal pembayaran.
11. Untuk pembayaran kartu/deposit, validasi kecocokan member dan kartu + otorisasi pemegang kartu.
12. Konfirmasi total diterima.
13. Simpan transaksi.
14. Trigger print struk ke print queue.
15. UI reset ke transaksi baru.

## 7) Shortcut Keyboard Minimal v1
- F2: fokus ke input scan/cari.
- F4: fokus ke panel pembayaran.
- F8: hold transaksi.
- F9: buka dialog checkout.
- F10: simpan/selesaikan transaksi.
- Ctrl+Del: hapus baris item aktif.
- Ctrl+/: buka bantuan shortcut.
- Esc: tutup dialog aktif.

## 8) Data & Domain Rules
- Sale header: nomor transaksi, kasir, waktu, member wajib (minimal member default/walk-in), subtotal, total.
- Sale item: produk, qty, harga terpilih, subtotal.
- Scanner rule: scan produk sama berulang => increment qty baris existing.
- Payment: multi-payment didukung (cash, deposit, gabungan).
- Stock mutation: selalu tercatat via inventory ledger.
- Member deposit: debit saldo + ledger member harus atomik bersama sale.
- Pembayaran deposit wajib validasi:
- member transaksi dan pemilik kartu harus cocok.
- perlu otorisasi pemegang kartu (PIN/OTP/signature sesuai kebijakan implementasi).
- Idempotency key: setiap submit checkout membawa client_txn_id unik.

## 9) Integritas & Transaksi DB
- Gunakan transaction.atomic pada checkout.
- Urutan commit:
1. validasi cart terbaru (harga, stok, status item)
2. create sale header
3. create sale items
4. create payment
5. post inventory out (pos_sale)
6. post member ledger (jika deposit)
7. set status final + total
- Jika salah satu langkah gagal, rollback penuh.

## 10) Otorisasi
- Role kasir: transaksi jual normal, hold/resume, print.
- Role admin_toko: semua hak kasir + void/reprint/reversal khusus.
- Aksi sensitif (void, ubah harga manual, override diskon): wajib supervisor approval (v2 jika belum ada PIN supervisor).

## 11) Thermal Printing Strategy
- Gunakan ESC/POS raw command.
- POS web mengirim payload ke local print bridge, contoh: POST http://127.0.0.1:17971/print.
- Print bridge menulis ke device printer dan mengembalikan status job.
- Stop print berdasarkan akhir data stream (bukan ukuran kertas), lalu cut command di akhir struk.

Payload print v1:
- job_id
- sale_number
- copies
- lines/commands
- printer_profile

Retry policy:
- Jika printer offline, job masuk retry queue.
- UI tampilkan badge pending print.
- Kasir/admin bisa reprint job gagal.

## 12) Offline & Resilience (v1.1 jika belum penuh di v1)
- Simpan draft cart di localStorage/IndexedDB.
- Simpan print queue lokal agar tetap bisa retry.
- Untuk checkout offline penuh butuh strategi sinkronisasi dan conflict resolution (masuk fase lanjut).

## 13) API Contract Awal
- POST /sales/pos/cart/price-preview
- POST /sales/pos/checkout
- POST /sales/pos/void/{sale_number}
- GET /sales/pos/receipt/{sale_number}
- POST /sales/pos/reprint/{sale_number}

Payload checkout minimal:
- member_id (wajib, default member jika walk-in)
- cart_items [{ product_id, qty }]
- payments [{ method: cash|deposit, amount }]
- card_number (wajib jika ada pembayaran deposit)
- card_auth (PIN/OTP/approval token, sesuai mekanisme final)
- client_txn_id (idempotency)

Standar respons:
- success: boolean
- code: string
- message: string
- data: object
- request_id: string

## 14) Logging & Audit
- Simpan log event:
- sale_created
- payment_posted
- stock_posted
- member_debited
- receipt_printed
- sale_voided
- reprint_requested
- Field minimal log: actor, timestamp, terminal_id, sale_number, payload_ringkas.

## 15) Struktur UI POS v1
- Panel kiri: pencarian item + hasil cepat.
- Panel tengah: cart line-item editable.
- Panel kanan: ringkasan total, member, metode bayar, tombol checkout.
- Footer status: mode keyboard, status printer, koneksi, queue print.

## 16) Roadmap Implementasi
Tahap A (fondasi backend)
- Tambah endpoint checkout idempotent.
- Rapikan service checkout atomic + audit.
- Tambah model print job (opsional, jika mau persisted queue server-side).

Tahap B (UI kasir desktop-like)
- Halaman POS baru keyboard-first.
- Hotkeys, focus manager, cart interaksi cepat.
- Dialog checkout non-blocking.

Tahap C (thermal print)
- Integrasi print bridge localhost.
- Template struk ESC/POS.
- Retry queue + reprint.

Tahap D (hardening)
- Benchmark latency.
- Uji race condition/double submit.
- Uji skenario gagal printer/gagal jaringan.

## 17) Definition of Done v1
- Kasir bisa transaksi cepat tanpa banyak mouse.
- Checkout aman dari double-submit.
- Stok dan pembayaran konsisten.
- Struk thermal tercetak stabil dan berhenti saat job selesai.
- Ada audit event untuk aksi kritikal.
- QA lulus skenario normal + error utama.
