# Thermal Print Bridge (Windows) - POS Koperasi

Dokumen ini untuk menjalankan bridge printer thermal ESC/POS asli di Windows.

## 1) Instal dependency
Jalankan di root project:

```powershell
venv\Scripts\pip install -r requirements.txt
```

Pastikan `pywin32` terpasang.

## 2) Tentukan nama printer
Lihat nama printer persis dari Windows (Devices and Printers). Contoh:
- `EPSON TM-T82 Receipt`

## 3) Jalankan mode console (uji cepat)
Set env lalu jalankan server:

```powershell
$env:PRINTER_NAME="EPSON TM-T82 Receipt"
$env:PRINT_BRIDGE_HOST="127.0.0.1"
$env:PRINT_BRIDGE_PORT="17971"
venv\Scripts\python tools\print_bridge\bridge_server.py
```

Cek health:

```powershell
curl http://127.0.0.1:17971/health
```

## 4) Test print manual

```powershell
curl -X POST http://127.0.0.1:17971/print `
  -H "Content-Type: application/json" `
  -d '{"sale_number":"TEST-001","copies":1,"lines":["POS KOPERASI","TEST PRINT","Terima kasih"]}'
```

## 4b) Mode simulator (tanpa printer fisik)
Jika tidak punya printer thermal, gunakan simulator:

```powershell
$env:PRINT_SIMULATE="1"
$env:PRINT_SIMULATE_DIR="D:\\BP\\sharedprojects\\koperasi\\tmp\\print-sim"
venv\Scripts\python tools\print_bridge\bridge_server.py
```

Semua request `/print` akan sukses dan hasilnya disimpan ke file:
- `*.txt` (hasil teks terbaca)
- `*.bin` (raw bytes ESC/POS)

Alternatif: kirim `printer_name: "SIMULATOR"` di payload.

## 5) Install sebagai Windows Service
Jalankan PowerShell as Administrator.

```powershell
$env:PRINTER_NAME="EPSON TM-T82 Receipt"
$env:PRINT_BRIDGE_HOST="127.0.0.1"
$env:PRINT_BRIDGE_PORT="17971"
venv\Scripts\python tools\print_bridge\windows_service.py install
venv\Scripts\python tools\print_bridge\windows_service.py start
```

Stop/Remove service:

```powershell
venv\Scripts\python tools\print_bridge\windows_service.py stop
venv\Scripts\python tools\print_bridge\windows_service.py remove
```

## 6) Integrasi dengan POS Koperasi
Backend POS sudah mengirim print ke default:
- `http://127.0.0.1:17971/print`

Jika service hidup dan printer benar, status print di modal struk akan `sent`.
Jika gagal, status `failed` dan bisa `Retry Pending`.

## 7) Troubleshooting
- Error `pywin32 belum terpasang`: install ulang requirements.
- Error printer not found: pastikan `PRINTER_NAME` sama persis dengan nama printer Windows.
- Job gagal saat service: cek Windows Event Viewer log aplikasi service.
- Karakter aneh: saat ini encoding pakai CP437; jika perlu kita bisa ubah profile per printer.
- `Microsoft Print to PDF` tidak ideal untuk RAW ESC/POS (sering tidak muncul Save As). Untuk testing tanpa printer gunakan mode simulator di atas.
