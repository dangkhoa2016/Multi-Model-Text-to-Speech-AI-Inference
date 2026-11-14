"""Static bilingual introduction for a self-hosted Text-to-Speech inference gateway.

The page contains no runtime configuration, credentials, third-party assets,
JavaScript, private model metadata, or GPU calls.
"""

from html import escape
from string import Template

PROJECT_URL = "https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference"

COPY = {
    "en": {
        "language_switch": "Tiếng Việt",
        "nav_models": "Models",
        "nav_start": "Get started",
        "nav_health": "Gateway health",
        "eyebrow": "SELF-HOSTED · TEXT-TO-SPEECH API",
        "headline": "Three speech models.",
        "headline_accent": "One secure gateway.",
        "introduction": (
            "Build natural speech into your applications through an authenticated, "
            "asynchronous API with model-specific controls and encrypted job storage."
        ),
        "source_link": "Explore on GitHub",
        "documentation_link": "Read API documentation",
        "notice": (
            "For self-hosted integrations, not an open voice-generation website. "
            "Job requests and model metadata require a Bearer token. "
            "Gateway health does not prove that a GPU or model is ready."
        ),
        "architecture": "ARCHITECTURE",
        "client": "Your application",
        "gateway": "CPU API gateway",
        "gpu": "On-demand GPU workers",
        "terminal": "API OVERVIEW",
        "terminal_note": "Public landing and health · Bearer required for inference",
        "model_heading": "Three models. Individual controls.",
        "model_lead": (
            "Choose the speech model that fits your application. Options, "
            "licenses and deployment availability are specific to each model."
        ),
        "moss_note": "Multilingual speech, voice and generation controls",
        "higgs_note": "Emotion, style and expression · separate license conditions",
        "voxcpm_note": "Voice design and authorized reference speech",
        "steps_heading": "Start building",
        "steps_lead": "Deploy a secure gateway and connect a client without exposing your model runtime.",
        "step1": "Configure the gateway",
        "step1_body": "Set up Modal, PostgreSQL, Redis, private credentials and audited model artifacts.",
        "step2": "Inspect model options",
        "step2_body": "Use a Bearer token to access validated per-model JSON Schemas.",
        "step3": "Submit and poll",
        "step3_body": "Send an idempotent job, poll its status and download the generated WAV.",
        "deployment": "Deployment guide",
        "api": "API reference",
        "security": "Security and privacy",
        "footer": "Project code is MIT licensed. Model weights and dependencies have separate terms.",
    },
    "vi": {
        "language_switch": "English",
        "nav_models": "Các model",
        "nav_start": "Bắt đầu",
        "nav_health": "Trạng thái gateway",
        "eyebrow": "API TỔNG HỢP GIỌNG NÓI · TỰ TRIỂN KHAI",
        "headline": "Ba model giọng nói.",
        "headline_accent": "Một gateway bảo mật.",
        "introduction": (
            "Tích hợp tổng hợp giọng nói qua API bất đồng bộ có xác thực, "
            "tùy chỉnh riêng từng model và lưu trữ job bằng dữ liệu mã hóa."
        ),
        "source_link": "Xem mã nguồn GitHub",
        "documentation_link": "Đọc tài liệu API",
        "notice": (
            "Dành cho ứng dụng tự triển khai, không phải website tạo giọng công khai. "
            "Yêu cầu inference và metadata model cần Bearer token. "
            "Gateway health không xác nhận GPU hay model đã sẵn sàng."
        ),
        "architecture": "KIẾN TRÚC",
        "client": "Ứng dụng của bạn",
        "gateway": "CPU API gateway",
        "gpu": "GPU khi có yêu cầu",
        "terminal": "TỔNG QUAN API",
        "terminal_note": "Landing và health công khai · inference cần Bearer token",
        "model_heading": "Ba model. Bộ tùy chỉnh riêng.",
        "model_lead": (
            "Chọn model giọng nói phù hợp với ứng dụng của bạn. "
            "Tùy chọn, giấy phép và khả năng triển khai phụ thuộc từng model."
        ),
        "moss_note": "Giọng nói đa ngôn ngữ và tham số sinh âm thanh",
        "higgs_note": "Cảm xúc, phong cách và biểu cảm · giấy phép riêng",
        "voxcpm_note": "Thiết kế giọng nói và sử dụng giọng tham chiếu hợp lệ",
        "steps_heading": "Bắt đầu sử dụng",
        "steps_lead": "Triển khai gateway bảo mật và kết nối client mà không công khai runtime của model.",
        "step1": "Cấu hình gateway",
        "step1_body": "Chuẩn bị Modal, PostgreSQL, Redis, credentials riêng và model đã kiểm toán.",
        "step2": "Đọc tùy chọn model",
        "step2_body": "Dùng Bearer token để xem JSON Schema của từng model.",
        "step3": "Gửi và theo dõi job",
        "step3_body": "Gửi job idempotent, lấy trạng thái và tải WAV sau khi hoàn tất.",
        "deployment": "Hướng dẫn triển khai",
        "api": "Tài liệu API",
        "security": "Bảo mật và quyền riêng tư",
        "footer": "Mã nguồn dự án theo MIT. Trọng số model và dependencies có điều khoản riêng.",
    },
}

