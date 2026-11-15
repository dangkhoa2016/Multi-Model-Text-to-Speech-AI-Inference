# Triển khai và vận hành Modal

> 🌐 Language / Ngôn ngữ: [English](DEPLOYMENT.md) | **Tiếng Việt**


**Đọc trước khi vận hành.** Mã nguồn đã có CPU gateway với PostgreSQL/Redis và ba GPU adapter; MOSS/VoxCPM2 đã qua thử nghiệm HTTPS/GPU/WAV trên canary, nhưng không tự triển khai. Deploy sẽ bật CPU sweep mỗi phút và có thể phát sinh chi phí hạ tầng; GPU chỉ được gọi khi thực sự có job hợp lệ. Đặt giới hạn chi tiêu tại Modal, kiểm tra giấy phép từng model (đặc biệt Higgs TTS 3).

## 1. Chuẩn bị một workspace/environment Modal nhất quán

Bắt đầu tại thư mục gốc repository với Python 3.11–3.13. Nếu chưa có mã nguồn, chạy `git clone https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference.git`, sau đó `cd Multi-Model-Text-to-Speech-AI-Inference`; bỏ qua bước clone nếu đã đứng trong thư mục dự án. Với Windows, dùng lệnh kích hoạt virtual environment tương ứng trong PowerShell:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[modal,dev]'
modal setup
modal config show --redact
modal environment list
# Chọn environment đã tồn tại; "main" chỉ là ví dụ, không phải mặc định.
export MODAL_ENVIRONMENT="main"
modal secret list --env "$MODAL_ENVIRONMENT"
modal volume list --env "$MODAL_ENVIRONMENT"
```

Modal profile đang dùng xác định **workspace**; `--env` hoặc `MODAL_ENVIRONMENT` xác định **environment** chứa Secrets, Volumes và deployment. Xác nhận cả hai trước khi tạo tài nguyên; Secret cùng tên ở environment khác không thể tự thay thế. Xem [Modal environment CLI](https://modal.com/docs/cli/latest/environment).

Yêu cầu PostgreSQL TLS/backup, Redis/Valkey TLS (`rediss://`), Modal có quota CPU/GPU/storage và client HTTPS. Tách quyền runtime/migration. Kiểm tra mã nguồn bằng:

```bash
python -m pytest -q
```

## 2. Tạo bốn Modal Secret riêng biệt

Tạo token ngẫu nhiên đủ mạnh cho từng client và **một owner token riêng** (ASCII tối thiểu 24 ký tự, nên 48+). API chỉ lưu/so sánh SHA-256 của token đủ entropy. Giữ token gốc ở client hoặc secret manager, không lưu vào Git hay ảnh chụp màn hình. Hash token bằng `python scripts/hash_token.py`, tạo khóa mã hóa Fernet bằng:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Dùng Modal Dashboard để tạo **bốn Secret** trong cùng workspace/environment (tránh lộ token trong shell history):

| Secret | Biến môi trường | Nơi sử dụng |
| --- | --- | --- |
| `text-to-speech-api-authentication` | `TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` (JSON client ID → SHA256 hex), `TEXT_TO_SPEECH_OWNER_TOKEN_SHA256` (hash owner), tùy chọn `TEXT_TO_SPEECH_REVOKED_CLIENT_IDS` | Chỉ gateway |
| `text-to-speech-job-database` | `TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL` (`postgresql+psycopg://...`, TLS theo nhà cung cấp), `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` (Fernet), tùy chọn `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_CLIENT`, `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_MODEL`, `TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS` và cặp biến ngân sách GPU ước tính | Gateway và CPU runner; **không cấp cho GPU** |
| `text-to-speech-hugging-face-authentication` | `HUGGING_FACE_READ_TOKEN` hoặc `HF_TOKEN` (token Read đã được cấp quyền); tùy chọn `HF_XET_HIGH_PERFORMANCE=1` | Chỉ kiểm tra/tải model bằng CPU; **không cấp cho gateway/GPU** |
| `text-to-speech-rate-limiting` | `TEXT_TO_SPEECH_REDIS_CONNECTION_URL` (`rediss://...`), tùy chọn `TEXT_TO_SPEECH_REDIS_RATE_LIMIT_NAMESPACE` và các biến `TEXT_TO_SPEECH_RATE_*` và `TEXT_TO_SPEECH_AUTHENTICATION_*` trong `.env.example` | Chỉ gateway |

Ví dụ **cấu trúc** `TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` (placeholder, không phải hash thật):

```json
{"rails-app":"<chuỗi SHA256 hex chữ thường 64 ký tự>","notebook":"<chuỗi SHA256 hex chữ thường 64 ký tự>"}
```

