"""Local-only form bridge for DOM evidence captured through the supported browser tool.

Does not connect to Xiaohongshu, read cookies, or drive a browser.
"""
from __future__ import annotations
import argparse
import json
import secrets
import hashlib
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

KINDS = {'note_dom', 'creator_dom', 'audience_dom', 'search_dom', 'access_probe', 'visual_annotation'}

def validate_records(data):
    if not isinstance(data, list) or not data:
        raise ValueError('Expected a non-empty array of capture records')
    if any(not isinstance(row, dict) or row.get('kind') not in KINDS for row in data):
        raise ValueError('Unsupported capture record')
    serialized = json.dumps(data, ensure_ascii=False)
    forbidden = ('xsec_token=', 'web_session', '"cookies"', '"password"', '"authorization"')
    if any(term in serialized.lower() for term in forbidden):
        raise ValueError('Authentication material must not be saved')

def record_hash(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

class CaptureStore:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.seen = set()
        for path in self.output.glob('capture-*.json'):
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            rows = data if isinstance(data, list) else data.get('records', [data])
            self.seen.update(record_hash(row) for row in rows)

    def save(self, data):
        validate_records(data)
        with self.lock:
            fresh = []
            batch_seen = set(self.seen)
            for row in data:
                key = record_hash(row)
                if key not in batch_seen:
                    fresh.append(row)
                    batch_seen.add(key)
            if not fresh:
                return 0, None
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            filename = f'capture-{stamp}-{secrets.token_hex(3)}.json'
            target=self.output / filename
            temporary=self.output / (filename+'.tmp')
            temporary.write_text(json.dumps(fresh, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            temporary.replace(target)
            self.seen = batch_seen
            return len(fresh), filename


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'data' / 'private' / 'browser-captures')
    args = parser.parse_args()
    output = args.output.resolve()
    workspace = Path(__file__).parent.resolve()
    if not output.is_relative_to(workspace):
        raise SystemExit('Capture output must remain inside this project.')
    output.mkdir(parents=True, exist_ok=True)
    csrf = secrets.token_urlsafe(24)
    origin = f'http://127.0.0.1:{args.port}'
    store = CaptureStore(output)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: str) -> None:
            data = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != '/':
                self.reply(404, 'Not found')
                return
            self.reply(200, f'''<!doctype html><meta charset="utf-8"><title>研究证据本地保存</title>
            <style>body{{font:16px system-ui;max-width:900px;margin:48px auto}}textarea{{width:100%;height:400px}}button{{padding:12px 24px}}</style>
            <h1>研究证据本地保存</h1><p>只写入本机项目私有数据目录。输入中不得包含 Cookie、凭证或站点访问令牌。</p>
            <form method="post" action="/capture"><input type="hidden" name="csrf" value="{csrf}">
            <label for="payload">DOM 证据 JSON</label><textarea id="payload" name="payload" required></textarea>
            <p><button type="submit">保存研究证据</button></p></form>''')

        def do_POST(self) -> None:
            if self.path != '/capture' or self.headers.get('Origin') != origin:
                self.reply(403, 'Rejected origin or route')
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8_000_000:
                self.reply(413, 'Invalid payload size')
                return
            try:
                fields = parse_qs(self.rfile.read(size).decode('utf-8'), strict_parsing=True)
                if not secrets.compare_digest(fields.get('csrf', [''])[0], csrf):
                    raise ValueError('Invalid form token')
                data = json.loads(fields['payload'][0])
                added, filename = store.save(data)
                self.reply(200, f'<meta charset="utf-8"><title>证据已保存</title><h1>证据已保存</h1><p>{added} 条新增记录</p><p>{filename or "整批重复，无新增文件"}</p><a href="/">继续保存</a>')
            except (ValueError, KeyError, IndexError, UnicodeDecodeError) as exc:
                self.reply(400, '<meta charset="utf-8"><h1>未保存</h1><p>输入格式或去凭证检查未通过，请核对。</p>')

        def log_message(self, *_args: object) -> None:
            pass

    print(f'Capture bridge: {origin}', flush=True)
    print(f'Private output: {output}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