PAGE = Template("""<!doctype html>
<html lang="$language">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0b1424">
<meta name="description" content="$introduction">
<title>Text-to-Speech AI Inference · API</title>
<style>
:root{color-scheme:dark;--bg:#0b1424;--panel:#14243a;--line:#334a63;--text:#f0f6ff;--muted:#a7bdd5;--mint:#8cf4d3}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:radial-gradient(ellipse 70% 36% at 95% -8%,#31548583,transparent),radial-gradient(ellipse 56% 43% at -15% 36%,#195f6280,transparent),var(--bg);color:var(--text);font:16px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:inherit;text-decoration:none}a:focus-visible{outline:3px solid var(--mint);outline-offset:4px;border-radius:5px}
.shell{max-width:1180px;margin:auto;padding:0 28px}
header{border-bottom:1px solid #ffffff1b}
nav{min-height:80px;display:flex;align-items:center;justify-content:space-between;gap:18px}
.brand{display:flex;align-items:center;gap:12px;font-weight:790;font-size:15px;line-height:1.3}
.brand-mark{width:40px;height:40px;display:grid;place-items:center;border:1px solid #69d9c9a0;background:linear-gradient(140deg,#184c60,#26375b);border-radius:13px;color:var(--mint);font-size:24px}
.links{display:flex;gap:22px;align-items:center;font-size:13px;color:#d3e0ef}
.links a:hover,footer a:hover{color:var(--mint)}
.language-switch{padding:8px 13px;border:1px solid #607991;border-radius:999px;white-space:nowrap}
.hero{display:grid;grid-template-columns:minmax(0,1.06fr) minmax(300px,.94fr);gap:54px;align-items:center;padding:100px 0 89px}
.eyebrow{color:var(--mint);font-size:12px;letter-spacing:.17em;font-weight:800;margin:0 0 21px}
h1{font-size:clamp(40px,4.8vw,65px);line-height:1.1;letter-spacing:-.045em;margin:0 0 27px}
h1 span{display:block;background:linear-gradient(110deg,#85f3d1,#a2b6ff);background-clip:text;-webkit-background-clip:text;color:transparent}
.intro{font-size:18px;color:#bfd1e7;max-width:610px;margin:0 0 28px}
.actions{display:flex;gap:12px;flex-wrap:wrap}
.button{display:inline-flex;align-items:center;justify-content:center;padding:13px 18px;border-radius:12px;font-size:14px;font-weight:750}
.primary{background:var(--mint);color:#092b35}.secondary{border:1px solid #7488a0}
.notice{border-left:2px solid #75b0d4;padding-left:14px;margin:23px 0 0;font-size:13px;color:var(--muted);max-width:600px}
.visual{border:1px solid #3c5875;border-radius:22px;padding:25px;background:linear-gradient(145deg,#192d45ee,#101b2c);box-shadow:0 24px 90px #02081670}
.kicker{display:flex;justify-content:space-between;color:#a1b9d1;font-size:11px;letter-spacing:.15em;font-weight:780;margin-bottom:19px}
.dots{color:var(--mint)}
.node{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid #546f8a;background:#1b3047;border-radius:11px;padding:13px 16px;font-size:14px;font-weight:720}
.node small{color:var(--mint);font-size:11px}.arrow{text-align:center;color:#97b7d0;font-size:20px;line-height:1.7}
.workers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.workers .node{flex-direction:column;align-items:flex-start;min-width:0;overflow-wrap:anywhere;padding:11px 9px;font-size:12px}
.workers small{font-size:10px}
.terminal{border:1px solid #465c77;border-radius:12px;padding:16px;margin-top:19px;background:#0b192b}
.terminal-label{font-size:10px;letter-spacing:.11em;color:#9db4cf;font-weight:750;margin-bottom:9px}
.terminal code{display:block;color:#bcf7e2;font:12px/1.9 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
.terminal p{font-size:12px;color:var(--muted);margin:9px 0 0}
.section{padding:24px 0 78px}.section h2{font-size:clamp(27px,3.2vw,38px);letter-spacing:-.03em;line-height:1.2;margin:0 0 12px}
.lead{color:var(--muted);max-width:770px;margin:0 0 26px}
.cards,.steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}
.card{border:1px solid var(--line);border-radius:16px;padding:25px;background:linear-gradient(145deg,#172a42,#121e30)}
.tag{font-size:11px;font-weight:800;letter-spacing:.13em;color:var(--mint);margin:0 0 15px}
.card h3{font-size:19px;line-height:1.3;overflow-wrap:anywhere;margin:0 0 13px}
.card code{display:inline-block;border:1px solid #38516b;background:#0b1b2e;color:#cde0f7;border-radius:7px;padding:5px 8px;font-size:12px}
.card p:last-child{font-size:14px;color:var(--muted);margin:17px 0 0}
.step{padding:22px 23px;border-left:2px solid #58a6ad;background:#14263b;border-radius:0 11px 11px 0}
.step-num{color:var(--mint);font:750 12px ui-monospace,monospace}
.step h3{font-size:17px;margin:9px 0 8px}.step p{font-size:13px;color:var(--muted);margin:0 0 12px}
.step a{font-size:13px;color:var(--mint);font-weight:760}
footer{border-top:1px solid #ffffff24;padding:27px 0 36px}
footer .shell{display:flex;justify-content:space-between;align-items:flex-start;gap:22px;color:var(--muted);font-size:12px}
footer p{margin:0;max-width:670px}
@media(max-width:850px){.hero{grid-template-columns:1fr;gap:36px;padding:68px 0 72px}.visual{max-width:670px}.cards{grid-template-columns:repeat(2,1fr)}.steps{grid-template-columns:1fr}}
@media(max-width:590px){.shell{padding:0 18px}nav{min-height:72px}.brand span:last-child{display:none}.brand{font-size:12px}.links{gap:11px;font-size:12px}.links .optional{display:none}.hero{padding:55px 0 65px}.intro{font-size:16px}.visual{padding:17px}.workers{grid-template-columns:1fr}.cards{grid-template-columns:1fr}footer .shell{display:block}footer p{margin-bottom:12px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
</head>
<body>
<header><nav class="shell" aria-label="Main navigation">
<a class="brand" href="/" aria-label="Text-to-Speech AI Inference"><span class="brand-mark" aria-hidden="true">≋</span><span>Text-to-Speech<br>AI Inference</span></a>
<div class="links"><a class="optional" href="#models">$nav_models</a><a class="optional" href="#start">$nav_start</a><a class="optional" href="/v1/health">$nav_health</a><a class="language-switch" href="$other_url" hreflang="$other_language">$language_switch</a></div>
</nav></header>
<main class="shell">
<section class="hero" aria-labelledby="hero-heading">
<div><p class="eyebrow">$eyebrow</p><h1 id="hero-heading">$headline <span>$headline_accent</span></h1><p class="intro">$introduction</p>
<div class="actions"><a class="button primary" href="$project" target="_blank" rel="noopener noreferrer">$source_link ↗</a><a class="button secondary" href="$api_url" target="_blank" rel="noopener noreferrer">$documentation_link ↗</a></div>
<p class="notice">$notice</p></div>
<div class="visual" aria-label="$architecture">
<div class="kicker"><span>$architecture</span><span class="dots" aria-hidden="true">● ● ●</span></div>
<div class="node"><span>$client</span><small>HTTPS</small></div><div class="arrow" aria-hidden="true">↓</div>
<div class="node"><span>$gateway</span><small>FastAPI · Auth</small></div><div class="arrow" aria-hidden="true">↓</div>
<div class="node"><span>$gpu</span><small>Modal</small></div>
<div class="workers"><div class="node"><span>MOSS-TTS-v1.5</span><small>GPU</small></div><div class="node"><span>Higgs TTS 3</span><small>GPU · License</small></div><div class="node"><span>VoxCPM2</span><small>GPU</small></div></div>
<div class="terminal"><div class="terminal-label">$terminal</div><code>GET /v1/health · public</code><code>POST /v1/audio/jobs · Bearer</code><p>$terminal_note</p></div>
</div>
</section>
<section class="section" id="models" aria-labelledby="models-heading"><h2 id="models-heading">$model_heading</h2><p class="lead">$model_lead</p>
<div class="cards">
<article class="card"><p class="tag">MODEL 01 · OPENMOSS</p><h3>OpenMOSS-Team/MOSS-TTS-v1.5</h3><code>moss-tts-v1.5</code><p>$moss_note</p></article>
<article class="card"><p class="tag">MODEL 02 · BOSONAI</p><h3>bosonai/higgs-tts-3-4b</h3><code>higgs-tts-3</code><p>$higgs_note</p></article>
<article class="card"><p class="tag">MODEL 03 · OPENBMB</p><h3>openbmb/VoxCPM2</h3><code>voxcpm2</code><p>$voxcpm_note</p></article>
</div></section>
<section class="section" id="start" aria-labelledby="start-heading"><h2 id="start-heading">$steps_heading</h2><p class="lead">$steps_lead</p>
<div class="steps">
<article class="step"><span class="step-num">01</span><h3>$step1</h3><p>$step1_body</p><a href="$deployment_url" target="_blank" rel="noopener noreferrer">$deployment ↗</a></article>
<article class="step"><span class="step-num">02</span><h3>$step2</h3><p>$step2_body</p><a href="$api_url" target="_blank" rel="noopener noreferrer">$api ↗</a></article>
<article class="step"><span class="step-num">03</span><h3>$step3</h3><p>$step3_body</p><a href="$security_url" target="_blank" rel="noopener noreferrer">$security ↗</a></article>
</div></section></main>
<footer><div class="shell"><p>© Đăng Khoa · <a href="$project/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">MIT</a> · $footer</p><a href="$security_url" target="_blank" rel="noopener noreferrer">$security ↗</a></div></footer>
</body></html>""")


LANDING_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def render_landing(language: str = "en") -> str:
    """Render public HTML without accessing runtime configuration or the GPU."""
    locale = "vi" if language == "vi" else "en"
    suffix = ".vi" if locale == "vi" else ""
    values = {
        **COPY[locale],
        "language": locale,
        "other_url": "/?lang=vi" if locale == "en" else "/?lang=en",
        "other_language": "vi" if locale == "en" else "en",
        "project": PROJECT_URL,
        "api_url": f"{PROJECT_URL}/blob/main/docs/API{suffix}.md",
        "deployment_url": f"{PROJECT_URL}/blob/main/docs/DEPLOYMENT{suffix}.md",
        "security_url": f"{PROJECT_URL}/blob/main/docs/SECURITY{suffix}.md",
    }
    return PAGE.substitute({key: escape(value, quote=True) for key, value in values.items()})