Phải giữ `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` ổn định để phục hồi job và audio sau restart. Muốn thay khóa phải có kế hoạch mã hóa lại, không đổi trực tiếp rồi mất dữ liệu. Khi đổi hoặc thu hồi client token, cập nhật Secret và chủ động rollover gateway.

## 3. Migration PostgreSQL trước khi deploy

**Nâng cấp database đang hoạt động:** Sao lưu PostgreSQL, giữ Fernet key. Dừng gateway HTTP cũ và tạm ngưng scheduler trước khi đổi schema. Để runner CPU cũ hoàn tất các job GPU đang chạy; không tự hủy GPU chỉ để nâng cấp. Chạy `alembic upgrade head` qua revision `0002` mở rộng, `0003` tương thích writer cũ và `0004` bổ sung diagnostics/timing nullable, sau đó `0005` chuẩn hóa cột fingerprint của schema legacy/tạo index audit và `0006` bổ sung ledger ngân sách GPU ước tính. Cột `payload_sha256` vẫn cho runner cũ đọc/ghi; gateway mới dùng HMAC. Startup chuyển đổi metadata theo batch, không tải WAV mã hóa vào RAM và có thể tiếp tục sau restart. Chỉ khởi động gateway/scheduler mới khi migration xong, không để gateway HTTP cũ và mới cùng nhận job. Với database đã dùng `0002` đổi tên cột, `0003` phục hồi tương thích nhưng không cứu được kết quả đã mất trước đó; cần đối soát job. Không tự xoay khóa nếu chưa có quy trình migration riêng.


Trên máy vận hành được cấp quyền migration (nên dùng kết nối trực tiếp, không đi qua pool):

```bash
export TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL='postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@YOUR_HOST/YOUR_DB?sslmode=require'
alembic upgrade head
alembic current
```

Đây là URL minh họa; cung cấp secret thật theo cách an toàn. Migration tạo `tts_jobs`, `tts_audit_events`, `admission_lock` (một hàng khóa chung), `tts_estimated_gpu_budget_windows` (revision `0006`) và indexes. **Không chạy `Base.metadata.create_all` cho production.** Tách tài khoản migration có quyền DDL, runtime chỉ có DML và kiểm tra backup trước các lần nâng cấp tiếp theo.

## 4. Kiểm tra quyền Hugging Face, sau đó tải model bằng CPU

