# Release evidence and limitations

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](RELEASE_EVIDENCE.vi.md)

The earlier source SHA 0d4f5a3d932b8acaceb2ded29a41fcc63fd5a977 passed Python 3.11, 3.12, 3.13 and PostgreSQL 16 / Redis 7 in [GitHub Actions run 44481659149](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/runs/44481659149).

The controlled MOSS-TTS-v1.5 production v4 GPU canary used deployed source SHA b2f97b6869ff6c4cf74e64e165dc93b047310ef4, completed one authenticated HTTPS job, and returned a 24 kHz mono PCM16 WAV of 464,684 bytes (9.68 seconds). WAV SHA-256: f30497bb3d54545abd99cada06d55a603794ebfb74a62e145de0654f4f94acce.

VoxCPM2 had isolated GPU WAV acceptance; Higgs TTS 3 was disabled. Production v4 was stopped after the demo.

The rewritten compressed-lock source was not part of the historical v4 deployment. This statement concerns v4 only; later P1 GPU evidence is below. CPU tests and exact lock reconstruction alone do not prove GPU inference. No multi-replica, HA or always-on API claims are made. Internal credentials, WAV and evidence archives remain outside GitHub Release.

See [testing](TESTING.md), [licenses](../MODEL_LICENSES.md).


## P1 DX simplification acceptance — 21 November 2026

The 21 November 2026 acceptance report records the reviewed source snapshot f438860898f996b3013687c050f32ec7bb03502d. The isolated canary's modal_app.py matches that snapshot except for the application name and authentication Secret reference; this equivalence was verified by direct file comparison. The operator selected MOSS-TTS-v1.5 and VoxCPM2 through TEXT_TO_SPEECH_ENABLED_MODELS. Higgs was disabled. Manual GPU_ACCEPTED/CODEC_READY flags were not required.

- **VoxCPM2:** authenticated HTTPS -> CPU gateway -> L40S GPU -> WAV, mono 48,000 Hz, 3.04 seconds, 291,884 bytes. SHA-256: 8e3cb530aef6b8108cdf7508c1f68576c23f4b84efebf74566028ab9edbda9ac.
- **MOSS-TTS-v1.5:** authenticated HTTPS -> CPU gateway -> L40S GPU -> WAV, mono 24,000 Hz, 12.24 seconds, 587,564 bytes. SHA-256: 37c0b21b782040ce34efd8e8f1611d2bd61998411ee444f33de6303aec5743cf.
- **Final API acceptance: 19/19 aggregate checks PASS across two separate jobs in one canary** for completed job states, authenticated WAV downloads, WAV hashes and metadata, replay with HTTP 202 reused, conflicting payload with HTTP 409, owner/client isolation, and rejected disabled Higgs with HTTP 503.
- [GitHub Actions run 44866848762 on main](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/runs/44866848762): Python 3.11/3.12/3.13 and PostgreSQL 16/Redis 7 integration PASS. CPU tests: 225 passed, 3 skipped. Source checksums: 113 verified.
- Canary stopped, **0 running tasks**. WAV, JSON and report evidence were archived separately from the repository (verified archive SHA-256: 767378a5e0856b87f94f3f8194f973ecba2d0913ebd1785ccecc82982fcbdda2).

**Limitations:** These are bounded results for two models in the tested P1 configuration, not guarantees for every operator, GPU, option, throughput or multi-replica deployment. Higgs hosted inference remains unverified and separately licensed. Subsequent documentation, CI and Higgs deployment-validation ordering changes do not constitute another GPU canary. The later validation-order change is covered by CPU regression tests only.

## Identify the source version

Use `git rev-parse 'v1.0.0^{commit}'` to identify the commit in the release tag and `git rev-parse HEAD` to identify your checkout. The historical release tag previously pointed to `cc431401081314590eaf79ce81b414988ed15003`. The P1 results above apply to the recorded canary snapshot `f438860898f996b3013687c050f32ec7bb03502d` and its documented configuration. That historical SHA may be absent from the rewritten Git history. Compare the SHA for your checkout or release with the recorded snapshot before citing the result. GitHub Actions links may require repository access. The WAVs and supporting archive are held by the operator.
