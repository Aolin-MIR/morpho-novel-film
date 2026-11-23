"""Serve the gallery or the read-only production desk on a company network interface."""
import argparse
import http.client
import ipaddress
import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


SLUG = r'[a-z0-9][a-z0-9-]{0,74}'
STUDY_LABELS_ROUTE = '/api/review-films/study-labels'
PUBLIC = re.compile(
    rf'(?:/screenings(?:/{SLUG}(?:/(?:film\.mp4|poster\.jpg|director\.vtt))?)?'
    rf'|/review-films(?:/review-[a-z0-9-]{{1,65}}(?:/(?:film\.mp4|poster\.jpg|director\.vtt))?)?'
    rf'|/api/review-films(?:/(?:studies|methods|review-[a-z0-9-]{{1,65}}))?'
    rf'|/api/screenings(?:/{SLUG})?|/studio\.css|/screenings\.(?:css|js))'
)
PRODUCTION = re.compile(
    rf'(?:/studio(?:\.js)?|/api/state|/api/shots/[a-f0-9]{{24}}|/review/[a-f0-9]{{24}}'
    rf'|/media/[a-f0-9]{{24}}|/art/(?:stage|village)\.jpg'
    rf'|/studies/(?:five-spice|subway|tile-cat)\.jpg'
    rf'|/archive/screenings(?:/{SLUG}(?:/(?:film\.mp4|poster\.jpg|director\.vtt))?)?'
    rf'|/api/archive/screenings(?:/{SLUG})?)'
)
FORWARD_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges',
                   'Cache-Control', 'Last-Modified', 'ETag')


class Gallery(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_HEAD(self):
        self.serve_gallery()

    def do_GET(self):
        self.serve_gallery()

    def do_POST(self):
        if self.server.production:
            self.send_error(405)
            return
        if urlsplit(self.path).path != STUDY_LABELS_ROUTE:
            self.send_error(404)
            return
        address, port = self.server.server_address
        length = self.headers.get('Content-Length', '')
        if (self.headers.get('Origin') != f'http://{address}:{port}'
                or self.headers.get_content_type() != 'application/json'
                or not length.isascii() or not length.isdecimal() or len(length) > 4
                or not 1 <= int(length) <= 4096):
            self.send_error(403)
            return
        body = self.rfile.read(int(length))
        self.forward(STUDY_LABELS_ROUTE, {'Content-Type': 'application/json',
                                         'Origin': f'http://127.0.0.1:{self.server.studio_port}'}, body)

    def serve_gallery(self):
        path = urlsplit(self.path).path
        if path == '/':
            self.send_response(302)
            self.send_header('Location', '/studio' if self.server.production else '/screenings')
            self.end_headers()
            return
        if self.server.production and PRODUCTION.fullmatch(path):
            headers = {'Range': self.headers['Range']} if 'Range' in self.headers else {}
            self.forward('/' if path == '/studio' else path, headers)
            return
        if not PUBLIC.fullmatch(path):
            if not re.fullmatch(r'/(?:study-mixes/[a-f0-9]{24}\.mp4|studies/[a-z0-9-]+\.mp4|library-studies/[a-z0-9-]+\.mp4|media/[a-f0-9]{24})', path):
                self.send_error(404)
                return
            catalog = http.client.HTTPConnection('127.0.0.1', self.server.studio_port, timeout=8)
            try:
                catalog.request('GET', '/api/review-films/studies' if path.startswith('/library-studies/') else '/api/screenings/studies')
                result = catalog.getresponse()
                if result.status != 200:
                    self.send_error(503)
                    return
                if path not in {urlsplit(row['playback']).path for row in json.load(result)}:
                    self.send_error(404)
                    return
            except (OSError, ValueError, http.client.HTTPException):
                self.send_error(503)
                return
            finally:
                catalog.close()
        headers = {'Range': self.headers['Range']} if 'Range' in self.headers else {}
        self.forward(path, headers)

    def forward(self, path, headers, body=None):
        upstream = http.client.HTTPConnection('127.0.0.1', self.server.studio_port, timeout=40)
        started = False
        try:
            upstream.request(self.command, path, body=body, headers=headers)
            response = upstream.getresponse()
            self.send_response(response.status)
            for key in FORWARD_HEADERS:
                if (value := response.getheader(key)) is not None:
                    self.send_header(key, value)
            if response.status in (301, 302, 307, 308):
                location = response.getheader('Location', '')
                if PUBLIC.fullmatch(location):
                    self.send_header('Location', location)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            started = True
            if self.command != 'HEAD':
                while block := response.read(128 * 1024):
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            if not started:
                self.send_error(502)
        except (socket.timeout, OSError, http.client.HTTPException):
            if not started:
                self.send_error(503)
        finally:
            upstream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', required=True, type=ipaddress.IPv4Address)
    parser.add_argument('--port', type=int, default=18825)
    parser.add_argument('--studio-port', type=int, default=18824)
    parser.add_argument('--studio', action='store_true', help='serve the read-only production desk')
    args = parser.parse_args()
    if args.bind not in ipaddress.ip_network('10.0.0.0/8'):
        parser.error('请使用公司内网网卡的地址。')
    with ThreadingHTTPServer((str(args.bind), args.port), Gallery) as server:
        server.studio_port = args.studio_port
        server.production = args.studio
        server.serve_forever()


if __name__ == '__main__':
    main()
