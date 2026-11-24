# Architecture Overview — Walled DLP Desktop Application

## 1. Design Goals & Key Architectural Choice

The assignment requires a **standalone desktop application** (strictly no browser extensions, plugins, or add-ons) that inspects outgoing prompts and file uploads on **ChatGPT Web** (`https://chatgpt.com`) and **Perplexity Web** (`https://www.perplexity.ai`) **before transmission**, shows a **native desktop confirmation dialog**, enforces **Continue / Cancel**, and modifies the **text color of all AI-generated responses**.

To satisfy all requirements reliably across any standard desktop browser (Chrome, Edge, Firefox, Safari) without breaking OAuth logins or bot protection, Walled DLP uses a **local selective HTTPS intercepting proxy** embedded inside a **native Python desktop application**:

```text
Standard Browser (Chrome / Edge / Firefox / Safari)
        │  HTTPS (chatgpt.com, perplexity.ai, upload buckets)
        ▼
┌─────────────────────────────────────────────────────────────┐
│  Walled DLP Desktop Application (single Python process)     │
│                                                             │
│  ┌───────────────────────┐    ┌──────────────────────────┐  │
│  │ Embedded mitmproxy    │───▶│ DLPEngine                │  │
│  │ (Selective TLS via    │    │  • ChatGPTAdapter        │  │
│  │  allow_hosts)         │    │  • PerplexityAdapter     │  │
│  └───────────────────────┘    │  • UploadRegistry (TTL)  │  │
│                               │  • StyleInjector (CSS)   │  │
│                               └──────────┬───────────────┘  │
│                                          ▼                  │
│  ┌───────────────────────┐    ┌──────────────────────────┐  │
│  │ Native Desktop UI     │◀───│ ContentInspector         │  │
│  │  • DesktopWindow (Tk) │    │  • Plain-Text Scanner    │  │
│  │  • NativeConfirmDialog│    │  • PDF Extractor (pypdf/ │  │
│  │    (osascript /       │    │    pdfplumber + OCR)     │  │
│  │     zenity / Tkinter) │    │  • Image OCR (Pillow +   │  │
│  └───────────────────────┘    │    Tesseract)            │  │
│                               └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
        │  Only forwarded upstream if clean OR user clicked Continue
        ▼
   Upstream Servers (OpenAI / Perplexity / Azure Blob / S3)
```

## 2. Module Breakdown

| Module | Responsibility |
| :--- | :--- |
| `walled_dlp/config.py` | Central `DLPConfig` (marker, proxy bind host/port, response colors, selective TLS host patterns). |
| `walled_dlp/models.py` | Domain types (`InspectionTarget`, `InspectionResult`, `DLPEvent`, `UserDecision`, `ActionOutcome`). |
| `walled_dlp/adapters/chatgpt.py` | ChatGPT prompt extraction, upload-init correlation, and Azure Blob / `oaiusercontent.com` file upload interception. |
| `walled_dlp/adapters/perplexity.py` | Perplexity prompt extraction, upload-init correlation, and S3 multipart/PUT file upload interception. |
| `walled_dlp/adapters/upload_registry.py` | Thread-safe TTL cache mapping presigned upload URLs and S3 keys back to original filenames and MIME types. |
| `walled_dlp/adapters/multipart.py` | Pure-stdlib `multipart/form-data` parser (`email.parser.BytesParser`) for extracting uploaded file bytes and metadata. |
| `walled_dlp/inspection/detector.py` | Magic-byte + MIME + extension router dispatching payloads to text, PDF, or image scanners. |
| `walled_dlp/inspection/text_scanner.py` | UTF-8 / UTF-16 / Latin-1 text decoder and marker matcher. |
| `walled_dlp/inspection/pdf_scanner.py` | Extracts text with `pypdf`, falls back to `pdfplumber`, and runs OCR on embedded raster images for scanned PDFs. |
| `walled_dlp/inspection/image_scanner.py` | Preprocesses images (alpha compositing, upscaling small screenshots, grayscale autocontrast) and runs Tesseract OCR + metadata checks. |
| `walled_dlp/proxy/engine.py` | Framework-agnostic `DLPEngine` coordinating adapters, inspection, user confirmation, enforcement, and response styling. |
| `walled_dlp/proxy/addon.py` | Thin `mitmproxy` bridge using `@concurrent` so awaiting user confirmation never stalls other browser requests. |
| `walled_dlp/proxy/style_injector.py` | Serves the virtual same-origin stylesheet `/__walled_dlp__/response-style.css` and injects `<link>` into HTML responses. |
| `walled_dlp/proxy/system_proxy.py` | Cross-platform OS proxy configuration and automatic cleanup on exit (`atexit` + signal handlers). |
| `walled_dlp/ui/dialog.py` | Native desktop confirmation dialog (`osascript` on macOS, `zenity` on Linux, topmost Tkinter modal on Windows/fallback). |
| `walled_dlp/ui/desktop_window.py` | Native Tkinter desktop dashboard with protection toggle, system proxy controls, response color picker, and live audit log. |

