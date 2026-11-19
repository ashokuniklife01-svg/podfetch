import http from 'http'; import fs from 'fs'; import path from 'path';
const root = '/workspace/mediatools', port = +process.argv[2], iso = process.argv[3] === 'iso';
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.wasm': 'application/wasm', '.json': 'application/json' };
http.createServer((q, r) => {
  let p = decodeURIComponent(q.url.split('?')[0]); if (p.endsWith('/')) p += 'index.html';
  const f = path.join(root, p); if (!f.startsWith(root) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
  const h = { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' };
  if (iso) { h['Cross-Origin-Opener-Policy'] = 'same-origin'; h['Cross-Origin-Embedder-Policy'] = 'require-corp'; }
  r.writeHead(200, h); fs.createReadStream(f).pipe(r);
}).listen(port, '127.0.0.1');
