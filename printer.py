"""
FlashForge Adventurer 5M client.

Two transports:
  * HTTP API on port 8898 (firmware 2.6.5+ / "new" API). Needs serial number
    and check code, both shown on the printer screen.
  * Legacy TCP protocol on port 8899. Needs nothing but the IP, but is slower
    and does not report progress.

The HTTP path is tried first; TCP is the fallback.
"""

import socket
import requests

HTTP_PORT = 8898
TCP_PORT = 8899
TCP_CHUNK = 4096


class PrinterError(Exception):
    pass


class FlashForgePrinter:
    def __init__(self, host, serial_number=None, check_code=None, timeout=15):
        self.host = host
        self.serial_number = serial_number
        self.check_code = check_code
        self.timeout = timeout

    # ---------- HTTP API (preferred) ----------

    @property
    def _http_creds(self):
        return {
            "serialNumber": self.serial_number or "",
            "checkCode": self.check_code or "",
        }

    def _http_base(self):
        return f"http://{self.host}:{HTTP_PORT}"

    def http_available(self):
        return bool(self.serial_number and self.check_code)

    def detail(self):
        """Live printer status. Also doubles as a credential check."""
        r = requests.post(
            f"{self._http_base()}/detail",
            json=self._http_creds,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if str(data.get("code")) not in ("0", "200"):
            raise PrinterError(data.get("message") or "Printer rejected the request")
        return data.get("detail", data)

    def upload_http(self, filename, payload, print_now=False, level_first=False):
        files = {
            "gcodeFile": (filename, payload, "application/octet-stream"),
        }
        form = dict(self._http_creds)
        form["fileSize"] = str(len(payload))
        form["printNow"] = "true" if print_now else "false"
        form["levelingBeforePrint"] = "true" if level_first else "false"

        r = requests.post(
            f"{self._http_base()}/uploadGcode",
            data=form,
            files=files,
            timeout=max(self.timeout, 300),
            headers={"Expect": ""},
        )
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            raise PrinterError(f"Unexpected reply from printer: {r.text[:200]}")
        if str(data.get("code")) not in ("0", "200"):
            raise PrinterError(data.get("message") or "Upload refused by printer")
        return data

    # ---------- Legacy TCP (fallback) ----------

    def _tcp_cmd(self, sock, command, expect_reply=True):
        sock.sendall(f"~{command}\r\n".encode())
        if not expect_reply:
            return b""
        sock.settimeout(self.timeout)
        try:
            return sock.recv(1024)
        except socket.timeout:
            return b""

    def upload_tcp(self, filename, payload, print_now=False):
        remote = f"0:/user/{filename}"
        with socket.create_connection((self.host, TCP_PORT), timeout=self.timeout) as s:
            self._tcp_cmd(s, "M601 S1")          # take control
            self._tcp_cmd(s, f"M28 {len(payload)} {remote}")  # begin write

            for i in range(0, len(payload), TCP_CHUNK):
                s.sendall(payload[i:i + TCP_CHUNK])

            self._tcp_cmd(s, "M29")              # end write
            if print_now:
                self._tcp_cmd(s, f"M23 {remote}")
            self._tcp_cmd(s, "M602", expect_reply=False)  # release control
        return {"transport": "tcp", "file": remote}

    # ---------- Dispatcher ----------

    def upload(self, filename, payload, print_now=False, level_first=False):
        if self.http_available():
            try:
                result = self.upload_http(filename, payload, print_now, level_first)
                result["transport"] = "http"
                return result
            except (requests.RequestException, PrinterError) as exc:
                last = str(exc)
        else:
            last = "No serial number or check code set"

        try:
            return self.upload_tcp(filename, payload, print_now)
        except OSError as exc:
            raise PrinterError(
                f"Both transports failed. HTTP: {last}. TCP: {exc}"
            )
