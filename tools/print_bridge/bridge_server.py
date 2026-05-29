import json
import logging
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

try:
    import win32print
except Exception:  # pragma: no cover
    win32print = None

LOGGER = logging.getLogger('koperasi_print_bridge')


def escpos_text_lines(lines):
    # Basic ESC/POS bytes: init, text lines, feed, cut.
    payload = bytearray()
    payload += b'\x1b@'  # initialize
    payload += b'\x1ba\x01'  # center for header line if any
    for i, line in enumerate(lines or []):
        if i == 1:
            payload += b'\x1ba\x00'  # back to left align
        text = str(line)
        payload += text.encode('cp437', errors='replace') + b'\n'
    payload += b'\n\n\n'
    payload += b'\x1dV\x00'  # full cut
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
        # Jika gagal tulis txt, tetap usahakan bin tersimpan
        with open(bin_path, 'wb') as f:
            f.write(raw_bytes)
    return {'txt_path': txt_path, 'bin_path': bin_path}


class PrintHandler(BaseHTTPRequestHandler):
    server_version = 'KoperasiPrintBridge/1.0'

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

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == '/health':
            self._json(200, {'success': True, 'service': 'print-bridge', 'status': 'ok'})
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
                    job_id = raw_print(printer_name=printer_name, raw_bytes=raw, doc_name=data.get('sale_number', 'POS Receipt'))
                    jobs.append(job_id)

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
            self._json(400, {'success': False, 'message': str(exc)})

    def log_message(self, fmt, *args):
        LOGGER.info('%s - %s', self.address_string(), fmt % args)


class PrintBridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler, printer_name=''):
        super().__init__(server_address, handler)
        self.printer_name = printer_name


def create_server(host='127.0.0.1', port=17971, printer_name=''):
    return PrintBridgeServer((host, int(port)), PrintHandler, printer_name=printer_name)


def run_forever(host='127.0.0.1', port=17971, printer_name=''):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    server = create_server(host=host, port=port, printer_name=printer_name)
    LOGGER.info('Print bridge listening on %s:%s printer=%s', host, port, printer_name or '(payload)')
    server.serve_forever()


if __name__ == '__main__':
    run_forever(
        host=os.getenv('PRINT_BRIDGE_HOST', '127.0.0.1'),
        port=int(os.getenv('PRINT_BRIDGE_PORT', '17971')),
        printer_name=os.getenv('PRINTER_NAME', ''),
    )
