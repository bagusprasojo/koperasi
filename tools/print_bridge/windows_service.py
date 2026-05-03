import os
import sys
import traceback
import servicemanager
import socket
import win32event
import win32service
import win32serviceutil

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from bridge_server import create_server


class KoperasiPrintBridgeService(win32serviceutil.ServiceFramework):
    _svc_name_ = 'KoperasiPrintBridge'
    _svc_display_name_ = 'Koperasi POS Thermal Print Bridge'
    _svc_description_ = 'HTTP bridge localhost untuk cetak ESC/POS thermal printer POS Koperasi.'

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None
        socket.setdefaulttimeout(2)
        self.log_file = os.path.join(CURRENT_DIR, 'service.log')

    def _log(self, message):
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(message + '\n')
        except Exception:
            pass

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        try:
            servicemanager.LogInfoMsg('KoperasiPrintBridge service starting')
            host = os.getenv('PRINT_BRIDGE_HOST', '127.0.0.1')
            port = int(os.getenv('PRINT_BRIDGE_PORT', '17971'))
            printer_name = os.getenv('PRINTER_NAME', '')
            self._log(f'start host={host} port={port} printer={printer_name}')
            self.server = create_server(host=host, port=port, printer_name=printer_name)
            self.server.serve_forever()
        except Exception as exc:
            msg = f'service error: {exc}\n{traceback.format_exc()}'
            self._log(msg)
            servicemanager.LogErrorMsg(msg)
            raise
        finally:
            servicemanager.LogInfoMsg('KoperasiPrintBridge service stopped')


if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(KoperasiPrintBridgeService)
