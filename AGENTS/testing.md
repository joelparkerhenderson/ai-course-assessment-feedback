# Testing

- Run: `python3 -m unittest discover -s tests -t .`. Tests are offline and run in well under a second.
- Tests use `FakeClient` in `tests/test_marker.py`, which returns queued JSON
  answers and records the seed of each call. Use it for any grader behaviour,
  and never require a running Ollama in unit tests.
- Every bug fix gets a regression test. Every new flag gets a test that
  triggers it and a test showing it isn't triggered on a normal essay.
- Seed behaviour is part of the contract (`seed + i`, retries at
  `seed + 1000 * attempt`), so tests assert on it.
- **End-to-end check** (needs Ollama and a gpt-oss model, and takes minutes):
  ```sh
  python3 -m marker check
  python3 -m marker grade assets/example-papers/ --out results/example --force
  ```
  Then confirm that the JSON has all criteria, sensible justifications and
  verified evidence. Run it twice without `--force` and confirm the second run
  skips the essay (fingerprint unchanged).
