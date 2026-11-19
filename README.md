# UNIK Tools

Free browser-based media tools for the UNIK Life team:

1. **Video to MP3** – ffmpeg.wasm (libmp3lame, 128/192/320 kbps)
2. **Video Compressor** – ffmpeg.wasm (H.264 CRF 23/28/33 + optional 1080/720/480/360p downscale, before/after size)
3. **Images to PDF** – jsPDF (multi-image, reorder/rotate/remove, A4/Letter/Legal/fit, orientation, margins)
4. **Background Remover** – @imgly/background-removal (isnet_quint8 model, transparent/white/colour background PNG)

100% static, no backend: files never leave the user's device. All libraries and the AI model are self-hosted under `vendor/` (no CDN at runtime).

## Deploy (Render static site)
- Build command: `echo ok` · Publish directory: `./`
- `render.yaml` adds COOP/COEP headers (enables multi-threaded WASM for faster background removal) and caching for `vendor/`. The site also works without these headers.

## Vendored versions
@ffmpeg/ffmpeg 0.12.15, @ffmpeg/core 0.12.10 (single-thread ESM), jsPDF 4.2.1, @imgly/background-removal 1.7.0 (bundled with esbuild; `maxNumThreads` patched to use 1 thread unless crossOriginIsolated), onnxruntime-web 1.21.0 wasm + isnet_quint8 from @imgly/background-removal-data 1.7.0.
