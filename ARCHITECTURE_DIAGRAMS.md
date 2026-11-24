# Walled DLP Architecture Diagrams

This file contains the editable Mermaid source and rendered SVGs for the Walled DLP desktop application's architecture and data flow. The diagrams reflect the current implementation: response styling is injected through a same-origin stylesheet, and the proxy leaves website Content Security Policy headers unchanged.

## Complete Architecture

![Complete Walled DLP architecture](docs/diagrams/walled-dlp-architecture.svg)

```mermaid
flowchart TB
    Browser["Browser<br/>Chrome / Edge / Firefox / Safari"]

    subgraph DesktopApp["Walled DLP Desktop Application"]
        App["app.py<br/>Application lifecycle"]
        Config["DLPConfig<br/>Marker, colors, proxy address,<br/>selective TLS host patterns"]
        UI["DesktopWindow<br/>Protection toggle, proxy controls,<br/>color picker, activity log"]
        Dialog["NativeConfirmDialog<br/>Continue / Cancel"]
        Proxy["ProxyServer<br/>Embedded mitmproxy"]
        Addon["MitmproxyDLPAddon<br/>HTTP flow snapshots and callbacks"]
        Engine["DLPEngine<br/>Inspection, enforcement,<br/>events and response styling"]
        ChatGPT["ChatGPTAdapter<br/>Prompt and upload extraction"]
        Perplexity["PerplexityAdapter<br/>Prompt and upload extraction"]
        Registry["UploadRegistry<br/>Presigned URL/key metadata<br/>with TTL"]
        Inspector["ContentInspector<br/>File classification and marker scan"]
        Text["Text scanner"]
        PDF["PDF extractor<br/>pypdf / pdfplumber"]
        Image["Image OCR<br/>Pillow / Tesseract"]
        Style["StyleInjector<br/>HTML link injection and CSS"]
        Events["DLP events<br/>Audit activity"]
        OSProxy["System proxy manager<br/>Optional OS proxy configuration"]
    end

    OpenAI["ChatGPT / OpenAI"]
    PPLX["Perplexity"]
    Storage["Upload storage<br/>oaiusercontent / Azure Blob / S3"]
    OtherHosts["Other HTTPS hosts"]

    App --> Config
    App --> Proxy
    App --> Engine
    App --> UI
    App --> Dialog
    UI -->|"inspection toggle and color"| Engine
    UI -->|"enable/disable"| OSProxy
    UI -->|"dispatch modal on UI thread"| Dialog
    OSProxy -. "optional system routing" .-> Browser
    Engine -->|"confirmation callback"| Dialog
    Engine -->|"event callback"| Events
    Events --> UI

    Browser -->|"HTTP(S) through configured proxy"| Proxy
    Proxy --> Addon
    Addon --> Engine
    Engine --> ChatGPT
    Engine --> Perplexity
    ChatGPT <--> Registry
    Perplexity <--> Registry
    ChatGPT -->|"prompt or file target"| Inspector
    Perplexity -->|"prompt or file target"| Inspector
    Inspector --> Text
    Inspector --> PDF
    Inspector --> Image

    Proxy -->|"selectively decrypted traffic"| OpenAI
    Proxy -->|"selectively decrypted traffic"| PPLX
    Proxy -->|"selectively decrypted upload traffic"| Storage
    Proxy -. "pass through without TLS decryption" .-> OtherHosts

    Addon -->|"response hook"| Engine
    Engine --> Style
    Style -->|"inject stylesheet link into supported-site HTML"| Addon
    Browser -->|"GET /__walled_dlp__/response-style.css"| Proxy
    Proxy --> Addon
    Addon --> Engine
    Engine -->|"serve generated CSS from memory"| Browser
```

## Data Flow

![Walled DLP request and response data flow](docs/diagrams/walled-dlp-data-flow.svg)

```mermaid
flowchart TD
    Start["Browser sends a prompt or starts a file upload"]
    Proxy["Local mitmproxy receives request"]
    Addon["Addon snapshots request and calls DLPEngine"]
    Enabled{"Inspection enabled?"}
    Bypass["Forward request unchanged"]
    Supported{"Owned by ChatGPT or Perplexity adapter?"}
    Extract["Adapter extracts prompt text or file bytes"]
    Target{"Inspection target found?"}
    NoTarget["Forward request unchanged"]

    Prompt["Prompt text"]
    File["Uploaded file bytes"]
    Type["ContentInspector classifies file<br/>by signature, MIME type, and extension"]
    TextScan["Text scanner"]
    PDFScan["PDF text extraction<br/>and OCR of embedded images"]
    ImageScan["Image OCR"]
    Fallback["Text/binary fallback scan"]
    Sensitive{"WALLED_SENSITIVE found?"}

    Clean["Forward original request upstream"]
    Dialog["Native confirmation dialog"]
    Decision{"User decision"}
    Continue["Continue: forward original request upstream"]
    Cancel["Cancel: return local HTTP 403<br/>without forwarding this request"]

    Init["Upload-init response observed"]
    InitResponse{"Upload-init response?"}
    Registry["UploadRegistry stores presigned URL/key<br/>and file metadata with TTL"]
    Upload["Later PUT or multipart upload"]
    Upstream["ChatGPT / Perplexity / upload storage"]
    Response["Upstream response"]
    HTML{"Supported-site HTML response?"}
    Inject["Inject same-origin stylesheet link<br/>leave CSP unchanged"]
    Page["Browser renders page and AI response"]
    CSSReq["Browser requests<br/>/__walled_dlp__/response-style.css"]
    CSS["Proxy serves generated CSS from memory"]
    Color["CSS applies selected light/dark response color"]
    Audit["Emit DLP event to desktop activity log"]

    Start --> Proxy --> Addon --> Enabled
    Enabled -- "No" --> Bypass
    Enabled -- "Yes" --> Supported
    Supported -- "No" --> Bypass
    Supported -- "Yes" --> Extract --> Target
    Target -- "No" --> NoTarget
    Target -- "Prompt" --> Prompt --> TextScan --> Sensitive
    Target -- "File" --> File --> Type
    Type -- "Text" --> TextScan
    Type -- "PDF" --> PDFScan
    Type -- "Image" --> ImageScan
    Type -- "Other/unknown" --> Fallback
    PDFScan --> Sensitive
    ImageScan --> Sensitive
    Fallback --> Sensitive

    Sensitive -- "No" --> Clean --> Upstream
    Sensitive -- "Yes" --> Dialog --> Decision
    Decision -- "Continue" --> Continue --> Upstream
    Decision -- "Cancel" --> Cancel

    NoTarget --> Upstream
    Upstream --> Response
    Response --> InitResponse
    InitResponse -- "Yes" --> Init --> Registry
    Registry -. "metadata identifies later upload" .-> Upload
    Upload --> Proxy

    Clean --> Audit
    Continue --> Audit
    Cancel --> Audit
    Response --> HTML
    HTML -- "No" --> Page
    HTML -- "Yes" --> Inject --> Page
    Page --> CSSReq --> CSS --> Color
```
