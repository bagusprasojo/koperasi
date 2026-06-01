import json
import logging
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

try:
    import win32print
except Exception:  # pragma: no cover
    win32print = None

LOGGER = logging.getLogger('koperasi_print_agent')


def escpos_text_lines(lines):
    payload = bytearray()
    payload += b'\x1b@'
    payload += b'\x1ba\x01'
    for i, line in enumerate(lines or []):
        if i == 1:
            payload += b'\x1ba\x00'
        payload += str(line).encode('cp437', errors='replace') + b'\n'
    payload += b'\n\n\n'
    payload += b'\x1dV\x00'
    return bytes(payload)


def raw_print(printer_name, raw_bytes, doc_name='POS Receipt'):
    if win32print is None:
        raise RuntimeError('pywin32 belum terpasang. Install: pip install pywin32')
    h_printer = win32print.OpenPrinter(printer_name)
    try:
        h_job = win32print.StartDocPrinter(h_printer, 1, (doc_name, None, 'RAW'))
        try:
            win32print.StartPagePrinter(h_printer)
            win32print.WritePrinter(h_printer, raw_bytes)
            win32print.EndPagePrinter(h_printer)
        finally:
            win32print.EndDocPrinter(h_printer)
        return h_job
    finally:
        win32print.ClosePrinter(h_printer)


def simulate_print(raw_bytes, doc_name='POS Receipt'):
    out_dir = os.getenv('PRINT_SIMULATE_DIR', os.path.join(os.getcwd(), 'tmp', 'print-sim'))
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    safe_name = ''.join(c if c.isalnum() or c in ('-', '_') else '-' for c in doc_name)[:60]
    txt_path = os.path.join(out_dir, f'{stamp}-{safe_name}.txt')
    bin_path = os.path.join(out_dir, f'{stamp}-{safe_name}.bin')
    try:
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(raw_bytes.decode('cp437', errors='replace'))
        with open(bin_path, 'wb') as f:
            f.write(raw_bytes)
    except Exception:
        with open(bin_path, 'wb') as f:
            f.write(raw_bytes)
    return {'txt_path': txt_path, 'bin_path': bin_path}