1. Đăng nhập [Hugging Face](https://huggingface.co/), mở trang từng model và gửi yêu cầu/chấp nhận điều khoản **nếu model có hạn chế truy cập**. Quyền được cấp cho tài khoản người dùng, không tự động được cấp cho Modal. Model công khai có thể không cần bước này; luôn kiểm tra chính sách hiện tại.
2. Tạo token **Read** của chính tài khoản được cấp quyền, lưu vào `HUGGING_FACE_READ_TOKEN` **hoặc** `HF_TOKEN` trong Secret `text-to-speech-hugging-face-authentication`. Token có sẵn trong máy cá nhân không tự chuyển sang Modal container.
**Token Hugging Face và tăng tốc bằng Xet.** CPU preflight/downloader chấp nhận biến cũ `HUGGING_FACE_READ_TOKEN` hoặc biến chuẩn `HF_TOKEN`; nếu cả hai có giá trị thì ưu tiên biến cũ. Chỉ cần đặt **một** biến trong Modal Secret `text-to-speech-hugging-face-authentication`. Không chuyển token sang HTTP gateway hay GPU worker. CPU download image cài `huggingface_hub` cùng `hf_xet>=1.7,<2`. Muốn thử tăng tốc, thêm `HF_XET_HIGH_PERFORMANCE=1` **vào chính Secret chỉ dành cho CPU**; mặc định bỏ trống. Chế độ này có thể tăng sử dụng CPU, RAM, băng thông, I/O và chi phí mà không đảm bảo luôn nhanh hơn. Từ `hf_xet` 1.7.0, bộ đệm tải hiệu năng cao tự điều chỉnh theo bộ nhớ khả dụng. Sau khi đo thử, xóa biến để quay lại mặc định. Không dùng `HF_HUB_ENABLE_HF_TRANSFER` đã deprecated. Xem [Hugging Face Xet storage](https://huggingface.co/docs/hub/xet/using-xet-storage). Khi cập nhật Secret, cần kiểm tra hiệu lực trên CPU container mới trong đúng Modal environment trước khi tải có tính phí.

3. Kiểm tra quyền tải bằng CPU trong **đúng Modal workspace/environment** trước khi tải checkpoint lớn:

```bash
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access --model-id moss-tts-v1.5
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access --model-id voxcpm2
# Kiểm tra quyền tải không đồng nghĩa với quyền thương mại của Higgs TTS 3:
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access --model-id higgs-tts-3
```

Bước này không tải trọng số, không khởi chạy GPU. Thông báo lỗi phân biệt thiếu token, chưa được cấp quyền gated model, sai repository ID và lỗi dịch vụ Hugging Face; không in token. Mỗi model được kiểm tra riêng, nên thất bại của một model không xóa dữ liệu đã tải của model khác. Phải xem xét giấy phép sử dụng độc lập với quyền tải checkpoint.

4. Chỉ tải checkpoint sau khi bước kiểm tra tương ứng thành công:

- `text-to-speech-moss-tts-v1-5-model-weights`
- `text-to-speech-higgs-tts-3-model-weights`
- `text-to-speech-voxcpm2-model-weights`

```bash
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_tts_model
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_voxcpm2_model
# Codec âm thanh riêng của MOSS, khoảng 7,1 GB; chỉ dùng CPU:
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_audio_tokenizer
```

**Audio tokenizer của MOSS:** `OpenMOSS-Team/MOSS-Audio-Tokenizer`
(Apache-2.0) là checkpoint riêng, được khóa tại revision
`3cd226ba2947efa357ef453bcad111b6eafba782`. CPU downloader
kiểm tra 14 file (7.101.115.998 byte), SHA-256 từng file và hash LFS
upstream trước khi ghi vào Volume
`text-to-speech-moss-audio-tokenizer-weights`. Worker MOSS mount ở
`/codec` và kiểm tra manifest/revision trước khi khởi động server.
Preprocessing và vocoder đều dùng đường dẫn cục bộ này, với
`HF_HUB_OFFLINE=1` để không tự tải checkpoint.

**Tương thích MOSS:** Hai bản vá giới hạn đúng `sglang-omni==0.2.0`
và SHA-256 mã nguồn. Chúng giữ đủ bảy tham số sampling riêng kênh
text/audio qua `stage_params.tts_engine`, đồng thời bổ sung kiểm
tra ngôn ngữ, bao gồm tiếng Việt. Nếu mã upstream thay đổi thì build
dừng thay vì bỏ qua tham số. Kiểm thử CPU không thay thế việc xác minh
CUDA, VRAM và WAV thực tế.

Tải dữ liệu có chi phí CPU/storage nhưng **không cấp phát GPU**. Với Higgs, chỉ sau khi đã xác minh có quyền sử dụng API host theo giấy phép:

```bash
HIGGS_TTS_3_LICENSE_CONFIRMED=1 modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_higgs_tts_3_model
```

Xác nhận giấy phép Higgs **không** thay thế quyền sử dụng thực tế. Higgs mặc định tắt; chỉ được bật khi chọn `higgs-tts-3` trong `TEXT_TO_SPEECH_ENABLED_MODELS`, có quyền sử dụng phù hợp (`HIGGS_TTS_3_LICENSE_CONFIRMED=1`) và OCI digest SGLang bất biến đã kiểm toán. Thiếu một trong hai điều kiện khi chọn Higgs sẽ khiến deploy báo lỗi rõ ràng; GPU worker cũng kiểm tra license lúc khởi động. Client không thể bật Higgs bằng request.

## 5. Deploy sau khi chuẩn bị Secret, migration và checkpoint

Chọn model tại **một biến duy nhất** trên máy triển khai. Mặc định danh sách rỗng, không model nào nhận job. Khi được chọn, MOSS/VoxCPM2 tự build dependency theo lockfile có SHA-256; không cần cờ nghiệm thu GPU/codec thủ công.

```bash
# Sau khi tạo Secrets, chạy migration và tải checkpoint bằng CPU:
TEXT_TO_SPEECH_ENABLED_MODELS=moss-tts-v1.5,voxcpm2 \
  modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py

# Higgs là lựa chọn riêng, chỉ dùng khi quyền hosted/API đã được cấp.
# HIGGS_TTS_3_LICENSE_CONFIRMED=1 \
# TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF=lmsysorg/sglang-omni@sha256:<digest-da-xac-minh> \
# TEXT_TO_SPEECH_ENABLED_MODELS=higgs-tts-3 \
#   modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py
```

`enabled` trong catalog phản ánh model được operator chọn, **không phải bằng chứng GPU đã sẵn sàng**. Worker kiểm tra integrity manifest của checkpoint (và codec MOSS) rồi mới nạp model thực tế; nếu thiếu sẽ báo lỗi cụ thể, không yêu cầu người dùng xác nhận bằng `GPU_ACCEPTED` hoặc `CODEC_READY`.

- `text_to_speech_api`: chỉ CPU, nhận ba Secret, xác thực trước khi đọc JSON; không mount trọng số.
- `dispatch_text_to_speech_job`: CPU, chỉ nhận Secret PostgreSQL/mã hóa, claim job và gọi GPU tương ứng khi hợp lệ.
- `resume_queued_text_to_speech_jobs`: tác vụ **CPU theo lịch mỗi phút**, đọc job `queued` và đánh thức runner; không retry `running`/`indeterminate`.
- Mỗi GPU worker: một class + một Volume riêng, `max_containers=1`, `min_containers=0`.

MOSS/Higgs dùng SGLang-Omni loopback `127.0.0.1:8000`; VoxCPM2 dùng API Python upstream. Worker MOSS đóng gói `configs/moss_tts.yaml` vào image và truyền `--config /opt/text-to-speech/configs/moss_tts.yaml`, theo [cookbook MOSS của upstream](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/moss_tts.md). Pipeline MOSS single-process với CUDA compiler/linker đã qua nghiệm thu sinh WAV PCM trên L40S trong app riêng. Canary HTTPS qua gateway end-to-end cũng PASS ngày 21/11/2026; mỗi operator vẫn cần nghiệm thu deployment của mình. Cấu hình Higgs cần đối chiếu và nghiệm thu riêng.

**Kiểm tra codec MOSS:** Chọn MOSS thì image tự sử dụng lockfile
dependency đã xác minh SHA-256. Worker tự kiểm tra integrity của model và
audio tokenizer trước khi chạy GPU. Người vận hành vẫn tải checkpoint bằng
hàm CPU riêng; không phải khai báo cờ xác nhận codec/GPU.

**Image dependency có khóa SHA-256:** MOSS sử dụng Debian/Python 3.12 với
lockfile 283 package; VoxCPM2 dùng lockfile riêng 167 package trong
`runtime_locks/`. Cả hai được resolve cho Linux x86_64/manylinux 2.34
và cài bằng `uv==0.13.5`, bắt buộc `--require-hashes`. Trước khi
build image, ứng dụng còn đối chiếu SHA-256 của nguyên lockfile.

**VoxCPM2:** Phiên bản package đã được pin trong lockfile; không yêu
cầu nhập lại số phiên bản hoặc cờ nghiệm thu GPU. Worker tự kiểm tra
checkpoint manifest trước khi nạp model.

MOSS không còn dùng Docker image SGLang cũ làm nền vì có thể gây
xung đột môi trường Python/SGLang; image mới chỉ sử dụng Python 3.12
và những package được khóa trong lockfile. Hai bản vá MOSS vẫn kiểm
tra SHA-256 nguồn `sglang-omni==0.2.0` trước khi áp dụng.

**Higgs độc lập:** Chỉ runtime Higgs tiếp tục sử dụng
`TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF` với OCI digest bất biến,
cùng điều kiện giấy phép riêng. Hai bộ lockfile MOSS/VoxCPM2 không
phải bằng chứng nghiệm thu Higgs. MOSS-TTS-v1.5 và VoxCPM2 đã tạo WAV hợp lệ trong các bài nghiệm thu L40S riêng; Higgs vẫn cần nghiệm thu CUDA, checkpoint và WAV độc lập. Trước khi mở phục vụ, phải kiểm tra đường đi end-to-end qua gateway.

Mã nguồn không tự tải tag mutable `:dev`. OCI digest đã kiểm toán hiện
chỉ dùng cho Higgs và vẫn chịu ràng buộc giấy phép riêng. Với MOSS và
VoxCPM2, dependency được khóa SHA-256 và build tự động khi được chọn.
Catalog CPU trả `enabled=false` và POST trả 503 khi model chưa được chọn;
GPU worker tự kiểm tra checkpoint trước khi chạy.
`denoise=true` của VoxCPM2 vẫn bị từ chối vì chưa cài denoiser.

**Nghiệm thu GPU độc lập, chỉ khi được phê duyệt:**
`scripts/gpu_runtime_acceptance.py` là công cụ kiểm thử nghiên cứu, tách
khỏi API production. Script hỗ trợ đủ **MOSS-TTS-v1.5, VoxCPM2 và Higgs
TTS 3**, sử dụng package lock SHA-256, Volume checkpoint chỉ đọc và
những câu kiểm thử cố định. Script không tải model hoặc mở API công khai.
Khi chưa được người vận hành đồng ý, công cụ dùng `gpu=None` và từ
chối chạy GPU.

Trước khi chạy, cần kiểm tra Modal Workspace Budget. Chỉ kiểm tra
**một model và một giai đoạn** tại mỗi lượt. Bước CUDA kiểm tra driver,
VRAM và phép tính GPU; chưa nạp checkpoint.

```bash
# Chọn một model: moss, voxcpm hoặc higgs.
TTS_GPU_ACCEPTANCE_APPROVAL=APPROVED_GPU_ACCEPTANCE_WITH_CHARGES modal run --env "$MODAL_ENVIRONMENT" scripts/gpu_runtime_acceptance.py --model higgs --phase cuda

# Chỉ sau khi CUDA đạt và chi phí chạy WAV được chấp thuận riêng:
# TTS_GPU_ACCEPTANCE_APPROVAL=APPROVED_GPU_ACCEPTANCE_WITH_CHARGES modal run --env "$MODAL_ENVIRONMENT" scripts/gpu_runtime_acceptance.py --model higgs --phase wav
```

Thay `higgs` bằng `moss` hoặc `voxcpm` khi kiểm thử model khác.
Giai đoạn WAV xác minh SHA-256 checkpoint, nạp model, tổng hợp một
câu tiếng Việt ngắn và ghi lại thông tin WAV, thời gian, peak VRAM
PyTorch. Mỗi function dùng một L40S và giới hạn thời gian; thử nghiệm
thất bại vẫn có thể phát sinh chi phí.

**Runtime nghiên cứu Higgs** dùng cùng bộ dependency cố định
`sglang-omni==0.2.0` với MOSS nhưng không áp dụng bản vá dành riêng
cho MOSS. Nó **khác** image OCI Higgs production; bài thử này không
thay thế nghiệm thu môi trường production. Có thể dùng biến
`TTS_HIGGS_CHECKPOINT_REVISION_SHA` để khóa revision đã xác minh.
Higgs vẫn áp dụng giấy phép nghiên cứu/phi thương mại; triển khai
hosted thương mại cần quyết định riêng. Tham khảo
[hướng dẫn Higgs SGLang upstream](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/higgs_tts.md).
Không tự bật model hay redeploy Gateway chỉ vì chạy bài thử.

**Tải checkpoint Higgs dành riêng cho nghiên cứu (chỉ CPU, tùy chọn):**
Lệnh tải Higgs cho production yêu cầu quyết định giấy phép riêng đối với
dịch vụ hosted; nghiệm thu nghiên cứu không thay đổi chính sách đó. Trước
khi tải, xác minh mục đích nghiên cứu được phép, quyền Hugging Face,
Modal Workspace Budget, Volume `text-to-speech-higgs-tts-3-model-weights`
**đã tồn tại và còn trống** trong Modal environment đang dùng, cùng
Secret chỉ đọc `text-to-speech-hugging-face-authentication`.
Lệnh CPU được chấp thuận riêng sẽ tải revision bất biến, đối chiếu danh sách
file từ Hub, tạo manifest SHA-256 và commit vào Volume:

```bash
# Có thể phát sinh phí CPU, lưu trữ, truyền dữ liệu; không khởi chạy GPU.
TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL=APPROVED_HIGGS_RESEARCH_CPU_DOWNLOAD_WITH_CHARGES \
modal run --env "$MODAL_ENVIRONMENT" scripts/higgs_research_checkpoint.py
```

Có thể bổ sung `TTS_HIGGS_CHECKPOINT_REVISION_SHA=<40-ky-tu-hex-chu-thuong>`
để khóa revision đã xác minh cho **cả** lần tải này và nghiệm thu GPU sau
đó. Nếu Volume đã có dữ liệu hoặc revision không khớp, công cụ sẽ dừng
để người vận hành kiểm tra thay vì tự ghi đè. Chấp thuận nghiên cứu
**không bật** `HIGGS_TTS_3_LICENSE_CONFIRMED`, không triển khai API
công khai và không cấp quyền sử dụng hosted thương mại.

**Xác minh checkpoint:** Mỗi lượt tải CPU được chủ động cho phép sẽ ghi `.tts-checkpoint-integrity.json` (revision Hugging Face bất biến, SHA-256 và kích thước từng file) vào Volume riêng. CPU downloader còn đối chiếu danh sách file với cây repository tại revision bất biến, từ chối file cũ dư ra; worker GPU kiểm tra toàn bộ file trước khi nạp model. Đây là phát hiện lỗi/can thiệp dữ liệu, **không phải chữ ký độc lập** nếu attacker sửa cả manifest. Có thể khóa revision được operator phê duyệt qua `TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA`, `TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA`, `TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA` trước khi deploy. Volume cũ chưa có manifest phải được xác minh/tải lại bằng CPU theo quyết định operator; kiểm tra toàn file khi khởi động GPU có thể làm tăng thời gian/chi phí.

**Ngân sách admission ước tính (tùy chọn):** Migration `0006` tạo ledger PostgreSQL theo ngày UTC. Đặt **đồng thời** `TEXT_TO_SPEECH_ESTIMATED_GPU_USD_PER_JOB` và `TEXT_TO_SPEECH_MAX_ESTIMATED_GPU_USD_PER_DAY` trong Modal Secret `text-to-speech-job-database`, hoặc bỏ cả hai để tắt. Mỗi job mới dự trữ số USD ước tính cố định trên toàn bộ client; replay idempotency không cộng trùng; vượt trần trả HTTP 429 và `Retry-After` tới ngày UTC tiếp theo. Đây không phải số liệu GPU đã tiêu thụ, không hoàn tiền khi job thất bại và không thay thế Modal Workspace Budget thực.

**Chính sách cloning tại production:** Mặc định tắt clone giọng. `reference_consent: true` chỉ là lời xác nhận của client, chưa đủ xác minh quyền người nói. Sau khi kiểm tra quyền sử dụng giọng và biện pháp chống lạm dụng, operator có thể chủ động bật **trên máy chạy `modal deploy`**, không phải notebook hay request:

```bash
export TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING=1
export TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON='["rails-app"]'
modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py  # Chỉ chạy khi được phép triển khai
```

`modal_app.py` đọc cờ không chứa bí mật khi deploy rồi nhúng `1` hoặc `0` vào CPU gateway image bằng `.env()`. Container remote **không tự kế thừa** biến môi trường của shell deploy. Nếu thiếu cờ global **hoặc** client ID không nằm trong allowlist, API trả HTTP 403 **trước enqueue hoặc gọi GPU** khi có reference audio. Muốn tắt, unset/đặt `0` rồi redeploy. Bật cờ không phải bằng chứng đã xác minh consent.

**CPU operations monitor (tùy chọn):** Mặc định `TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE=off`, không có lịch giám sát. Trên **máy chạy modal deploy**, đặt `TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE=hourly` để kiểm tra mỗi giờ: job queued quá lâu, heartbeat `running` bị treo, `indeterminate` và tổng ngân sách ước tính ngày UTC. Hàm chỉ ghi số liệu tổng hợp vào Modal Logs và đánh dấu lỗi Function Call khi cần operator xử lý; không gửi email/webhook, không gọi/retry GPU. Đặt `off` rồi redeploy để tắt. CPU sweep queue mỗi phút là tác vụ riêng vẫn hoạt động.

## 6. Kiểm thử và xử lý job không chắc chắn

Kiểm tra CPU/API trước: auth, catalog, lỗi 401/429, idempotency, HTTP 202, trạng thái `queued`, owner-only metrics. Sau đó, **khi cho phép phát sinh phí GPU**, thử từng model bằng một câu ngắn theo thứ tự VoxCPM2 → MOSS → Higgs (nếu đã có quyền). Đo WAV, hiệu năng thực, memory và hóa đơn. WAV mock chỉ là **âm thanh im lặng**.

Nếu heartbeat quá hạn, GET báo `indeterminate` mà không tự retry. Khi mất xác nhận GPU, job có trạng thái `indeterminate` lưu trong DB, **tiếp tục giữ slot GPU** để tránh chồng tác vụ. Chỉ sau khi xem log/modal invocation và xác minh GPU đã dừng mới chạy CLI operator:

```bash
python scripts/reconcile_job.py JOB_ID CLIENT_ID
python scripts/reconcile_job.py JOB_ID CLIENT_ID --confirm-stopped
```

`--confirm-stopped` đánh dấu job `failed` và giải phóng slot sau xác nhận thủ công; **không tự chạy lại GPU**. Không dùng khi GPU có thể còn đang xử lý.

CPU sweep **chỉ xóa `completed` và `failed` đã được xác nhận** sau `TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS` (mặc định 7 ngày). `indeterminate`, `running` và `queued` **không bao giờ tự động bị xóa**; job không rõ kết quả giữ slot cho tới khi operator xác minh GPU đã ngừng và chạy CLI reconcile. Điều này tránh trùng lặp lời gọi GPU có tính phí. Backup database và chính sách lưu của Modal là độc lập.

## 7. Phạm vi nghiệm thu của người vận hành

Kiểm thử CPU và integration PostgreSQL/Redis xác minh hoạt động của phần mềm,
nhưng không thay thế việc chạy model trên GPU thật. Khi triển khai, người
vận hành cần kiểm tra quyền tải checkpoint, tính tương thích runtime,
HTTP API có xác thực, trạng thái hoàn tất job, WAV đầu ra và audit events
bằng một yêu cầu có kiểm soát và được cho phép. Một canary GPU thành công
**không** chứng minh khả năng tải nhiều replica, failover, độ sẵn sàng
liên tục hay mức sẵn sàng phục vụ nhiều người dùng công khai.

Trước khi cung cấp API cho bên ngoài, cần đánh giá riêng giới hạn chi phí,
giấy phép checkpoint và các dependency, quyền và sự đồng ý của chủ giọng nói,
quyền riêng tư, thời hạn lưu dữ liệu, edge security, lưu trữ và khôi phục
sau sự cố. WAV sinh ra được mã hóa trong PostgreSQL BYTEA; bản này chưa
tích hợp R2/S3 làm kho audio. Higgs TTS 3 cần có quyền sử dụng theo giấy
phép riêng và vượt qua kiểm tra runtime trước khi được bật.

**Demo ngắn hạn:** Các scheduled CPU functions (bao gồm sweep khôi phục
queued job) có thể phát sinh phí Modal khi app được triển khai, ngay cả
khi không chạy GPU. Hãy stop app sau demo; thao tác này không tự loại bỏ
phí lưu database hay checkpoint Volume. Nên nghiệm thu khôi phục bản sao
lưu riêng trước khi xóa bất kỳ dữ liệu bền vững nào.

Tham khảo [Kiến trúc](ARCHITECTURE.vi.md), [API](API.vi.md), [Bảo mật](SECURITY.vi.md).

## Kiểm thử integration PostgreSQL và Redis trên CPU

Bộ pytest thông thường không cần dịch vụ ngoài. Để nghiệm thu với database thật, hãy tạo một **PostgreSQL database thử nghiệm riêng** và Redis instance/namespace dành cho thử nghiệm; tuyệt đối không dùng credentials hoặc dữ liệu production. Chạy Alembic migrations trước:

```bash
# Nạp hai URL từ secret manager cục bộ, không commit giá trị thực.
# TTS_INTEGRATION_POSTGRESQL_URL phải trỏ tới database có "test" hoặc "validation".
# TTS_INTEGRATION_REDIS_URL phải trỏ tới Redis dành riêng cho thử nghiệm.
export TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL="$TTS_INTEGRATION_POSTGRESQL_URL"
alembic upgrade head
python -m pytest -q tests/test_real_postgresql_redis_integration.py
```

Bộ test CPU tùy chọn này sử dụng nhiều kết nối PostgreSQL độc lập để xác nhận concurrent enqueue/claim, phân quyền owner, slot của job chưa rõ kết quả, Redis Lua quota và lockout phân tán. Test tạo bản ghi và khóa Redis tạm; **không** gọi Modal, không chạy inference. Nếu thiếu một trong hai URL, các integration test được skip. Kết quả local không chứng minh Modal multi-replica, backup/restore hoặc GPU đã sẵn sàng.

## 8. Checklist nghiệm thu khi cài đặt từ đầu

Thực hiện theo thứ tự; ghi đạt/chưa đạt, **không** ghi lại mật khẩu hoặc token.

1. [ ] Dùng Python 3.11–3.13, cài dependency và chạy thành công `python -m pytest -q`.
2. [ ] Xác nhận workspace bằng `modal config show --redact` và chọn `MODAL_ENVIRONMENT` trong danh sách `modal environment list`.
3. [ ] Có PostgreSQL kết nối TLS; tài khoản migration cần quyền DDL, tài khoản gateway chỉ cần quyền DML; có backup và đã kiểm tra quy trình phục hồi.
4. [ ] Có Redis/Valkey hỗ trợ TLS (`rediss://`) và Lua scripts, với namespace rate-limit tách biệt.
5. [ ] Dùng trình tạo mật khẩu ngẫu nhiên có chất lượng mật mã trong password manager để sinh **hai Bearer token khác nhau**, mỗi token ít nhất 48 ký tự ASCII in được (client và owner). Lưu bản gốc tại đó, rồi hash riêng bằng `python scripts/hash_token.py`; không ghi token gốc vào log.
6. [ ] Tạo và lưu an toàn khóa Fernet **một lần**. Tạo đủ bốn Modal Secrets bằng Dashboard trong environment đã chọn; kiểm tra tên bằng `modal secret list --env "$MODAL_ENVIRONMENT"`.

7. [ ] Chạy `alembic upgrade head` bằng kết nối migration, kiểm tra `alembic current` hiển thị revision `0006`.
8. [ ] Kiểm tra quyền đọc từng model trên Hugging Face. `ACCESS OK` không đồng nghĩa đã tải checkpoint hoặc GPU sẵn sàng.
9. [ ] Khi đã cho phép dùng CPU/storage có tính phí, tải checkpoint vào Volume riêng và xác nhận có integrity manifest.
10. [ ] Khóa phiên bản runtime đã kiểm toán theo Mục 9; model chưa đủ điều kiện phải được giữ tắt.
11. [ ] Sau khi được phép deploy CPU, thực hiện nghiệm thu GET-only ở Mục 10 trước khi gửi job có tính phí.

## 9. Kiểm toán và khóa phiên bản GPU runtime

MOSS-TTS-v1.5 và VoxCPM2 dùng dependency lock đã kiểm toán trong repository. Repository **không cung cấp sẵn** SGLang-Omni image digest đã phê duyệt cho Higgs. Không tự chế digest hoặc dùng mutable tag.

- **MOSS/Higgs:** Kiểm tra registry digest của container `lmsysorg/sglang-omni` từ nguồn đáng tin cậy, đối chiếu tương thích với `sglang-omni==0.2.0` và file YAML MOSS. Ghi đúng `lmsysorg/sglang-omni@sha256:<64-hex-chữ-thường>` vào `TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF` **trên máy deploy**.
- **VoxCPM2:** Kiểm tra các phiên bản upstream và package index (ví dụ `python -m pip index versions voxcpm`); xem xét phiên bản `X.Y.Z`, CUDA dependencies và API `VoxCPM.from_pretrained` trước khi đặt `TEXT_TO_SPEECH_VOXCPM_PACKAGE_VERSION`.

- **Revision checkpoint bất biến:** Sau khi tải CPU thành công, ghi lại revision Hugging Face 40 ký tự hex; có thể khóa bằng `TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA`, `TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA` hoặc `TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA`. Giá trị phải khớp manifest trong Volume.
- **Giấy phép Higgs:** Quyền hosted/API là quyết định pháp lý độc lập. Giữ `HIGGS_TTS_3_LICENSE_CONFIRMED=0` cho đến khi được phép.

Model không được chọn hiển thị `enabled=false`. Khi chọn Higgs, thiếu xác nhận giấy phép hoặc tham chiếu image bất biến hợp lệ thì cấu hình bị từ chối trước khi dựng registry image. Image build thành công **không chứng minh** GPU hoặc audio inference đã nghiệm thu.

## 10. Nghiệm thu HTTP chỉ đọc sau khi được phép deploy

Lấy URL HTTPS **thực tế** của `text_to_speech_api` từ Modal Dashboard hoặc lệnh xem thông tin App; không tự đoán hostname.

```bash
modal app info text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"
modal app logs text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT" --tail 30
export TTS_BASE_URL="https://YOUR-ACTUAL-DEPLOYED-ENDPOINT"
curl --silent --show-error --include --max-time 20 "$TTS_BASE_URL/"
curl --silent --show-error --include --max-time 20 "$TTS_BASE_URL/v1/health"
# Một lần không gửi token dự kiến nhận 401; thử sai nhiều lần sẽ bị khóa tạm.
curl --silent --show-error --include --max-time 20 "$TTS_BASE_URL/v1/models"
read -r -s -p "Client Bearer token: " TTS_CLIENT_TOKEN; echo
curl --silent --show-error --include --max-time 20 \
  -H "Authorization: Bearer $TTS_CLIENT_TOKEN" "$TTS_BASE_URL/v1/models"
unset TTS_CLIENT_TOKEN
```

Kết quả dự kiến: `/` trả HTML song ngữ (HTTP 200); `/v1/health` trả JSON có `status=ok` (HTTP 200), **chỉ là CPU liveness, không phải GPU readiness**; `/v1/models` không token trả 401, có token client hợp lệ trả 200 cùng ba model và cờ `enabled`. Model không được chọn có `enabled=false`; cấu hình bắt buộc thiếu hoặc sai bị từ chối khi xác minh deployment. Giai đoạn này không POST, không tạo WAV hoặc gọi GPU.

## 11. Xử lý lỗi, thay Secret và rollback

| Triệu chứng | Bước kiểm tra và xử lý an toàn |
| --- | --- |
| Không tìm thấy Modal Secret | Kiểm tra profile, `MODAL_ENVIRONMENT` và `modal secret list --env "$MODAL_ENVIRONMENT"`; tạo đúng Secret ở environment đã chọn |
| Gateway báo database chưa sẵn sàng | Kiểm tra `alembic current`, TLS/CA, quyền tài khoản và revision `0006`; không chạy `create_all` trên production |
| HTTP 503 do Redis | Kiểm tra URL `rediss://`, chứng chỉ, firewall và Lua; không bỏ cơ chế fail-closed |
| Hugging Face 401/403 hoặc gated | Chạy lại CPU preflight với tài khoản được cấp quyền; không tải mù checkpoint |
| Model `enabled=false` hoặc POST 503 | Kiểm tra danh sách model được chọn; kiểm tra cấu hình runtime và quyền sử dụng Higgs nếu áp dụng, rồi chủ động redeploy |
| Checkpoint integrity error | So sánh manifest của Volume với revision được phép; xác minh/tải lại bằng CPU theo quy trình |
| HTTP 401/429 | Kiểm tra client hash, token bị thu hồi hoặc tuân thủ `Retry-After`; không tắt rate limits |
| Job `indeterminate` | Xem chính xác Modal Function Call, xác minh GPU đã dừng trước khi chạy lệnh đối soát |

Khi thay hash của client/owner trong Secret, cần chủ động làm mới các container gateway. `modal app rollover text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"` tạo container mới mà không đổi code nhưng có thể ảnh hưởng request đang chạy. Không thay Fernet key nếu chưa có quy trình mã hóa lại đã kiểm chứng.

Trước khi rollback code, xem `modal app history text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"` và [hướng dẫn rollback Modal](https://modal.com/docs/cli/latest/app). Rollback Modal **không rollback Alembic** và không phục hồi backup PostgreSQL. Không tự dừng GPU có thể còn chạy chỉ để rollback.