## 3. Prompt & File Interception Flow

1. **Selective TLS Interception**: `mitmproxy` is configured with `allow_hosts` restricted to ChatGPT, Perplexity, and their file upload storage domains (`*.oaiusercontent.com`, `*.blob.core.windows.net`, `ppl-ai-file-upload.s3*.amazonaws.com`). All other websites bypass TLS decryption completely.
2. **Text Prompts**:
   - **ChatGPT**: Intercepts `POST /backend-api/f/conversation` (and `/backend-api/conversation`), extracts all text parts from `messages[].content.parts` (plus any inline base64 data URIs), and scans for `WALLED_SENSITIVE`.
   - **Perplexity**: Intercepts `POST /rest/sse/perplexity_ask` (and `/api/ask`), extracts `query_str` / `params.query_str`, and scans for `WALLED_SENSITIVE`.
3. **Two-Step File Upload Correlation**:
   - Modern GenAI web apps first request a presigned cloud storage URL and then upload raw file bytes directly to Azure Blob (ChatGPT) or Amazon S3 (Perplexity).
   - In `observe_response()`, the site adapters inspect `POST /backend-api/files` (ChatGPT) and `POST /rest/uploads/batch_create_upload_urls` (Perplexity), storing the returned `upload_url` / S3 `key` alongside the original `filename` in `UploadRegistry`.
   - When the browser subsequently sends the `PUT` (ChatGPT) or `multipart/form-data` `POST` (Perplexity) carrying the file bytes, the adapter intercepts it **before transmission**, attaches the original filename, and passes the raw bytes to `ContentInspector`.
4. **Content Inspection**:
   - Magic bytes (`%PDF-`, PNG/JPEG/GIF/BMP/TIFF/WebP headers) take precedence over file extensions so renamed files are still scanned accurately.
   - Plain text files are decoded directly; PDFs are parsed with `pypdf` / `pdfplumber` (plus OCR on embedded images); images are preprocessed with Pillow and scanned via Tesseract OCR.

## 4. Enforcement & Native Confirmation

- **No marker found**: `DLPEngine.process_request()` returns `allow=True`, forwarding the request immediately without modifying a single byte.
- **Marker detected**:
  - The request worker pauses inside `@concurrent request()` while `NativeConfirmDialog.ask()` displays an OS-native dialog with the detection source (**Text Prompt** or **File Upload**), prompt snippet or file name, and **Continue** / **Cancel** buttons.
  - **Continue**: The request is released unmodified to the upstream server.
  - **Cancel**: The proxy immediately synthesizes a local HTTP `403 Forbidden` JSON response with appropriate CORS headers. Zero bytes of the sensitive payload leave the machine, and the web application handles the rejected request gracefully so the user can edit or retry.

## 5. AI Response Text Color Customization

To customize AI-generated response text color reliably without violating strict `Content-Security-Policy` headers (which often block inline `<style>` tags without nonces):

1. When an HTML document (`text/html`) returns from `chatgpt.com` or `www.perplexity.ai`, `StyleInjector` inserts `<link rel="stylesheet" id="walled-dlp-response-style" href="/__walled_dlp__/response-style.css" />` before `</head>` and ensures `style-src` includes `'self'`.
2. When the browser requests `/__walled_dlp__/response-style.css` from the same origin, `DLPEngine` serves the generated CSS directly from memory.
3. The CSS targets assistant response containers on ChatGPT (`[data-message-author-role="assistant"]`, `article[data-turn="assistant"]`) and AI answer containers on Perplexity (`.prose`, `[data-testid="answer"]`, `.markdown-content`), adapting automatically to light and dark themes (or applying any custom color selected in the desktop app).
