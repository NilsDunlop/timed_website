# TIMED local test cluster

This POSIX-only harness presents an AsyncSSH server and the small subset of
Slurm used by the connector. It runs on macOS and Linux, and on Windows inside
WSL2, where `scripts/run_e2e.py` starts it itself; the product connector
remains cross-platform.

Start it from the repository root:

```bash
uv run python dev/test_cluster/server.py
```

By default the server listens on `127.0.0.1:2222` and keeps its state in
`dev/test_cluster/.state/`; `--host`, `--port`, and `--state-dir` change them.
Its keyboard-interactive defaults are `dev-password` and `123456`; override them
with `TEST_CLUSTER_PASSWORD` and `TEST_CLUSTER_VERIFICATION_CODE`. Set
`TEST_CLUSTER_PENDING_SECONDS` to keep submitted jobs pending long enough to
exercise queue UI.

Copy `config/profiles.dev.yaml` outside the repository, replace its
`/absolute/path/to/repo` placeholders with this checkout's absolute path, and
start TIMED with `uv run python scripts/dev.py --profiles <that copy>`. The harness writes only below its state directory, by
default the gitignored `dev/test_cluster/.state/`. SFTP is chrooted to its test
home, direct TCP forwarding is limited to loopback destinations, and commands
run through `bash -lc` with the stand-in Slurm commands first on `PATH`.
