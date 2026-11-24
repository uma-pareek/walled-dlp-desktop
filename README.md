# Walled DLP — Standalone Desktop GenAI Data Loss Prevention

A lightweight, standalone desktop Data Loss Prevention (DLP) application that monitors interactions with **ChatGPT Web** (`https://chatgpt.com`) and **Perplexity Web** (`https://www.perplexity.ai`) accessed through any standard web browser — with **no browser extensions, plugins, or add-ons**.

## Features

- **Pre-Transmission Prompt & File Inspection**: Inspects outgoing text prompts and uploaded files (`.txt`/code/text, `.pdf`, and images via OCR) for the sensitive marker `WALLED_SENSITIVE` before any bytes leave the device.
- **Native Desktop Confirmation Dialog**: Displays an OS-native modal dialog (`osascript` on macOS, `zenity`/Tkinter on Linux, native topmost Tkinter on Windows) identifying whether the detection occurred in a **Text Prompt** or **File Upload**, displaying the prompt snippet or file name, and offering **Continue** and **Cancel** actions.
- **Clean Enforcement**:
  - **Clean content**: Passes through automatically with zero interruption.
  - **Continue**: Forwards the original prompt or file unmodified.
  - **Cancel**: Blocks transmission locally (HTTP `403`) so the user can edit, retry, or re-upload normally.
- **Consistent AI Response Text Color**: Injects a same-origin stylesheet (`/__walled_dlp__/response-style.css`) into both ChatGPT and Perplexity to customize the text color of all AI-generated responses in both light and dark themes.
- **Selective Interception**: Only decrypts traffic for ChatGPT, Perplexity, and their upload storage hosts; all other browsing traffic passes through untouched.

## Prerequisites

1. **Python 3.10+** (with `tkinter` included in standard macOS/Windows Python installers; on Debian/Ubuntu run `sudo apt install python3-tk`).
2. **Tesseract OCR** (required for scanning uploaded image files and scanned PDFs):
   - **macOS (Homebrew)**: `brew install tesseract`
   - **Ubuntu / Debian**: `sudo apt install tesseract-ocr`
   - **Windows**: Install from [UB-Mannheim Tesseract installer](https://github.com/UB-Mannheim/tesseract/wiki) and ensure `tesseract.exe` is on your `PATH`.

## Setup

```bash
python3 -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## Running the Application

### Option 1: Desktop GUI Mode (Recommended)

```bash
python3 main.py
```

Or with automatic OS system proxy setup (automatically restored on exit):

```bash
python3 main.py --auto-proxy
```

### Option 2: Headless Mode (Native Dialogs Only)

```bash
python3 main.py --headless --auto-proxy
```

### CLI Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--host` | `127.0.0.1` | Local proxy bind host |
| `--port` | `8080` | Local proxy listen port |
| `--marker` | `WALLED_SENSITIVE` | Sensitive marker to detect |
| `--color` | *(auto light/dark teal)* | Custom hex color override for AI responses (e.g., `"#4338CA"`) |
| `--auto-proxy` | `false` | Automatically configure and restore the OS HTTP/HTTPS proxy |
| `--headless` | `false` | Run without the dashboard window (native dialogs still appear) |
| `--verbose` | `false` | Enable debug logging |

## One-Time Browser & Certificate Setup

Because Walled DLP inspects HTTPS traffic locally without a browser extension, trust the local `mitmproxy` root CA certificate once:

1. Start Walled DLP (`python3 main.py`).
2. Either click **Enable System Proxy** in the app window, or launch a browser pointed at the proxy:
   - **macOS Chrome**:
     ```bash
     /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --proxy-server="http://127.0.0.1:8080"
     ```
   - **Windows Chrome**:
     ```powershell
     & "C:\Program Files\Google\Chrome\Application\chrome.exe" --proxy-server="http://127.0.0.1:8080"
     ```
   - **Linux Chrome**:
     ```bash
     google-chrome --proxy-server="http://127.0.0.1:8080"
     ```
3. Visit `http://mitm.it` in that browser and install/trust the certificate for your OS (or install `~/.mitmproxy/mitmproxy-ca-cert.pem` directly into your OS/browser trust store).
4. Open `https://chatgpt.com` and `https://www.perplexity.ai` normally.

## Running Automated Tests

The repository includes sample test fixtures in `samples/` and an end-to-end test suite covering all 9 acceptance criteria (including real PDF extraction and Tesseract OCR):

```bash
python3 -m unittest discover -s tests -v
```

To regenerate the sample fixtures (requires `reportlab`):

```bash
python3 scripts/generate_samples.py
```

## Demo Recording Checklist

Use the bundled files in `samples/` to record a quick demonstration on both **ChatGPT** and **Perplexity**:

1. **AI Response Color**: Submit a clean prompt (e.g., `"Explain what DLP means in one sentence."`) — confirm no dialog appears and the AI response renders in the customized teal color.
2. **Sensitive Prompt (Cancel -> Continue)**: Submit `"Draft a summary for WALLED_SENSITIVE Q4 roadmap"` — verify the native desktop dialog appears showing **Text Prompt**, click **Cancel** (blocked), then retry and click **Continue** (submitted unmodified).
3. **Sensitive Text File**: Upload `samples/sensitive_notes.txt` — verify the native dialog shows **File Upload** and `sensitive_notes.txt`. Test **Cancel** and **Continue**.
4. **Sensitive PDF File**: Upload `samples/sensitive_report.pdf` — verify the native dialog triggers before upload.
5. **Sensitive Image File (OCR)**: Upload `samples/sensitive_screenshot.png` — verify OCR detects `WALLED_SENSITIVE` and triggers the native dialog before upload.
6. **Repeat on Perplexity**: Run the same prompt, file upload, and AI response color verification on `https://www.perplexity.ai`.
