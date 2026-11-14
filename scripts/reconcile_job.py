"""Read job status; optionally close a confirmed-stopped GPU task (never requeues).

No secrets, prompts, or audio are printed. Requires TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL and
TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY. Use ONLY after independently verifying GPU termination.
"""
import argparse
import os

from tts_inference.persistence import JobStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id", help="Existing opaque 32-char job ID")
    parser.add_argument("owner", help="Client owner ID")
    parser.add_argument("--confirm-stopped", action="store_true", help="Verified in Modal logs that the GPU is no longer executing")
    args = parser.parse_args()
    store = JobStore(os.environ["TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL"], os.environ["TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY"])
    state = store.inspect(args.job_id, args.owner)
    if state is None:
        raise SystemExit("No such job in that client scope")
    print("Job:", state["job_id"], "status:", state["status"], "model:", state["model"])
    if args.confirm_stopped:
        store.close_after_manual_gpu_confirmation(args.job_id, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")
        print("Manually closed after operator confirmation; never requeued")


if __name__ == "__main__":
    main()
