# Koperasi Print Agent (Desktop EXE)

Print agent ini adalah pengganti `print_bridge` dengan endpoint kompatibel:

- `GET http://127.0.0.1:17971/health`
- `POST http://127.0.0.1:17971/print`
- `GET http://127.0.0.1:17971/` (home UI)
- `GET http://127.0.0.1:17971/logs` (log print)

Tujuan: aplikasi web POS **tidak perlu perubahan**.

## 1) Jalankan dari Python (dev/test)

```powershell
$env:PRINTER_NAME="EPSON TM-T82X Receipt"
$env:PRINT_BRIDGE_HOST="127.0.0.1"
$env:PRINT_BRIDGE_PORT="17971"
venv\Scripts\python tools\print_agent\app.py
```

Cek:

```powershell
Invoke-WebRequest http://127.0.0.1:17971/health
```

## 2) Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File tools\print_agent\build.ps1
```

Hasil:

`tools\print_agent\dist\KoperasiPrintAgent.exe`

## 3) Konfigurasi via `.env` (opsional)

Buat file `tools\print_agent\.env`:

```env
PRINTER_NAME=EPSON TM-T82X Receipt
PRINT_BRIDGE_HOST=127.0.0.1
PRINT_BRIDGE_PORT=17971
PRINT_BRIDGE_ALLOWED_ORIGINS=*
PRINT_AGENT_LOG_FILE=C:\Koperasi\print-agent.log
```

Catatan:

- Jika `PRINTER_NAME` kosong, payload `/print` harus mengirim `printer_name`.
- Untuk debug tanpa printer, set:
  - `PRINT_SIMULATE=1`

## 4) Autostart saat login Windows

Contoh buat scheduled task:

```powershell
$exe = "C:\Koperasi\KoperasiPrintAgent.exe"
schtasks /Create /TN "KoperasiPrintAgent" /SC ONLOGON /RL HIGHEST /TR "`"$exe`"" /F
schtasks /Run /TN "KoperasiPrintAgent"
```

## 5) Uji cetak manual

```powershell
$body = @{
  sale_number = "TEST-001"
  copies = 1
  lines = @("POS KOPERASI","No: TEST-001","TOTAL 10.000")
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:17971/print" -Method Post -ContentType "application/json" -Body $body
```

## 6) Catatan kompatibilitas

- Kontrak API disamakan dengan `tools/print_bridge/bridge_server.py`.
- Jika agent ini dipakai, Anda bisa stop service `print_bridge` lama untuk menghindari bentrok port.
- Home UI menyediakan:
  - tombol `Cek Kesehatan`
  - tombol `Test Print`
  - tombol menuju halaman `Log Print`
