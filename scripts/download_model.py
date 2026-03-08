"""Download the BitNet model from HuggingFace (no token required)."""

import sys
from pathlib import Path

def main():
    target_dir = Path(__file__).resolve().parent.parent / "models" / "BitNet-b1.58-2B-4T"
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading model to {target_dir} ...")
    print("This may take a while depending on your connection speed.")

    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id="microsoft/BitNet-b1.58-2B-4T-gguf",
        local_dir=str(target_dir),
        repo_type="model",
        ignore_patterns=["*.md", "*.txt"],
    )

    print(f"Model downloaded to {target_dir}")

    # Verify expected file exists
    expected_files = list(target_dir.glob("*.gguf"))
    if expected_files:
        print(f"Found GGUF files: {[f.name for f in expected_files]}")
    else:
        print("WARNING: No .gguf files found after download. The model may need re-downloading.")
        sys.exit(1)


if __name__ == "__main__":
    main()
