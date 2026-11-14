"""Safe translation of public controls to the underlying model APIs."""


def moss_tts_payload(job: dict, reference_path: str | None = None) -> dict:
    options = job["model_config"]
    keys = ("text_temperature", "audio_temperature", "text_top_p",
            "audio_top_p", "text_top_k", "audio_top_k",
            "audio_repetition_penalty")
    payload = {
        "model": "OpenMOSS-Team/MOSS-TTS-v1.5",
        "input": job["input"], "voice": options["voice"],
        "response_format": "wav", "stream": False,
        "max_new_tokens": options["max_new_tokens"],
        "stage_params": {"tts_engine": {key: options[key] for key in keys}},
    }
    for key in ("language", "instructions", "seed"):
        if options.get(key) is not None:
            payload[key] = options[key]
    if options.get("duration_tokens") is not None:
        payload["token_count"] = options["duration_tokens"]
    if reference_path:
        payload["references"] = [{"audio_path": reference_path,
                                  "text": job.get("reference_text") or ""}]
    return payload


def higgs_tts_3_payload(job: dict, reference_path: str | None = None) -> dict:
    model_options = job["model_config"]
    tags = []
    if model_options.get("emotion"):
        tags.append(f"<|emotion:{model_options['emotion']}|>")
    if model_options.get("style"):
        tags.append(f"<|style:{model_options['style']}|>")
    for key, prefix in (("speed", "speed"), ("pitch", "pitch"), ("expressiveness", "expressive")):
        if model_options[key] != "normal":
            tags.append(f"<|prosody:{prefix}_{model_options[key]}|>")
    payload = {
        "model": "bosonai/higgs-tts-3-4b", "voice": model_options["voice"],
        "input": "".join(tags) + job["input"],
        "response_format": "wav", "stream": False,
        "temperature": model_options["temperature"], "top_p": model_options["top_p"],
        "top_k": model_options["top_k"], "max_new_tokens": model_options["max_new_tokens"],
    }
    if model_options.get("seed") is not None:
        payload["seed"] = model_options["seed"]
    if reference_path:
        payload["references"] = [{"audio_path": reference_path, "text": job.get("reference_text") or ""}]
    return payload


def voxcpm2_payload(job: dict, reference_path: str | None = None) -> dict:
    model_options = job["model_config"]
    controls = []
    if model_options.get("voice_description"):
        controls.append(model_options["voice_description"])
    controls += [f"{model_options[key]} {label}" for key, label in (("gender", "voice"), ("age", "person")) if model_options.get(key)]
    controls += [f"{model_options[key]} {key}" for key in ("emotion", "tone") if model_options.get(key)]
    if model_options.get("pace") and model_options["pace"] != "normal":
        controls.append(model_options["pace"].replace("_", " ") + " pace")
    text = f"({', '.join(controls)}){job['input']}" if controls else job["input"]
    payload = {
        "text": text, "cfg_value": model_options["cfg_value"], "inference_timesteps": model_options["inference_timesteps"],
        "min_len": model_options["min_len"], "max_len": model_options["max_len"], "normalize": model_options["normalize"],
        "denoise": model_options["denoise"], "retry_badcase": model_options["retry_badcase"],
        "retry_badcase_max_times": model_options["retry_badcase_max_times"],
        "retry_badcase_ratio_threshold": model_options["retry_badcase_ratio_threshold"],
    }
    if reference_path:
        payload["reference_wav_path"] = reference_path
        if model_options["cloning_mode"] == "continuation":
            payload["prompt_wav_path"] = reference_path
            payload["prompt_text"] = job["reference_text"]
    return payload