class PrintHandler(BaseHTTPRequestHandler):
    server_version = 'KoperasiPrintAgent/1.0'

    def _cors_origin(self):
        allowed = os.getenv('PRINT_BRIDGE_ALLOWED_ORIGINS', '*').strip()
        req_origin = self.headers.get('Origin', '')
        if allowed == '*':
            return '*'
        allowed_list = [x.strip() for x in allowed.split(',') if x.strip()]
        if req_origin and req_origin in allowed_list:
            return req_origin
        return allowed_list[0] if allowed_list else '*'

    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', self._cors_origin())
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Max-Age', '86400')

    def _json(self, code, payload):
        out = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self._set_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _html(self, code, content):
        out = content.encode('utf-8')
        self.send_response(code)
        self._set_cors_headers()
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _append_print_log(self, payload):
        path = getattr(self.server, 'print_log_path', '')
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def _render_home(self):
        printer = escape(getattr(self.server, 'printer_name', '') or '(payload)')
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Koperasi Print Agent</title>
<style>
body{{font-family:Arial,sans-serif;background:#f1f5f9;margin:0;padding:24px;color:#0f172a}}
.card{{max-width:760px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px}}
button{{border:1px solid #cbd5e1;background:#0f172a;color:#fff;border-radius:8px;padding:10px 14px;cursor:pointer}}
button.secondary{{background:#fff;color:#0f172a}}
pre{{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow:auto}}
.row{{display:flex;gap:10px;flex-wrap:wrap}}
a{{color:#1d4ed8;text-decoration:none}}
</style></head>
<body><div class="card">
<h2>Koperasi Print Agent</h2>
<p>Endpoint: <code>http://127.0.0.1:{getattr(self.server, 'server_port', 17971)}</code></p>
<p>Printer default: <strong>{printer}</strong></p>
<div class="row">
<button onclick="checkHealth()">Cek Kesehatan</button>
<button onclick="testPrint()">Test Print</button>
<a href="/logs"><button class="secondary" type="button">Lihat Log Print</button></a>
</div>
<h3>Hasil</h3><pre id="out">-</pre>
</div>
<script>
async function checkHealth(){{
  const r=await fetch('/health'); const d=await r.json();
  document.getElementById('out').textContent=JSON.stringify(d,null,2);
}}
async function testPrint(){{
  const payload={{sale_number:'TEST-AGENT',copies:1,lines:['KOPERASI','TEST PRINT',new Date().toISOString()]}}
  const r=await fetch('/print',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
  const d=await r.json();
  document.getElementById('out').textContent=JSON.stringify(d,null,2);
}}
</script></body></html>"""

    def _read_log_rows(self, limit=200):
        path = getattr(self.server, 'print_log_path', '')
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            rows = []
            for raw in lines[-limit:]:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except Exception:
                    rows.append({'raw': raw})
            return rows
        except Exception:
            return []

    def _render_logs(self):
        rows = self._read_log_rows()
        html_rows = []
        for r in reversed(rows):
            ts = escape(str(r.get('ts', '-')))
            status = escape(str(r.get('status', '-')))
            sale = escape(str(r.get('sale_number', '-')))
            msg = escape(str(r.get('message', '')))
            html_rows.append(f"<tr><td>{ts}</td><td>{sale}</td><td>{status}</td><td>{msg}</td></tr>")
        body = ''.join(html_rows) or '<tr><td colspan="4">Belum ada log.</td></tr>'
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Log Print Agent</title>
<style>
body{{font-family:Arial,sans-serif;background:#f1f5f9;margin:0;padding:24px;color:#0f172a}}
.card{{max-width:980px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px}}
table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid #e2e8f0;padding:8px;text-align:left;font-size:13px}}
a{{color:#1d4ed8;text-decoration:none}}
</style></head><body><div class="card">
<h2>Log Print</h2><p><a href="/">Kembali ke Home</a></p>
<table><thead><tr><th>Waktu</th><th>No Transaksi</th><th>Status</th><th>Pesan</th></tr></thead><tbody>{body}</tbody></table>
</div></body></html>"""

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == '/':
            self._html(200, self._render_home())
            return
        if self.path == '/health':
            self._json(200, {'success': True, 'service': 'print-agent', 'status': 'ok'})
            return
        if self.path.startswith('/logs'):
            self._html(200, self._render_logs())
            return
        self._json(404, {'success': False, 'message': 'not found'})

    def do_POST(self):
        if self.path != '/print':
            self._json(404, {'success': False, 'message': 'not found'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length).decode('utf-8')
            data = json.loads(body or '{}')
            printer_name = data.get('printer_name') or self.server.printer_name
            if not printer_name:
                raise ValueError('printer_name wajib diisi (payload atau env PRINTER_NAME).')
            copies = int(data.get('copies', 1))
            if copies <= 0:
                raise ValueError('copies harus > 0.')
            lines = data.get('lines', [])
            raw = escpos_text_lines(lines)
            simulate_mode = os.getenv('PRINT_SIMULATE', '').strip() == '1' or str(printer_name).upper().startswith('SIMULATOR')
            jobs = []
            artifacts = []
            for _ in range(copies):
                if simulate_mode:
                    artifacts.append(simulate_print(raw_bytes=raw, doc_name=data.get('sale_number', 'POS Receipt')))
                    jobs.append('SIMULATED')
                else:
                    jobs.append(raw_print(printer_name=printer_name, raw_bytes=raw, doc_name=data.get('sale_number', 'POS Receipt')))
            self._append_print_log(
                {
                    'ts': datetime.now().isoformat(timespec='seconds'),
                    'sale_number': data.get('sale_number', ''),
                    'status': 'success',
                    'message': 'printed' if not simulate_mode else 'simulated',
                    'printer_name': printer_name,
                    'copies': copies,
                }
            )
            self._json(
                200,
                {
                    'success': True,
                    'message': 'printed' if not simulate_mode else 'simulated',
                    'printer_name': printer_name,
                    'jobs': jobs,
                    'simulate_mode': simulate_mode,
                    'artifacts': artifacts,
                },
            )
        except Exception as exc:
            LOGGER.exception('print error')
            self._append_print_log(
                {
                    'ts': datetime.now().isoformat(timespec='seconds'),
                    'sale_number': data.get('sale_number', '') if 'data' in locals() and isinstance(data, dict) else '',
                    'status': 'failed',
                    'message': str(exc),
                }
            )
            self._json(400, {'success': False, 'message': str(exc)})

    def log_message(self, fmt, *args):
        LOGGER.info('%s - %s', self.address_string(), fmt % args)


class PrintAgentServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler, printer_name='', print_log_path=''):
        super().__init__(server_address, handler)
        self.printer_name = printer_name
        self.print_log_path = print_log_path


def run_forever(host='127.0.0.1', port=17971, printer_name=''):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    log_path = os.getenv('PRINT_AGENT_LOG_FILE', os.path.join(os.getcwd(), 'print-agent.log'))
    server = PrintAgentServer((host, int(port)), PrintHandler, printer_name=printer_name, print_log_path=log_path)
    LOGGER.info('Print agent listening on %s:%s printer=%s', host, port, printer_name or '(payload)')
    server.serve_forever()
