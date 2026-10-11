# Security and privacy

Student essays are **personal data** (UK GDPR). The reason this project uses a
local open-weights model is so that essays never leave the machine.

## Invariants

1. **Loopback only.** `OllamaClient` refuses non-loopback hosts unless
   `allow_remote=True` (`--allow-remote`). This applies to `--host` and to the
   `OLLAMA_HOST_URL` environment variable alike. Don't weaken `is_loopback`
   or change the default.
   Don't add telemetry, update checks or any other outbound network call.
2. **No third-party runtime dependencies.** This keeps the supply-chain surface small.
3. **Essays are untrusted input:**
   - *Prompt injection.* The essay is wrapped in `<essay>` tags, `</essay>` inside
     it is neutralised, the system prompt declares it to be data, and
     `detect_injection` flags it for humans independently of the model. Keep all four layers.
   - *File parsing.* `.docx` parsing reads only `word/document.xml`, refuses
     an uncompressed size over 50 MiB (zip-bomb guard), and uses `xml.etree`
     (no external entity resolution). Don't add parsers that run macros or
     fetch remote resources.
   - *Truncation.* Ollama silently truncates a prompt longer than `num_ctx`,
     which can drop the instructions and security rules while the model
     still answers. `grader.check_context` and the token checks in
     `sample_once` refuse that case; keep both.
4. **Logs.** `audit.jsonl` has hashes and marks, not essay text. Result JSON
   does contain quotes and the model's raw output (and its reasoning trace
   with `--keep-reasoning` or `--show thinking`). Treat `results/` as
   personal data, and keep it out of git.
5. **Downloads** (example papers) go to a scratch directory first and are
   never executed. See `skills/prepare-essays`.
6. **Human in the loop.** The tool produces provisional marks and flags. Never
   build a feature that publishes marks to students without human confirmation.
